import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from research_agent.agents.workflow_guard import ResearchWorkflowGuardMiddleware
from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.application.research_service import ResearchService
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
