import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from research_agent.agents.workflow_guard import ResearchWorkflowGuardMiddleware
from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.application.research_service import ResearchService
from research_agent.domain.models import ResearchStage
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def _request(name: str, thread_id: str = "thread-a", **args):
    return SimpleNamespace(
        tool_call={
            "name": name,
            "args": args,
            "id": f"call-{name}",
            "type": "tool_call",
        },
        runtime=SimpleNamespace(config={"configurable": {"thread_id": thread_id}}),
    )


def _error_code(message: ToolMessage) -> str:
    return json.loads(message.content)["error_code"]


def test_workflow_guard_requires_project_and_blocks_general_purpose() -> None:
    guard = ResearchWorkflowGuardMiddleware()
    handler_calls = 0

    def handler(_request):
        nonlocal handler_calls
        handler_calls += 1
        return "ok"

    before_project = guard.wrap_tool_call(
        _request("task", subagent_type="literature-scout", description="search"),
        handler,
    )
    assert isinstance(before_project, ToolMessage)
    assert _error_code(before_project) == "project_must_be_created_first"
    assert handler_calls == 0

    save_before_project = guard.wrap_tool_call(
        _request("save_paper_card", project_id="invented", payload_json="{}"),
        handler,
    )
    assert isinstance(save_before_project, ToolMessage)
    assert _error_code(save_before_project) == "project_must_be_created_first"
    assert handler_calls == 0

    assert guard.wrap_tool_call(_request("create_research_project"), handler) == "ok"
    blocked_general = guard.wrap_tool_call(
        _request("task", subagent_type="general-purpose", description="search"),
        handler,
    )
    assert isinstance(blocked_general, ToolMessage)
    assert _error_code(blocked_general) == "subagent_not_allowed"
    assert handler_calls == 1


def test_workflow_guard_allows_only_one_scout_per_thread() -> None:
    guard = ResearchWorkflowGuardMiddleware()
    guard.wrap_tool_call(_request("create_research_project"), lambda _request: "created")
    scout_request = _request(
        "task",
        subagent_type="literature-scout",
        description="search",
    )

    assert guard.wrap_tool_call(scout_request, lambda _request: "report") == "report"
    second = guard.wrap_tool_call(scout_request, lambda _request: "unexpected")

    assert isinstance(second, ToolMessage)
    assert _error_code(second) == "literature_scout_limit_reached"


def test_workflow_guard_rejects_conflicting_paper_reader_evidence_policy() -> None:
    guard = ResearchWorkflowGuardMiddleware()
    guard.wrap_tool_call(_request("create_research_project"), lambda _request: "created")

    blocked = guard.wrap_tool_call(
        _request(
            "task",
            subagent_type="paper-reader",
            description=(
                "精读论文。PaperCard格式要求：evidence_id使用简单格式如E1、E2，"
                "不要包含paper_id前缀。"
            ),
        ),
        lambda _request: "unexpected",
    )

    assert isinstance(blocked, ToolMessage)
    assert _error_code(blocked) == "paper_reader_description_conflicts_with_evidence_id_policy"


def test_workflow_guard_rejects_conflicting_synthesizer_schema_description() -> None:
    guard = ResearchWorkflowGuardMiddleware()
    guard.wrap_tool_call(_request("create_research_project"), lambda _request: "created")

    blocked_schema = guard.wrap_tool_call(
        _request(
            "task",
            subagent_type="research-synthesizer",
            description=(
                "生成SynthesisReport。你必须输出JSON Schema："
                '{"consensus":[{"claim":"x","supporting_evidence":["P1:E1"]}]}'
            ),
        ),
        lambda _request: "unexpected",
    )
    blocked_tool = guard.wrap_tool_call(
        _request(
            "task",
            subagent_type="research-synthesizer",
            description="请通过 get_research_project 获取当前运行状态和相关产物。",
        ),
        lambda _request: "unexpected",
    )

    assert isinstance(blocked_schema, ToolMessage)
    assert _error_code(blocked_schema) == "synthesizer_description_defines_schema"
    assert isinstance(blocked_tool, ToolMessage)
    assert _error_code(blocked_tool) == "synthesizer_description_uses_unavailable_project_tool"


def test_workflow_guard_blocks_subagents_in_the_wrong_stage(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    state = ResearchRuntimeState()
    guard = ResearchWorkflowGuardMiddleware(service, state)
    project = service.create_project("topic", "question")
    state.register_project("thread-a", project.project_id)
    guard.wrap_tool_call(_request("create_research_project"), lambda _request: "created")

    blocked = guard.wrap_tool_call(
        _request("task", subagent_type="paper-reader", description="read"),
        lambda _request: "unexpected",
    )

    assert isinstance(blocked, ToolMessage)
    assert _error_code(blocked) == "subagent_stage_not_ready"


def test_workflow_guard_blocks_paper_reader_outside_screening_decision(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    state = ResearchRuntimeState()
    guard = ResearchWorkflowGuardMiddleware(service, state)
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [
                {
                    "paper_id": "https://openalex.org/W4409797280",
                    "title": "Included",
                    "source": "OpenAlex",
                },
                {
                    "paper_id": "https://openalex.org/W4409797281",
                    "title": "Excluded",
                    "source": "OpenAlex",
                },
            ],
        },
        ResearchStage.SEARCHED,
        actor="scout",
    )
    service.save_artifact_and_transition(
        project.project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {
                    "paper_id": "https://openalex.org/W4409797280",
                    "title": "Included",
                    "source": "OpenAlex",
                },
                {
                    "paper_id": "https://openalex.org/W4409797281",
                    "title": "Excluded",
                    "source": "OpenAlex",
                },
            ],
            "excluded_paper_ids": ["https://openalex.org/W4409797281"],
            "executed_queries": ["query"],
        },
        ResearchStage.SEARCH_REVIEW_PENDING,
        actor="human-search-review",
    )
    service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["https://openalex.org/W4409797280"],
            "excluded_paper_ids": ["https://openalex.org/W4409797281"],
            "reasons": ["ok"],
        },
        ResearchStage.SCREENED,
        actor="human-search-review",
    )
    state.register_project("thread-a", project.project_id)
    guard.bind_existing_project("thread-a")

    blocked = guard.wrap_tool_call(
        _request(
            "task",
            subagent_type="paper-reader",
            description="Read W4409797281",
        ),
        lambda _request: "unexpected",
    )
    allowed = guard.wrap_tool_call(
        _request(
            "task",
            subagent_type="paper-reader",
            description="paper_id: W4409797280",
        ),
        lambda _request: "ok",
    )

    assert isinstance(blocked, ToolMessage)
    assert _error_code(blocked) == "paper_reader_not_in_screening_decision"
    assert allowed == "ok"


def test_workflow_guard_reserves_pending_search_review_for_user_api(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    state = ResearchRuntimeState()
    guard = ResearchWorkflowGuardMiddleware(service, state)
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [
                {"paper_id": "P1", "title": "Paper", "source": "OpenAlex"}
            ],
            "selection_notes": [],
        },
        ResearchStage.SEARCHED,
        actor="scout",
    )
    service.save_artifact_and_transition(
        project.project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {"paper_id": "P1", "title": "Paper", "source": "OpenAlex"}
            ],
            "executed_queries": ["query"],
        },
        ResearchStage.SEARCH_REVIEW_PENDING,
        actor="human-search-review",
    )
    state.register_project("thread-a", project.project_id)
    guard.bind_existing_project("thread-a")

    blocked = guard.wrap_tool_call(
        _request(
            "save_screening_decision",
            project_id=project.project_id,
            included_paper_ids=["P1"],
            excluded_paper_ids=[],
            reasons=["relevant"],
        ),
        lambda _request: "unexpected",
    )

    assert isinstance(blocked, ToolMessage)
    assert _error_code(blocked) == "human_search_review_required"
