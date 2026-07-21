from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from research_agent.application.library_service import LibraryService
from research_agent.infrastructure.sqlite_repository import LibraryPaperNotFound


@dataclass
class LibraryToolset:
    """Library tools plus the exact sources exposed during one Agent run."""

    tools: list[BaseTool]
    source_registry: dict[str, dict[str, Any]] = field(default_factory=dict)


def build_library_tools(
    service: LibraryService,
    *,
    allowed_library_ids: list[str] | None = None,
) -> LibraryToolset:
    """Build scoped, read-only tools for research and library question answering."""
    from langchain_core.tools import tool

    allowed = set(allowed_library_ids) if allowed_library_ids is not None else None
    source_registry: dict[str, dict[str, Any]] = {}

    def scoped_ids(requested: list[str] | None) -> list[str] | None:
        clean = list(dict.fromkeys(str(item).strip() for item in requested or [] if str(item).strip()))
        if allowed is None:
            return clean or None
        if not clean:
            return sorted(allowed)
        return [item for item in clean if item in allowed]

    def remember(sources: list[dict[str, Any]]) -> None:
        for source in sources:
            source_id = str(source.get("source_id") or "").strip()
            if source_id:
                source_registry[source_id] = dict(source)

    @tool
    def search_library(query: str, limit: int = 8) -> str:
        """Search local papers, notes, indexed PDFs, and historical project evidence."""
        results = service.search_library(
            query,
            library_ids=scoped_ids(None),
            limit=max(1, min(int(limit), 20)),
        )
        for result in results:
            remember(result.get("sources") or [])
        return json.dumps(results, ensure_ascii=False)

    @tool
    def retrieve_library_passages(
        query: str,
        library_ids: list[str] | None = None,
        limit: int = 12,
    ) -> str:
        """Retrieve page-aware passages and reusable evidence from the local library."""
        selected_ids = scoped_ids(library_ids)
        if allowed is not None and not selected_ids:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "library_scope_empty",
                    "instruction": "Use only papers inside the allowed library scope.",
                },
                ensure_ascii=False,
            )
        sources = service.retrieve_library_sources(
            query,
            library_ids=selected_ids,
            limit=max(1, min(int(limit), 30)),
        )
        remember(sources)
        return json.dumps(sources, ensure_ascii=False)

    @tool
    def get_library_paper_context(library_id: str) -> str:
        """Read one paper's metadata, notes, analyses, project evidence, and index status."""
        clean_id = library_id.strip()
        if allowed is not None and clean_id not in allowed:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "paper_outside_library_scope",
                    "library_id": clean_id,
                },
                ensure_ascii=False,
            )
        try:
            detail = service.get_paper(clean_id)
        except LibraryPaperNotFound:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "library_paper_not_found",
                    "library_id": clean_id,
                },
                ensure_ascii=False,
            )
        paper = detail["paper"]
        sources = service.retrieve_library_sources(
            paper.get("title") or paper.get("abstract") or clean_id,
            library_ids=[clean_id],
            limit=12,
        )
        remember(sources)
        payload = {
            "paper": paper,
            "notes": detail.get("notes", [])[:20],
            "analyses": detail.get("analyses", [])[:5],
            "evidence": detail.get("evidence", [])[:20],
            "attachments": detail.get("attachments", []),
            "indexed_chunk_count": detail.get("indexed_chunk_count", 0),
            "sources": sources,
        }
        return json.dumps(payload, ensure_ascii=False)

    return LibraryToolset(
        tools=[search_library, retrieve_library_passages, get_library_paper_context],
        source_registry=source_registry,
    )
