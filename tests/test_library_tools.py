import json

from research_agent.application.library_service import LibraryService
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.tools.library_tools import build_library_tools


def _tools_by_name(toolset):
    return {tool.name: tool for tool in toolset.tools}


def test_library_tools_search_retrieve_and_register_exact_sources(tmp_path) -> None:
    service = LibraryService(SqliteResearchRepository(tmp_path / "test.db"))
    paper = service.upsert_paper(
        {
            "title": "Evidence-aware routing",
            "abstract": "The routing method keeps every claim traceable.",
        }
    )
    toolset = build_library_tools(service)
    tools = _tools_by_name(toolset)

    search_results = json.loads(tools["search_library"].invoke({"query": "traceable routing"}))
    passages = json.loads(
        tools["retrieve_library_passages"].invoke(
            {"query": "traceable routing", "library_ids": [paper.library_id]}
        )
    )

    assert search_results[0]["library_id"] == paper.library_id
    assert passages[0]["source_id"] in toolset.source_registry
    assert toolset.source_registry[passages[0]["source_id"]]["text"].startswith(
        "The routing method"
    )


def test_library_tools_enforce_selected_scope(tmp_path) -> None:
    service = LibraryService(SqliteResearchRepository(tmp_path / "test.db"))
    allowed = service.upsert_paper(
        {"title": "Allowed paper", "abstract": "Allowed evidence."}
    )
    blocked = service.upsert_paper(
        {"title": "Blocked paper", "abstract": "Blocked evidence."}
    )
    tools = _tools_by_name(
        build_library_tools(service, allowed_library_ids=[allowed.library_id])
    )

    results = json.loads(tools["search_library"].invoke({"query": "evidence"}))
    blocked_context = json.loads(
        tools["get_library_paper_context"].invoke({"library_id": blocked.library_id})
    )

    assert [item["library_id"] for item in results] == [allowed.library_id]
    assert blocked_context["error_code"] == "paper_outside_library_scope"
