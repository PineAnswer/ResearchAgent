from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.config import get_config

from research_agent.application.candidate_ranking import rank_candidates
from research_agent.application.paper_ids import normalize_paper_id


def thread_id_from_config(config: RunnableConfig | dict[str, Any] | None) -> str:
    configurable = (config or {}).get("configurable", {})
    return str(configurable.get("thread_id") or "unscoped")


def thread_id_from_runtime(runtime: Any) -> str:
    """Read RunnableConfig from tool runtimes or the active model-call context."""
    config = getattr(runtime, "config", None)
    if config is None:
        try:
            config = get_config()
        except RuntimeError:
            config = None
    return thread_id_from_config(config)


def _json_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("Subagent structured_response must be a JSON object")
    return deepcopy(value)


SUBAGENT_SCHEMA_TOOLS = {
    "literature-scout": "SearchReport",
    "paper-reader": "PaperCard",
    "research-synthesizer": "SynthesisReport",
    "evidence-reviewer": "ReviewResult",
    "research-outliner": "ReviewOutline",
    "narrative-writer": "SectionDraft",
    "chief-editor": "NarrativeReview",
}

SUBAGENT_REQUIRED_KEYS = {
    "literature-scout": {"candidate_ids", "screening_decisions"},
    "paper-reader": {"paper_id", "title", "research_question", "findings"},
    "research-synthesizer": {"topic", "consensus", "conflicts", "method_comparison", "gaps"},
    "evidence-reviewer": {"verdict", "fatal_issues", "suggestions", "verified_evidence_ids"},
    "research-outliner": {"title", "narrative_arc", "sections"},
    "narrative-writer": {"section_id", "heading", "content", "cited_evidence"},
    "chief-editor": {"title", "abstract", "sections", "references"},
}


def _task_text(inputs: Any) -> str:
    if not isinstance(inputs, dict):
        return ""
    messages = inputs.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _expected_paper_id(inputs: Any) -> str | None:
    patterns = (
        r"paper_id[^:\n]*:\s*[\"']([^\"']+)[\"']",
        r"paper_id[^:\n]*:\s*([^\s,;]+)",
    )
    if not isinstance(inputs, dict):
        return None
    for message in reversed(inputs.get("messages", [])):
        text = _message_content(message)
        if not isinstance(text, str):
            continue
        for pattern in patterns:
            if match := re.search(pattern, text, flags=re.IGNORECASE):
                return match.group(1).strip()
    return None


def _message_content(message: Any) -> Any:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content


def _diagnostic_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _diagnostic_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_diagnostic_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


_SENSITIVE_DIAGNOSTIC_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "cookie",
    "access_token",
    "refresh_token",
)


def _redact_diagnostic(value: Any) -> Any:
    value = _diagnostic_value(value)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(marker in key.casefold() for marker in _SENSITIVE_DIAGNOSTIC_KEYS)
                else _redact_diagnostic(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_diagnostic(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+",
            "Bearer [REDACTED]",
            value,
        )
    return value


def _structured_response_diagnostics(
    result: dict[str, Any], subagent_type: str
) -> dict[str, Any]:
    messages = result.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    for message in reversed(messages):
        message_type = getattr(message, "type", None)
        if message_type is None and isinstance(message, dict):
            message_type = message.get("type")
        if message_type != "ai":
            continue
        def field(name: str, default: Any) -> Any:
            value = getattr(message, name, None)
            if value is None and isinstance(message, dict):
                value = message.get(name)
            return default if value is None else value

        response_metadata = field("response_metadata", {})
        tool_calls = field("tool_calls", [])
        invalid_tool_calls = field("invalid_tool_calls", [])
        finish_reason = (
            response_metadata.get("finish_reason")
            if isinstance(response_metadata, dict)
            else None
        )
        if invalid_tool_calls:
            parse_status = "invalid_tool_calls"
        elif finish_reason == "tool_calls" and not tool_calls:
            parse_status = "tool_call_finish_without_parsed_calls"
        elif tool_calls:
            parse_status = "schema_tool_call_not_promoted"
        elif field("content", None):
            parse_status = "unparsed_content"
        else:
            parse_status = "empty_model_response"
        return {
            "subagent_type": subagent_type,
            "expected_schema_tool": SUBAGENT_SCHEMA_TOOLS.get(subagent_type),
            "content": _redact_diagnostic(field("content", None)),
            "tool_calls": _redact_diagnostic(tool_calls),
            "invalid_tool_calls": _redact_diagnostic(invalid_tool_calls),
            "additional_kwargs": _redact_diagnostic(field("additional_kwargs", {})),
            "response_metadata": _redact_diagnostic(response_metadata),
            "usage_metadata": _redact_diagnostic(field("usage_metadata", {})),
            "message_id": field("id", None),
            "finish_reason": finish_reason,
            "parse_status": parse_status,
            "diagnostic_hint": "See the matching llm.response entry in messages.jsonl.",
            "raw_provider_response_captured": False,
        }
    return {
        "subagent_type": subagent_type,
        "expected_schema_tool": SUBAGENT_SCHEMA_TOOLS.get(subagent_type),
        "message": "No AIMessage was available in the subagent result.",
        "raw_provider_response_captured": False,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _tool_call_args(call: Any) -> tuple[str, Any]:
    if isinstance(call, dict):
        return str(call.get("name", "")), call.get("args")
    name = str(getattr(call, "name", ""))
    args = getattr(call, "args", None)
    return name, args


def _looks_like_expected_payload(payload: Any, subagent_type: str) -> bool:
    if not isinstance(payload, dict):
        return False
    required = SUBAGENT_REQUIRED_KEYS.get(subagent_type)
    if required is None:
        return True
    if {"project", "artifacts", "events"} & set(payload):
        return False
    return required.issubset(payload)


def _fallback_structured_payload(result: dict[str, Any], subagent_type: str) -> dict[str, Any] | None:
    expected_tool = SUBAGENT_SCHEMA_TOOLS.get(subagent_type)
    messages = result.get("messages", [])
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is None and isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                name, args = _tool_call_args(call)
                if expected_tool is not None and name == expected_tool:
                    parsed = deepcopy(args) if isinstance(args, dict) else (
                        _extract_json_object(args) if isinstance(args, str) else None
                    )
                    if _looks_like_expected_payload(parsed, subagent_type):
                        return parsed
        content = _message_content(message)
        if isinstance(content, str) and content.strip():
            parsed = _extract_json_object(content)
            if _looks_like_expected_payload(parsed, subagent_type):
                return parsed
    return None


@dataclass
class RecordedSubagentResult:
    payload: dict[str, Any]
    consumed: bool = False


def _query_key(query: str) -> str:
    """Collapse case, word order, plural suffixes, and repeated tokens."""
    tokens = re.findall(r"[\w]+", query.casefold(), flags=re.UNICODE)
    normalized = {
        token[:-1] if token.endswith("s") and len(token) > 3 else token
        for token in tokens
    }
    return " ".join(sorted(normalized))


def _paper_identities(*values: str) -> set[str]:
    identities: set[str] = set()
    for value in values:
        normalized = normalize_paper_id(value)
        if normalized:
            identities.add(normalized.casefold())
        arxiv_match = re.search(
            r"(?:arxiv[:./])?(\d{4}\.\d{4,5}(?:v\d+)?)",
            value,
            flags=re.IGNORECASE,
        )
        if arxiv_match:
            identities.add(f"arxiv:{arxiv_match.group(1).casefold()}")
    return identities


def _paper_key(paper_id: str, doi: str) -> str:
    if match := re.search(r"\bW\d+\b", paper_id, flags=re.IGNORECASE):
        return match.group(0).upper()
    arxiv_identities = sorted(
        item for item in _paper_identities(paper_id, doi) if item.startswith("arxiv:")
    )
    if arxiv_identities:
        return arxiv_identities[0]
    normalized_doi = doi.casefold().removeprefix("https://doi.org/").strip()
    if normalized_doi:
        return f"doi:{normalized_doi}"
    return paper_id.casefold().strip() or "unknown-paper"


def _candidate_identity(candidate: dict[str, Any]) -> str:
    return normalize_paper_id(candidate.get("paper_id") or candidate.get("doi") or "")


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    merged_by_identity: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        identity = _candidate_identity(candidate)
        if not identity:
            identity = f"title:{str(candidate.get('title', '')).casefold().strip()}"
        if identity not in merged_by_identity:
            merged_by_identity[identity] = deepcopy(candidate)
            continue
        merged = merged_by_identity[identity]
        for field, value in candidate.items():
            if field in {
                "sources",
                "matched_queries",
                "authors",
                "fields_of_study",
                "impact_explanation",
                "authority_explanation",
                "ranking_explanation",
            }:
                combined = list(merged.get(field) or [])
                for item in value or []:
                    if item not in combined:
                        combined.append(item)
                merged[field] = combined
            elif field in {
                "citation_counts",
                "citation_percentiles",
                "influential_citation_counts",
                "recent_citation_velocities",
                "momentum_percentiles",
            }:
                combined = dict(merged.get(field) or {})
                combined.update(value or {})
                merged[field] = combined
            elif field == "abstract":
                if len(str(value or "")) > len(str(merged.get(field) or "")):
                    merged[field] = value
            elif field == "is_retracted":
                merged[field] = bool(merged.get(field) or value)
            elif not merged.get(field) and value is not None:
                merged[field] = value
        sources = list(merged.get("sources") or [])
        if sources:
            merged["source"] = " + ".join(sources)
    return list(merged_by_identity.values())


def _temporary_candidate_ids(candidate_ids: list[Any]) -> bool:
    if not candidate_ids:
        return False
    return all(
        re.fullmatch(r"P\d{1,4}", str(item).strip(), flags=re.IGNORECASE)
        for item in candidate_ids
    )


def _normalized_candidate_venue_type(value: Any) -> str | None:
    normalized = str(value or "").casefold().strip()
    if normalized in {"journal", "journal-article", "periodical"}:
        return "journal"
    if normalized in {
        "conference",
        "conference-paper",
        "conference-proceedings",
        "proceedings",
        "proceedings-article",
    }:
        return "conference"
    return None


def _remap_screening_dict(
    value: Any,
    id_map: dict[str, str],
) -> Any:
    if not isinstance(value, dict):
        return value
    remapped: dict[str, Any] = {}
    for key, item in value.items():
        mapped = id_map.get(str(key).strip(), str(key))
        remapped[mapped] = item
    return remapped


class ResearchRuntimeState:
    """Thread-scoped project, search, and structured subagent state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._project_ids: dict[str, str] = {}
        self._user_ids: dict[str, str] = {}
        self._conversation_ids: dict[str, str] = {}
        self._search_terms: dict[str, list[str]] = {}
        self._search_keys: dict[str, set[str]] = {}
        self._search_query_rounds: dict[str, list[list[str]]] = {}
        self._max_search_rounds: dict[str, int] = {}
        self._search_sources: dict[str, set[str]] = {}
        self._search_result_counts: dict[str, dict[str, int]] = {}
        self._raw_search_results: dict[str, list[dict]] = {}
        self._search_constraints: dict[str, dict[str, Any]] = {}
        self._prefer_library_search: dict[str, bool] = {}
        self._results: dict[tuple[str, str], RecordedSubagentResult] = {}
        self._rejections: dict[tuple[str, str, str], int] = {}
        self._paper_fetches: dict[tuple[str, str], set[str]] = {}
        self._paper_fetch_attempts: set[tuple[str, str]] = set()

    def register_project(
        self,
        thread_id: str,
        project_id: str,
        *,
        user_id: str = "",
        conversation_id: str = "",
    ) -> None:
        with self._lock:
            self._project_ids[thread_id] = project_id
            if user_id:
                self._user_ids[thread_id] = user_id
            if conversation_id:
                self._conversation_ids[thread_id] = conversation_id
            self._search_terms[thread_id] = []
            self._search_keys[thread_id] = set()
            self._search_query_rounds[thread_id] = []
            self._search_sources[thread_id] = set()
            self._search_result_counts[thread_id] = {}
            self._raw_search_results[thread_id] = []
            self._search_constraints.setdefault(thread_id, {})
            self._prefer_library_search.setdefault(thread_id, False)
            for key in [item for item in self._results if item[0] == thread_id]:
                del self._results[key]
            for key in [item for item in self._rejections if item[0] == thread_id]:
                del self._rejections[key]
            for key in [item for item in self._paper_fetches if item[0] == thread_id]:
                del self._paper_fetches[key]
            self._paper_fetch_attempts = {
                item for item in self._paper_fetch_attempts if item[0] != thread_id
            }

    def project_id(self, thread_id: str) -> str | None:
        with self._lock:
            return self._project_ids.get(thread_id)

    def user_id(self, thread_id: str) -> str | None:
        with self._lock:
            return self._user_ids.get(thread_id)

    def conversation_id(self, thread_id: str) -> str | None:
        with self._lock:
            return self._conversation_ids.get(thread_id)

    def set_search_constraints(
        self,
        thread_id: str,
        *,
        year_from: int,
        year_to: int,
        max_search_rounds: int,
        prefer_library_search: bool = False,
    ) -> None:
        with self._lock:
            self._search_constraints[thread_id] = {
                "year_from": int(year_from),
                "year_to": int(year_to),
            }
            self._max_search_rounds[thread_id] = max(1, int(max_search_rounds))
            self._prefer_library_search[thread_id] = bool(prefer_library_search)

    def search_constraints(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._search_constraints.get(thread_id, {}))

    def prefer_library_search(self, thread_id: str) -> bool:
        with self._lock:
            return self._prefer_library_search.get(thread_id, False)

    def record_search(self, thread_id: str, query: str) -> bool:
        query = query.strip()
        if not query:
            return False
        with self._lock:
            key = _query_key(query)
            keys = self._search_keys.setdefault(thread_id, set())
            if key in keys:
                return False
            keys.add(key)
            terms = self._search_terms.setdefault(thread_id, [])
            terms.append(query)
            return True

    def search_terms(self, thread_id: str) -> list[str]:
        with self._lock:
            return list(self._search_terms.get(thread_id, []))

    def reserve_search_round(
        self, thread_id: str, queries: list[str]
    ) -> tuple[list[str], str | None]:
        """Atomically reserve one executed multi-source query-design round."""
        normalized = [" ".join(str(query).split()) for query in queries]
        with self._lock:
            known = self._search_keys.setdefault(thread_id, set())
            unique: list[str] = []
            unique_keys: set[str] = set()
            for query in normalized:
                key = _query_key(query)
                if query and key and key not in known and key not in unique_keys:
                    unique.append(query)
                    unique_keys.add(key)
            if not unique:
                return [], "duplicate_search_query"
            rounds = self._search_query_rounds.setdefault(thread_id, [])
            limit = self._max_search_rounds.get(thread_id, 1)
            if len(rounds) >= limit:
                return [], "search_round_limit_reached"
            known.update(unique_keys)
            self._search_terms.setdefault(thread_id, []).extend(unique)
            rounds.append(unique)
            return unique, None

    def search_query_rounds(self, thread_id: str) -> list[list[str]]:
        with self._lock:
            return [list(round_queries) for round_queries in self._search_query_rounds.get(thread_id, [])]

    def max_search_rounds(self, thread_id: str) -> int:
        with self._lock:
            return self._max_search_rounds.get(thread_id, 1)

    def mark_search_source(self, thread_id: str, source: str) -> None:
        with self._lock:
            self._search_sources.setdefault(thread_id, set()).add(source)

    def has_search_source(self, thread_id: str, source: str) -> bool:
        with self._lock:
            return source in self._search_sources.get(thread_id, set())

    def search_result_count(self, thread_id: str, source: str) -> int:
        with self._lock:
            return self._search_result_counts.get(thread_id, {}).get(source, 0)

    def store_search_results(
        self, thread_id: str, results_json: str, *, source: str = ""
    ) -> None:
        """Persist raw search tool output so the LLM does not need to
        reproduce full paper metadata in its structured_response."""
        import json as _json

        try:
            parsed = _json.loads(results_json)
        except (_json.JSONDecodeError, TypeError):
            return
        if isinstance(parsed, dict):
            parsed = parsed.get("candidates", [])
        if not isinstance(parsed, list):
            return
        candidates = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            source_names = []
            raw_sources = item.get("sources")
            if isinstance(raw_sources, list):
                for value in raw_sources:
                    if isinstance(value, str):
                        source = value.strip()
                    elif isinstance(value, dict):
                        source = str(
                            value.get("provider")
                            or value.get("database")
                            or value.get("source_name")
                            or ""
                        ).strip()
                    else:
                        source = ""
                    if source and source not in source_names:
                        source_names.append(source)
            primary_source = str(item.get("source", "")).strip()
            if not source_names and primary_source:
                source_names.append(primary_source)
            matched_queries = [
                str(value).strip()
                for value in (
                    item.get("matched_queries")
                    if isinstance(item.get("matched_queries"), list)
                    else []
                )
                if isinstance(value, str) and value.strip()
            ]
            candidates.append(
                {
                    "paper_id": str(item.get("paper_id", "")),
                    "title": str(item.get("title", "")),
                    "authors": (
                        list(item.get("authors", []))
                        if isinstance(item.get("authors"), list)
                        else []
                    ),
                    "year": item.get("year"),
                    "abstract": str(item.get("abstract", "")),
                    "doi": item.get("doi"),
                    "url": item.get("url"),
                    "source": primary_source,
                    "sources": source_names,
                    "matched_queries": matched_queries,
                    "relevance_score": item.get("relevance_score"),
                    "fields_of_study": list(item.get("fields_of_study") or []),
                    "publication_type": str(item.get("publication_type") or ""),
                    "citation_counts": dict(item.get("citation_counts") or {}),
                    "citation_percentiles": dict(
                        item.get("citation_percentiles") or {}
                    ),
                    "fwci": item.get("fwci"),
                    "influential_citation_counts": dict(
                        item.get("influential_citation_counts") or {}
                    ),
                    "influential_citation_percentile": item.get(
                        "influential_citation_percentile"
                    ),
                    "recent_citation_velocities": dict(
                        item.get("recent_citation_velocities") or {}
                    ),
                    "momentum_percentiles": dict(
                        item.get("momentum_percentiles") or {}
                    ),
                    "impact_score": item.get("impact_score"),
                    "impact_confidence": item.get("impact_confidence"),
                    "authority_score": item.get("authority_score"),
                    "diversity_score": item.get("diversity_score"),
                    "composite_score": item.get("composite_score"),
                    "is_retracted": bool(item.get("is_retracted")),
                    "impact_explanation": list(item.get("impact_explanation") or []),
                    "authority_explanation": list(
                        item.get("authority_explanation") or []
                    ),
                    "ranking_explanation": list(item.get("ranking_explanation") or []),
                    "library_id": str(item.get("library_id", "")),
                    "venue": str(item.get("venue", "")),
                    "venue_type": _normalized_candidate_venue_type(
                        item.get("venue_type")
                    ),
                    "venue_acronym": str(item.get("venue_acronym", "")),
                    "ccf_rank": item.get("ccf_rank"),
                    "ccf_category": item.get("ccf_category"),
                    "ccf_year": item.get("ccf_year"),
                    "sci_quartile": item.get("sci_quartile"),
                    "index_name": item.get("index_name"),
                    "impact_factor": item.get("impact_factor"),
                    "impact_factor_year": item.get("impact_factor_year"),
                    "nature_portfolio": bool(item.get("nature_portfolio")),
                    "venue_rating_explanation": str(
                        item.get("venue_rating_explanation", "")
                    ),
                    "venue_rating_source_url": item.get("venue_rating_source_url"),
                    "venue_rating_source_label": item.get("venue_rating_source_label"),
                    "venue_match_confidence": item.get("venue_match_confidence"),
                }
            )
        with self._lock:
            stored = self._raw_search_results.setdefault(thread_id, [])
            stored.extend(candidates)
            if source:
                counts = self._search_result_counts.setdefault(thread_id, {})
                counts[source] = counts.get(source, 0) + len(candidates)

    def get_search_results(self, thread_id: str) -> list[dict]:
        with self._lock:
            return list(self._raw_search_results.get(thread_id, []))

    def record_result(self, thread_id: str, subagent_type: str, payload: Any) -> None:
        with self._lock:
            self._results[(thread_id, subagent_type)] = RecordedSubagentResult(
                payload=_json_payload(payload)
            )

    def pending_result(self, thread_id: str, subagent_type: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is None or record.consumed:
                return None
            return deepcopy(record.payload)

    @staticmethod
    def _rejection_key(
        thread_id: str, subagent_type: str, scope: str | None = None
    ) -> tuple[str, str, str]:
        normalized_scope = ""
        if scope:
            normalized_scope = (
                normalize_paper_id(scope) if subagent_type == "paper-reader" else scope
            )
        return thread_id, subagent_type, normalized_scope

    def mark_consumed(
        self, thread_id: str, subagent_type: str, scope: str | None = None
    ) -> None:
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is not None:
                record.consumed = True
            self._rejections.pop(
                self._rejection_key(thread_id, subagent_type, scope), None
            )

    def reject_result(
        self, thread_id: str, subagent_type: str, scope: str | None = None
    ) -> int:
        """Consume an invalid result so a corrected subagent run can replace it."""
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is not None:
                record.consumed = True
            key = self._rejection_key(thread_id, subagent_type, scope)
            count = self._rejections.get(key, 0) + 1
            self._rejections[key] = count
            return count

    def rejection_count(
        self, thread_id: str, subagent_type: str, scope: str | None = None
    ) -> int:
        with self._lock:
            return self._rejections.get(
                self._rejection_key(thread_id, subagent_type, scope), 0
            )

    def reserve_paper_fetch(
        self,
        thread_id: str,
        paper_id: str,
        doi: str,
        url: str,
        max_attempts: int,
    ) -> str | None:
        """Reserve a distinct fetch request or return a structured error code."""
        with self._lock:
            key = (thread_id, _paper_key(paper_id, doi))
            for paper_identity in _paper_identities(paper_id, doi):
                self._paper_fetch_attempts.add((thread_id, paper_identity))
            signatures = self._paper_fetches.setdefault(key, set())
            signature = "|".join(
                [
                    paper_id.casefold().strip(),
                    doi.casefold().removeprefix("https://doi.org/").strip(),
                    url.casefold().strip(),
                ]
            )
            if signature in signatures:
                return "duplicate_paper_fetch"
            if len(signatures) >= max(1, max_attempts):
                return "paper_fetch_limit_reached"
            signatures.add(signature)
            return None

    def has_paper_fetch_attempt(self, thread_id: str, paper_id: str) -> bool:
        """Return whether the current paper-reader already attempted full-text access."""
        identities = _paper_identities(paper_id)
        if not identities:
            return False
        with self._lock:
            return any(
                (thread_id, identity) in self._paper_fetch_attempts
                for identity in identities
            )

    def reset_paper_fetch(self, thread_id: str, paper_id: str, doi: str = "") -> None:
        """Allow a fresh subagent attempt while the download tool reuses its disk cache."""
        with self._lock:
            identities = _paper_identities(paper_id, doi)
            for key, signatures in list(self._paper_fetches.items()):
                if key[0] != thread_id:
                    continue
                signature_identities: set[str] = set()
                for signature in signatures:
                    request_paper_id, request_doi, _ = signature.split("|", maxsplit=2)
                    signature_identities.update(
                        _paper_identities(request_paper_id, request_doi)
                    )
                if identities & signature_identities or key == (
                    thread_id,
                    _paper_key(paper_id, doi),
                ):
                    del self._paper_fetches[key]
            self._paper_fetch_attempts = {
                item
                for item in self._paper_fetch_attempts
                if item[0] != thread_id or item[1] not in identities
            }


class ExecutedSearchTrackingMiddleware(AgentMiddleware):
    """Record executed searches and block duplicate query intent."""

    def __init__(self, state: ResearchRuntimeState) -> None:
        self.state = state

    def _reserve(self, request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name", ""))
        search_tools = {
            "search_library",
            "search_openalex",
            "search_crossref",
            "search_multi_source",
        }
        if name not in search_tools:
            return None
        thread_id = thread_id_from_config(request.runtime.config)
        prefer_library = self.state.prefer_library_search(thread_id)
        if name == "search_library" and not prefer_library:
            return ToolMessage(
                content=json.dumps(
                    {
                        "ok": False,
                        "error_code": "local_library_search_disabled",
                        "instruction": "用户未启用文献库优先检索，请直接执行多源联网检索。",
                    },
                    ensure_ascii=False,
                ),
                tool_call_id=str(request.tool_call.get("id", "library-search-disabled")),
                name=name,
                status="error",
            )
        if prefer_library and name != "search_library" and not self.state.has_search_source(
            thread_id,
            "search_library",
        ):
            return ToolMessage(
                content=json.dumps(
                    {
                        "ok": False,
                        "error_code": "local_library_search_required",
                        "instruction": (
                            "必须先调用 search_library；只有确认本地覆盖缺口后才能联网检索。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                tool_call_id=str(request.tool_call.get("id", "library-first-search")),
                name=name,
                status="error",
            )
        if (
            name == "search_library"
            and self.state.has_search_source(thread_id, "search_library")
            and self.state.search_result_count(thread_id, "search_library") == 0
            and not self.state.has_search_source(thread_id, "search_multi_source")
        ):
            return ToolMessage(
                content=json.dumps(
                    {
                        "ok": False,
                        "error_code": "local_library_empty_use_external",
                        "instruction": (
                            "本地文献库检索已执行且结果为空；下一次必须调用"
                            "search_multi_source检索外部学术来源，禁止继续调用search_library。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                tool_call_id=str(request.tool_call.get("id", "external-search-required")),
                name=name,
                status="error",
            )
        args = request.tool_call.get("args", {})
        if name in {
            "search_openalex",
            "search_crossref",
            "search_multi_source",
        } and isinstance(args, dict):
            args.update(
                self.state.search_constraints(
                    thread_id_from_config(request.runtime.config)
                )
            )
        if name == "search_multi_source" and isinstance(args, dict):
            unique_queries, error_code = self.state.reserve_search_round(
                thread_id, list(args.get("queries", []))
            )
            if unique_queries:
                args["queries"] = unique_queries
                return None
            query = " | ".join(str(value) for value in args.get("queries", []))
            if error_code == "search_round_limit_reached":
                limit = self.state.max_search_rounds(thread_id)
                return ToolMessage(
                    content=json.dumps(
                        {
                            "ok": False,
                            "error_code": error_code,
                            "query": query,
                            "instruction": (
                                f"已完成用户允许的 {limit} 轮检索词设计。"
                                "请使用已有结果完成筛选并提交 SearchReport。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=str(request.tool_call.get("id", "search-round-limit")),
                    name=name,
                    status="error",
                )
        else:
            query = str(args.get("query", ""))
        if self.state.record_search(thread_id, query):
            return None
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error_code": "duplicate_search_query",
                    "query": query,
                    "instruction": "该查询与已执行查询重复；使用已有结果或换用真正互补的检索角度。",
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(request.tool_call.get("id", "duplicate-search")),
            name=name,
            status="error",
        )

    def _capture_result(self, request: ToolCallRequest, result: Any) -> None:
        name = str(request.tool_call.get("name", ""))
        if name not in {
            "search_library",
            "search_openalex",
            "search_crossref",
            "search_multi_source",
        }:
            return
        content = getattr(result, "content", result)
        if isinstance(content, str):
            thread_id = thread_id_from_config(request.runtime.config)
            self.state.mark_search_source(thread_id, name)
            self.state.store_search_results(
                thread_id, content, source=name
            )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        blocked = self._reserve(request)
        if blocked is not None:
            return blocked
        result = handler(request)
        self._capture_result(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        blocked = self._reserve(request)
        if blocked is not None:
            return blocked
        result = await handler(request)
        self._capture_result(request, result)
        return result


class PaperFetchGuardMiddleware(AgentMiddleware):
    """Prevent one paper from causing unbounded or duplicate network fetches."""

    def __init__(self, state: ResearchRuntimeState, max_attempts_per_paper: int = 2):
        self.state = state
        self.max_attempts_per_paper = max(1, max_attempts_per_paper)

    def _before(self, request: ToolCallRequest) -> ToolMessage | None:
        if str(request.tool_call.get("name", "")) != "fetch_paper_text":
            return None
        args = request.tool_call.get("args", {})
        error_code = self.state.reserve_paper_fetch(
            thread_id_from_config(request.runtime.config),
            str(args.get("paper_id", "")),
            str(args.get("doi", "")),
            str(args.get("url", "")),
            self.max_attempts_per_paper,
        )
        if error_code is None:
            return None
        instruction = (
            "相同参数已经尝试过；禁止重复调用，立即使用此前结果或摘要生成PaperCard。"
            if error_code == "duplicate_paper_fetch"
            else "该论文已达到全文获取上限；停止猜测URL，使用摘要生成PaperCard。"
        )
        return ToolMessage(
            content=json.dumps(
                {"ok": False, "error_code": error_code, "instruction": instruction},
                ensure_ascii=False,
            ),
            tool_call_id=str(request.tool_call.get("id", "paper-fetch-guard")),
            name="fetch_paper_text",
            status="error",
        )

    def _without_fetch_tool(self, request: Any) -> Any:
        paper_id = _expected_paper_id(request.state)
        thread_id = thread_id_from_runtime(request.runtime)
        if not paper_id or not self.state.has_paper_fetch_attempt(thread_id, paper_id):
            return request

        def tool_name(tool: Any) -> str:
            if isinstance(tool, dict):
                function = tool.get("function")
                if isinstance(function, dict):
                    return str(function.get("name", ""))
                return str(tool.get("name", ""))
            return str(getattr(tool, "name", ""))

        tools = [tool for tool in request.tools if tool_name(tool) != "fetch_paper_text"]
        system_content = ""
        if request.system_message is not None:
            system_content = str(request.system_message.content)
        system_content += (
            "\n\n系统已完成本篇论文唯一一次全文获取尝试，fetch_paper_text现已关闭。"
            "必须立即使用已经返回的全部带页码文本；若获取失败则使用任务摘要，"
            "并直接输出符合Schema的PaperCard。禁止请求任何其他全文路径。"
        )
        return request.override(
            tools=tools,
            system_message=SystemMessage(content=system_content.strip()),
        )

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._without_fetch_tool(request))

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        return await handler(self._without_fetch_tool(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        blocked = self._before(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        blocked = self._before(request)
        return blocked if blocked is not None else await handler(request)


def recording_runnable(
    agent: Any,
    subagent_type: str,
    state: ResearchRuntimeState,
    *,
    memory_provider: Callable[[str, str, str], dict[str, Any]] | None = None,
):
    """Wrap a structured agent and retain its exact JSON response for atomic commit."""

    def inject_memory(inputs: Any, config: RunnableConfig) -> Any:
        if memory_provider is None or not isinstance(inputs, dict):
            return inputs
        thread_id = thread_id_from_config(config)
        project_id = state.project_id(thread_id)
        if not project_id:
            return inputs
        try:
            memory = memory_provider(
                project_id,
                subagent_type,
                _task_text(inputs),
            )
        except Exception:  # noqa: BLE001 - memory must never block the agent task
            return inputs
        messages = inputs.get("messages")
        if not isinstance(messages, list):
            return inputs
        memory_message = SystemMessage(
            content=(
                "以下是系统从已提交项目产物生成的共享记忆账本。它用于让你承接前序 "
                "Agent 的工作；artifact_id、paper_id、evidence_id 和当前阶段任务均须"
                "保持原样。已提交产物是事实来源，不得虚构缺失内容，也不要重复已完成"
                "章节。请先阅读账本，再执行消息列表末尾的当前任务。\n"
                + json.dumps(memory, ensure_ascii=False, default=str)
            )
        )
        return {**inputs, "messages": [memory_message, *messages]}

    def record(result: dict[str, Any], inputs: Any, config: RunnableConfig) -> dict[str, Any]:
        thread_id = thread_id_from_config(config)
        structured_response = result.get("structured_response")
        if structured_response is None:
            fallback = _fallback_structured_payload(result, subagent_type)
            if fallback is None:
                payload = {
                    "_subagent_error": "structured_response_missing",
                    "_instruction": (
                        "模型没有返回可解析的结构化对象；请提交该失败结果，"
                        "由系统释放后重新委派一次。"
                    ),
                    "_diagnostics": _structured_response_diagnostics(
                        result, subagent_type
                    ),
                }
                if expected_id := _expected_paper_id(inputs):
                    payload["_paper_id"] = expected_id
            else:
                payload = _json_payload(fallback)
            result = {**result, "structured_response": payload}
        else:
            payload = _json_payload(structured_response)
        if subagent_type == "literature-scout":
            search_terms = state.search_terms(thread_id)
            payload["search_terms"] = search_terms
            supplied_log = payload.get("search_iteration_log") or []
            actual_log = []
            for index, queries in enumerate(state.search_query_rounds(thread_id)):
                supplied = (
                    supplied_log[index]
                    if index < len(supplied_log) and isinstance(supplied_log[index], dict)
                    else {}
                )
                actual_log.append(
                    {
                        **supplied,
                        "round": index + 1,
                        "queries": queries,
                        "query": " | ".join(queries),
                    }
                )
            payload["search_iteration_log"] = actual_log
            if not str(payload.get("query", "")).strip():
                payload["query"] = " | ".join(search_terms) if search_terms else "literature search"
            raw_results = _dedupe_candidates(state.get_search_results(thread_id))
            submitted_ids = list(payload.get("candidate_ids", []))
            candidate_ids = {normalize_paper_id(item) for item in submitted_ids}
            matched = [
                candidate
                for candidate in raw_results
                if _candidate_identity(candidate) in candidate_ids
            ]
            if candidate_ids and matched:
                decisions = {
                    normalize_paper_id(key): value
                    for key, value in (payload.get("screening_decisions") or {}).items()
                }
                reasons = {
                    normalize_paper_id(key): value
                    for key, value in (payload.get("screening_reasons") or {}).items()
                }
                for candidate in matched:
                    identity = _candidate_identity(candidate)
                    candidate["agent_decision"] = decisions.get(identity, "uncertain")
                    agent_reason = reasons.get(identity)
                    if agent_reason:
                        candidate["agent_screening_reason"] = agent_reason
                payload["candidates"] = rank_candidates(
                    matched,
                    search_terms,
                )
            elif raw_results and _temporary_candidate_ids(submitted_ids):
                selected = raw_results[: len(submitted_ids)]
                id_map = {
                    str(old_id).strip(): _candidate_identity(candidate)
                    for old_id, candidate in zip(submitted_ids, selected, strict=False)
                }
                payload["candidate_ids"] = [
                    id_map.get(str(item).strip(), str(item)) for item in submitted_ids
                ]
                payload["screening_decisions"] = _remap_screening_dict(
                    payload.get("screening_decisions"), id_map
                )
                payload["screening_reasons"] = _remap_screening_dict(
                    payload.get("screening_reasons"), id_map
                )
                payload["candidates"] = rank_candidates(selected, search_terms)
            elif raw_results:
                payload["candidates"] = rank_candidates(raw_results, search_terms)
        elif subagent_type == "paper-reader":
            if expected_id := _expected_paper_id(inputs):
                payload["paper_id"] = expected_id
        state.record_result(thread_id, subagent_type, payload)
        return result

    def invoke(inputs: Any, config: RunnableConfig) -> dict[str, Any]:
        prepared = inject_memory(inputs, config)
        return record(agent.invoke(prepared, config=config), inputs, config)

    async def ainvoke(inputs: Any, config: RunnableConfig) -> dict[str, Any]:
        prepared = inject_memory(inputs, config)
        result = await agent.ainvoke(prepared, config=config)
        return record(result, inputs, config)

    return RunnableLambda(invoke, afunc=ainvoke, name=f"record-{subagent_type}")
