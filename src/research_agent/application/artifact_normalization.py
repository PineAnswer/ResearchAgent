from __future__ import annotations

from typing import Any


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


def normalize_artifact_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Map known LLM field aliases to the canonical domain schema.

    The adapter is intentionally narrow: it only repairs field names observed at
    the Agent boundary. Pydantic still validates the resulting payload strictly.
    """
    normalized = dict(payload)
    if kind != "SearchReport":
        return normalized

    if "query" not in normalized and "research_question" in normalized:
        normalized["query"] = normalized.pop("research_question")

    if "candidates" not in normalized and "papers" in normalized:
        normalized["candidates"] = normalized.pop("papers")

    candidates = normalized.get("candidates")
    if isinstance(candidates, list):
        normalized["candidates"] = [_normalize_candidate(item) for item in candidates]

    if "selection_notes" not in normalized:
        summary = normalized.pop("summary", None)
        normalized["selection_notes"] = [str(summary)] if summary else []

    return normalized
