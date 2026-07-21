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

from research_agent.application.paper_ids import normalize_paper_id


def thread_id_from_config(config: RunnableConfig | dict[str, Any] | None) -> str:
    configurable = (config or {}).get("configurable", {})
    return str(configurable.get("thread_id") or "unscoped")


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
    "fact-checker": "FactCheckReport",
}

SUBAGENT_REQUIRED_KEYS = {
    "literature-scout": {"candidate_ids", "screening_decisions"},
    "paper-reader": {"paper_id", "title", "research_question", "findings"},
    "research-synthesizer": {"topic", "consensus", "conflicts", "method_comparison", "gaps"},
    "evidence-reviewer": {"verdict", "fatal_issues", "suggestions", "verified_evidence_ids"},
    "research-outliner": {"title", "narrative_arc", "sections"},
    "narrative-writer": {"section_id", "heading", "content", "cited_evidence"},
    "chief-editor": {"title", "abstract", "sections", "references"},
    "fact-checker": {"section_id", "verdict", "issues"},
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
    text = _task_text(inputs)
    patterns = (
        r"paper_id[^:\n]*:\s*[\"']([^\"']+)[\"']",
        r"paper_id[^:\n]*:\s*([^\s,;]+)",
    )
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
        return {
            "subagent_type": subagent_type,
            "expected_schema_tool": SUBAGENT_SCHEMA_TOOLS.get(subagent_type),
            "content": _diagnostic_value(field("content", None)),
            "tool_calls": _diagnostic_value(tool_calls),
            "invalid_tool_calls": _diagnostic_value(invalid_tool_calls),
            "additional_kwargs": _diagnostic_value(field("additional_kwargs", {})),
            "response_metadata": _diagnostic_value(response_metadata),
            "finish_reason": (
                response_metadata.get("finish_reason")
                if isinstance(response_metadata, dict)
                else None
            ),
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


def _paper_key(paper_id: str, doi: str) -> str:
    if match := re.search(r"\bW\d+\b", paper_id, flags=re.IGNORECASE):
        return match.group(0).upper()
    normalized_doi = doi.casefold().removeprefix("https://doi.org/").strip()
    if normalized_doi:
        return f"doi:{normalized_doi}"
    return paper_id.casefold().strip() or "unknown-paper"


def _candidate_identity(candidate: dict[str, Any]) -> str:
    return normalize_paper_id(candidate.get("paper_id") or candidate.get("doi") or "")


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for candidate in candidates:
        identity = _candidate_identity(candidate)
        if not identity:
            identity = f"title:{str(candidate.get('title', '')).casefold().strip()}"
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(candidate)
    return deduped


def _temporary_candidate_ids(candidate_ids: list[Any]) -> bool:
    if not candidate_ids:
        return False
    return all(
        re.fullmatch(r"P\d{1,4}", str(item).strip(), flags=re.IGNORECASE)
        for item in candidate_ids
    )


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
        self._search_sources: dict[str, set[str]] = {}
        self._raw_search_results: dict[str, list[dict]] = {}
        self._search_constraints: dict[str, dict[str, Any]] = {}
        self._prefer_library_search: dict[str, bool] = {}
        self._results: dict[tuple[str, str], RecordedSubagentResult] = {}
        self._rejections: dict[tuple[str, str], int] = {}
        self._paper_fetches: dict[tuple[str, str], set[str]] = {}

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
            self._search_sources[thread_id] = set()
            self._raw_search_results[thread_id] = []
            self._search_constraints.setdefault(thread_id, {})
            self._prefer_library_search.setdefault(thread_id, True)
            for key in [item for item in self._results if item[0] == thread_id]:
                del self._results[key]
            for key in [item for item in self._rejections if item[0] == thread_id]:
                del self._rejections[key]
            for key in [item for item in self._paper_fetches if item[0] == thread_id]:
                del self._paper_fetches[key]

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
        quality_venues_only: bool,
        prefer_library_search: bool = True,
    ) -> None:
        with self._lock:
            self._search_constraints[thread_id] = {
                "year_from": int(year_from),
                "year_to": int(year_to),
                "quality_venues_only": bool(quality_venues_only),
            }
            self._prefer_library_search[thread_id] = bool(prefer_library_search)

    def search_constraints(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._search_constraints.get(thread_id, {}))

    def prefer_library_search(self, thread_id: str) -> bool:
        with self._lock:
            return self._prefer_library_search.get(thread_id, True)

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

    def mark_search_source(self, thread_id: str, source: str) -> None:
        with self._lock:
            self._search_sources.setdefault(thread_id, set()).add(source)

    def has_search_source(self, thread_id: str, source: str) -> bool:
        with self._lock:
            return source in self._search_sources.get(thread_id, set())

    def store_search_results(self, thread_id: str, results_json: str) -> None:
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
                    "library_id": str(item.get("library_id", "")),
                    "venue": str(item.get("venue", "")),
                    "venue_type": item.get("venue_type"),
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

    def mark_consumed(self, thread_id: str, subagent_type: str) -> None:
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is not None:
                record.consumed = True
            self._rejections.pop((thread_id, subagent_type), None)

    def reject_result(self, thread_id: str, subagent_type: str) -> int:
        """Consume an invalid result so a corrected subagent run can replace it."""
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is not None:
                record.consumed = True
            key = (thread_id, subagent_type)
            count = self._rejections.get(key, 0) + 1
            self._rejections[key] = count
            return count

    def rejection_count(self, thread_id: str, subagent_type: str) -> int:
        with self._lock:
            return self._rejections.get((thread_id, subagent_type), 0)

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

    def reset_paper_fetch(self, thread_id: str, paper_id: str, doi: str = "") -> None:
        """Allow a fresh subagent attempt while the download tool reuses its disk cache."""
        with self._lock:
            self._paper_fetches.pop((thread_id, _paper_key(paper_id, doi)), None)


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
            unique_queries = []
            for value in args.get("queries", []):
                query = " ".join(str(value).split())
                if query and self.state.record_search(thread_id, query):
                    unique_queries.append(query)
            if unique_queries:
                args["queries"] = unique_queries
                return None
            query = " | ".join(str(value) for value in args.get("queries", []))
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
                thread_id, content
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
                payload["candidates"] = matched
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
                payload["candidates"] = selected
            elif raw_results:
                payload["candidates"] = raw_results
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
