from __future__ import annotations

from typing import Any

from research_agent.application.paper_ids import normalize_paper_id


def _normalize_evidence_id(paper_id: str, evidence_id: Any) -> str:
    normalized = str(evidence_id).strip()
    if not normalized or normalized.startswith(f"{paper_id}:") or normalized.startswith(f"{paper_id}-"):
        return normalized
    return f"{paper_id}:{normalized}"


def _normalize_paper_card(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    paper_id = str(normalized.get("paper_id", "")).strip()
    findings = normalized.get("findings")
    if not paper_id or not isinstance(findings, list):
        return normalized

    normalized_findings = []
    for finding in findings:
        if not isinstance(finding, dict):
            normalized_findings.append(finding)
            continue
        item = dict(finding)
        item["evidence_id"] = _normalize_evidence_id(paper_id, item.get("evidence_id", ""))
        finding_paper_id = str(item.get("paper_id", "")).strip()
        if not finding_paper_id or finding_paper_id.startswith(f"{paper_id}:"):
            item["paper_id"] = paper_id
        normalized_findings.append(item)
    normalized["findings"] = normalized_findings
    return normalized


def _normalize_candidate(candidate: Any) -> Any:
    if not isinstance(candidate, dict):
        return candidate
    normalized = dict(candidate)
    title = str(normalized.get("title", "")).strip()
    doi = normalized.get("doi")
    normalized.setdefault("paper_id", doi or normalized.get("url") or f"title:{title}")
    normalized.setdefault("authors", [])
    normalized.setdefault("abstract", "")
    normalized.setdefault("source", "literature-scout")
    return normalized


def _normalize_screening_decision(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in ("included_paper_ids", "excluded_paper_ids"):
        values = normalized.get(field)
        if isinstance(values, list):
            normalized[field] = [normalize_paper_id(item) for item in values]
    return normalized


def normalize_artifact_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Map known LLM field aliases to the canonical domain schema.

    The adapter is intentionally narrow: it only repairs field names observed at
    the Agent boundary. Pydantic still validates the resulting payload strictly.
    """
    normalized = dict(payload)
    if kind == "PaperCard":
        return _normalize_paper_card(normalized)
    if kind == "ScreeningDecision":
        return _normalize_screening_decision(normalized)

    if kind not in {"SearchReport", "SupplementalSearchReport"}:
        return normalized

    if "query" not in normalized and "research_question" in normalized:
        normalized["query"] = normalized.pop("research_question")
    if not str(normalized.get("query", "")).strip():
        search_terms = normalized.get("search_terms")
        if isinstance(search_terms, list) and search_terms:
            normalized["query"] = " | ".join(str(item) for item in search_terms if str(item).strip())

    # Alias: the LLM may still call them "papers" instead of "candidates"
    if "candidates" not in normalized and "papers" in normalized:
        normalized["candidates"] = normalized.pop("papers")

    candidates = normalized.get("candidates")
    if isinstance(candidates, list):
        normalized["candidates"] = [_normalize_candidate(item) for item in candidates]

    # Alias: LLM may use "screening" instead of "screening_decisions"
    if "screening_decisions" not in normalized and "screening" in normalized:
        normalized["screening_decisions"] = normalized.pop("screening")

    # Alias: LLM may use "exclusion_reasons" for screening_reasons
    if "screening_reasons" not in normalized and "exclusion_reasons" in normalized:
        normalized["screening_reasons"] = normalized.pop("exclusion_reasons")

    # Alias: "search_log" for "search_iteration_log"
    if "search_iteration_log" not in normalized and "search_log" in normalized:
        normalized["search_iteration_log"] = normalized.pop("search_log")

    # Handle old 'summary' alias for selection_notes (must be before setdefault)
    if "selection_notes" not in normalized:
        summary = normalized.pop("summary", None)
        if summary:
            normalized["selection_notes"] = [str(summary)]

    # Default empty collections for new fields
    for field in (
        "candidate_ids",
        "screening_decisions",
        "screening_reasons",
        "coverage_gaps",
        "search_iteration_log",
        "selection_notes",
    ):
        if field == "screening_decisions" or field == "screening_reasons":
            normalized.setdefault(field, {})
        else:
            normalized.setdefault(field, [])

    return normalized
