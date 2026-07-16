import json
from types import SimpleNamespace

from research_agent.application.research_service import ResearchService
from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.domain.models import ResearchStage
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.tools.project_tools import build_project_tools


def _runtime(thread_id: str):
    return SimpleNamespace(config={"configurable": {"thread_id": thread_id}})


def _tools_by_name(service: ResearchService):
    return {tool.name: tool for tool in build_project_tools(service)}


def _enter_search_review(service, project_id: str, paper_ids: list[str]) -> None:
    service.save_artifact(
        project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {"paper_id": paper_id, "title": paper_id, "source": "test"}
                for paper_id in paper_ids
            ],
            "executed_queries": ["query"],
        },
    )
    service.transition(
        project_id,
        ResearchStage.SEARCH_REVIEW_PENDING,
        actor="human-search-review",
    )


def test_active_project_tool_is_scoped_by_thread_and_takes_no_model_id(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    create = tools["create_research_project"]
    get_active = tools["get_active_research_project"]
    thread_a = _runtime("thread-a")
    thread_b = _runtime("thread-b")

    project_a = json.loads(create.func("topic-a", "question-a", runtime=thread_a))
    project_b = json.loads(create.func("topic-b", "question-b", runtime=thread_b))

    snapshot_a = json.loads(get_active.func(runtime=thread_a))
    snapshot_b = json.loads(get_active.func(runtime=thread_b))
    assert snapshot_a["project"]["project_id"] == project_a["project_id"]
    assert snapshot_b["project"]["project_id"] == project_b["project_id"]
    assert get_active.args == {}


def test_project_lookup_errors_are_recoverable_tool_results(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)

    missing_explicit = json.loads(tools["get_research_project"].func("invented-id"))
    missing_active = json.loads(
        tools["get_active_research_project"].func(runtime=_runtime("unknown-thread"))
    )

    assert missing_explicit["ok"] is False
    assert missing_explicit["error_code"] == "project_not_found"
    assert missing_active["ok"] is False
    assert missing_active["error_code"] == "active_project_unavailable"


def test_save_paper_card_returns_recoverable_error_before_screening(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    runtime = _runtime("thread-a")
    project = json.loads(
        tools["create_research_project"].func("topic", "question", runtime=runtime)
    )

    result = json.loads(
        tools["save_paper_card"].func(
            project["project_id"],
            json.dumps({"paper_id": "P1", "title": "metadata"}),
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "paper_card_stage_not_ready"
    assert service.get_snapshot(project["project_id"])["artifacts"] == []


def test_incomplete_paper_metadata_is_rejected_at_screened_stage(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    project = service.create_project("topic", "question")
    _, searched = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [],
            "selection_notes": [],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    _enter_search_review(service, searched.project_id, ["P1"])
    _, screened = service.save_artifact_and_transition(
        searched.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["P1"],
            "excluded_paper_ids": [],
            "reasons": ["relevant"],
        },
        ResearchStage.SCREENED,
        actor="pi",
    )

    result = json.loads(
        tools["save_paper_card"].func(
            screened.project_id,
            json.dumps({"paper_id": "P1", "title": "metadata"}),
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "invalid_paper_card"
    assert result["required_fields"] == [
        "paper_id",
        "title",
        "research_question",
        "methods",
        "datasets",
        "findings",
        "limitations",
    ]
    assert len(service.get_snapshot(project.project_id)["artifacts"]) == 3


def test_atomic_commit_returns_recoverable_error_for_malformed_json(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    project = service.create_project("topic", "question")

    result = json.loads(
        tools["save_artifact_and_transition"].func(
            project.project_id,
            "SearchReport",
            '{"query": "quoted "text"", "search_terms": []}',
            "SEARCHED",
            "literature-scout",
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "invalid_artifact_json"
    assert result["line"] == 1
    assert service.get_snapshot(project.project_id)["artifacts"] == []
    assert service.get_project(project.project_id).stage is ResearchStage.CREATED


def test_atomic_commit_returns_recoverable_error_for_stage_skip(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    project = service.create_project("topic", "question")

    result = json.loads(
        tools["save_artifact_and_transition"].func(
            project.project_id,
            "SearchReport",
            json.dumps(
                {
                    "query": "query",
                    "search_terms": ["query"],
                    "candidates": [],
                    "selection_notes": [],
                }
            ),
            "COMPLETED",
            "synthesis-assistant",
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "artifact_commit_rejected"
    assert service.get_snapshot(project.project_id)["artifacts"] == []
    assert service.get_project(project.project_id).stage is ResearchStage.CREATED


def test_empty_search_can_finish_inconclusive(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    project = service.create_project("topic", "question")
    _, searched = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [],
            "selection_notes": ["no usable candidates"],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    result = json.loads(
        tools["finish_inconclusive"].func(
            searched.project_id,
            "No usable papers were found.",
            ["query"],
            ["search budget exhausted"],
            "Broaden the research question.",
        )
    )

    assert result["mode"] == "inconclusive"
    assert result["project"]["stage"] == "INCONCLUSIVE"
    assert result["artifact"]["kind"] == "InsufficientEvidence"


def test_advance_stage_returns_recoverable_error_without_paper_cards(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tools = _tools_by_name(service)
    project = service.create_project("topic", "question")
    _, searched = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [],
            "selection_notes": [],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    _enter_search_review(service, searched.project_id, [])
    _, screened = service.save_artifact_and_transition(
        searched.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": [],
            "excluded_paper_ids": [],
            "reasons": ["no candidates"],
        },
        ResearchStage.SCREENED,
        actor="pi",
    )

    result = json.loads(
        tools["advance_project_stage"].func(
            screened.project_id,
            "EXTRACTED",
            "paper-reader",
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "stage_transition_rejected"
    assert service.get_project(project.project_id).stage is ResearchStage.SCREENED


def test_commit_subagent_result_preserves_exact_structured_payload(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    state = ResearchRuntimeState()
    tools = {
        tool.name: tool for tool in build_project_tools(service, runtime_state=state)
    }
    runtime = _runtime("thread-a")
    project = json.loads(
        tools["create_research_project"].func("topic", "question", runtime=runtime)
    )
    payload = {
        "query": "question",
        "search_terms": ["executed query"],
        "candidates": [],
        "selection_notes": ["exact child output"],
    }
    state.record_result("thread-a", "literature-scout", payload)

    result = json.loads(
        tools["commit_subagent_result"].func(
            project["project_id"],
            "literature-scout",
            runtime=runtime,
        )
    )

    # Pydantic fills defaults for new lightweight-search fields
    assert result["artifact"]["payload"]["query"] == payload["query"]
    assert result["artifact"]["payload"]["search_terms"] == payload["search_terms"]
    assert result["artifact"]["payload"]["candidates"] == payload["candidates"]
    assert result["artifact"]["payload"]["selection_notes"] == payload["selection_notes"]
    assert result["project"]["stage"] == "SEARCHED"
    assert state.pending_result("thread-a", "literature-scout") is None


def test_structured_results_remain_exact_through_review(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    state = ResearchRuntimeState()
    tools = {
        tool.name: tool for tool in build_project_tools(service, runtime_state=state)
    }
    runtime = _runtime("thread-a")
    project = json.loads(
        tools["create_research_project"].func("topic", "question", runtime=runtime)
    )
    project_id = project["project_id"]
    state.record_result(
        "thread-a",
        "literature-scout",
        {
            "query": "q",
            "search_terms": ["q"],
            "candidates": [
                {"paper_id": "P1", "title": "Paper", "source": "OpenAlex"}
            ],
            "selection_notes": ["relevant"],
        },
    )
    tools["commit_subagent_result"].func(
        project_id, "literature-scout", runtime=runtime
    )
    _enter_search_review(service, project_id, ["P1"])
    tools["save_screening_decision"].func(
        project_id, ["P1"], [], ["P1 is relevant"]
    )
    state.record_result(
        "thread-a",
        "paper-reader",
        {
            "paper_id": "P1",
            "title": "Paper",
            "research_question": "question",
            "methods": ["experiment"],
            "datasets": [],
            "findings": [
                {
                    "evidence_id": "P1:E1",
                    "paper_id": "P1",
                    "claim": "finding",
                    "quote": "Evidence text.",
                    "page": 1,
                }
            ],
            "limitations": [],
        },
    )
    tools["commit_subagent_result"].func(project_id, "paper-reader", runtime=runtime)
    tools["advance_project_stage"].func(project_id, "EXTRACTED", "paper-reader")
    active = json.loads(tools["get_active_research_project"].func(runtime=runtime))
    assert active["valid_evidence_ids"] == ["P1:E1"]
    assert active["evidence_catalog"][0]["claim"] == "finding"
    state.record_result(
        "thread-a",
        "research-synthesizer",
        {
            "topic": "topic",
            "consensus": [
                {"statement": "finding", "evidence_ids": ["P1:E1"]}
            ],
            "conflicts": [],
            "method_comparison": [],
            "gaps": [],
        },
    )
    tools["commit_subagent_result"].func(
        project_id, "research-synthesizer", runtime=runtime
    )
    tools["advance_project_stage"].func(
        project_id, "REVIEW_PENDING", "research-supervisor"
    )
    review_payload = {
        "verdict": "REVISE",
        "fatal_issues": ["needs another source"],
        "suggestions": ["add evidence"],
        "verified_evidence_ids": ["P1:E1"],
    }
    state.record_result("thread-a", "evidence-reviewer", review_payload)

    result = json.loads(
        tools["commit_subagent_result"].func(
            project_id, "evidence-reviewer", runtime=runtime
        )
    )

    assert result["artifact"]["payload"] == review_payload
    assert result["project"]["current_review"] == review_payload
    assert result["project"]["stage"] == "REVIEWED"


def test_invalid_synthesis_is_discarded_and_can_be_regenerated(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    state = ResearchRuntimeState()
    tools = {
        tool.name: tool for tool in build_project_tools(service, runtime_state=state)
    }
    runtime = _runtime("thread-a")
    project = json.loads(
        tools["create_research_project"].func("topic", "question", runtime=runtime)
    )
    project_id = project["project_id"]
    state.record_result(
        "thread-a",
        "literature-scout",
        {
            "query": "q",
            "search_terms": ["q"],
            "candidates": [{"paper_id": "P1", "title": "Paper"}],
            "selection_notes": [],
        },
    )
    tools["commit_subagent_result"].func(
        project_id, "literature-scout", runtime=runtime
    )
    _enter_search_review(service, project_id, ["P1"])
    tools["save_screening_decision"].func(project_id, ["P1"], [], ["relevant"])
    state.record_result(
        "thread-a",
        "paper-reader",
        {
            "paper_id": "P1",
            "title": "Paper",
            "research_question": "question",
            "methods": [],
            "datasets": [],
            "findings": [
                {
                    "evidence_id": "P1:E1",
                    "paper_id": "P1",
                    "claim": "finding",
                    "quote": "Evidence text.",
                    "page": 1,
                }
            ],
            "limitations": [],
        },
    )
    tools["commit_subagent_result"].func(project_id, "paper-reader", runtime=runtime)
    tools["advance_project_stage"].func(project_id, "EXTRACTED", "paper-reader")
    state.record_result(
        "thread-a",
        "research-synthesizer",
        {
            "topic": "topic",
            "consensus": [
                {"statement": "unsupported", "evidence_ids": ["P1:limitations"]}
            ],
            "conflicts": [],
            "method_comparison": [],
            "gaps": [],
        },
    )

    result = json.loads(
        tools["commit_subagent_result"].func(
            project_id, "research-synthesizer", runtime=runtime
        )
    )

    assert result["ok"] is False
    assert result["retry_allowed"] is True
    assert result["rejection_count"] == 1
    assert state.pending_result("thread-a", "research-synthesizer") is None
    assert service.get_project(project_id).stage is ResearchStage.EXTRACTED
