import research_agent.agents.supervisor as supervisor_module
import research_agent.agents.registry as registry_module
from langchain.agents.middleware import ModelCallLimitMiddleware
from research_agent.agents.prompts import PI_PROMPT
from research_agent.agents.runtime_state import (
    ExecutedSearchTrackingMiddleware,
    PaperFetchGuardMiddleware,
)
from research_agent.agents.serial_tools import SerialToolExecutionMiddleware
from research_agent.agents.supervisor import AgentExecutionError, ResearchSupervisor
from research_agent.agents.workflow_guard import ResearchWorkflowGuardMiddleware
from research_agent.application.research_service import ResearchService
from research_agent.application.research_service import WorkflowPrerequisiteError
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.tools.project_tools import build_project_tools


def test_fallback_policy_only_accepts_availability_errors() -> None:
    assert ResearchSupervisor.should_fallback(TimeoutError("model timed out")) is True
    assert (
        ResearchSupervisor.should_fallback(
            WorkflowPrerequisiteError("SearchReport is required")
        )
        is False
    )
    assert ResearchSupervisor.should_fallback(ValueError("bad schema")) is False
    wrapped = AgentExecutionError(TimeoutError("model timed out"), "RP-test")
    assert ResearchSupervisor.should_fallback(wrapped) is True
    assert wrapped.project_id == "RP-test"


def test_supervisor_has_atomic_commit_tool_and_explicit_ordering_policy(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    tool_names = {tool.name for tool in build_project_tools(service)}

    assert "save_artifact_and_transition" in tool_names
    assert "save_paper_card" in tool_names
    assert "advance_project_stage" in tool_names
    assert "get_active_research_project" in tool_names
    assert "同一条 AI 消息最多调用一个工具" in PI_PROMPT
    assert "禁止手工复制JSON" in PI_PROMPT
    assert "每次只委派一篇论文给 paper-reader" in PI_PROMPT
    assert "fetch_paper_text" in PI_PROMPT
    assert "必须复制 create_research_project 返回的原始 project_id" in PI_PROMPT


def test_supervisor_hides_unsafe_generic_write_tools(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    agent_configs: list[dict] = []

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    def fake_create_scout_agent(**kwargs):
        agent_configs.append(kwargs)
        return object()

    monkeypatch.setattr(supervisor_module, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(registry_module, "create_agent", fake_create_scout_agent)
    settings = Settings(
        model="test-model",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
    )

    ResearchSupervisor(settings)
    exposed_names = {tool.name for tool in captured["tools"]}

    assert "commit_subagent_result" in exposed_names
    assert "save_screening_decision" in exposed_names
    assert "advance_project_stage" in exposed_names
    assert "save_artifact_and_transition" not in exposed_names
    assert "save_paper_card" not in exposed_names
    assert "save_project_artifact" not in exposed_names
    assert "transition_project_stage" not in exposed_names
    assert "search_openalex" not in exposed_names
    assert "search_crossref" not in exposed_names
    assert "verify_doi" not in exposed_names
    assert "fetch_paper_text" not in exposed_names
    assert "get_active_research_project" not in exposed_names
    assert len(captured["middleware"]) == 2
    assert isinstance(captured["middleware"][0], SerialToolExecutionMiddleware)
    assert isinstance(captured["middleware"][1], ResearchWorkflowGuardMiddleware)
    configured_subagents = captured["subagents"]
    assert len(configured_subagents) == 4
    assert all(set(agent) == {"name", "description", "runnable"} for agent in configured_subagents)
    assert len(agent_configs) == 4
    assert all(
        isinstance(config["middleware"][0], SerialToolExecutionMiddleware)
        for config in agent_configs
    )
    reader = next(config for config in agent_configs if config["name"] == "paper-reader")
    assert len(reader["middleware"]) == 4
    assert isinstance(reader["middleware"][0], SerialToolExecutionMiddleware)
    assert isinstance(reader["middleware"][1], ModelCallLimitMiddleware)
    assert reader["middleware"][1].run_limit == 4
    assert reader["middleware"][1].exit_behavior == "end"
    assert reader["middleware"][2].tool_name == "extract_pdf_text"
    assert reader["middleware"][2].run_limit == 1
    assert reader["middleware"][2].exit_behavior == "end"
    assert isinstance(reader["middleware"][3], PaperFetchGuardMiddleware)
    assert reader["middleware"][3].max_attempts_per_paper == 2
    assert [tool.name for tool in reader["tools"]] == [
        "fetch_paper_text",
        "extract_pdf_text",
    ]
    scout_captured = next(
        config for config in agent_configs if config["name"] == "literature-scout"
    )
    assert [tool.name for tool in scout_captured["tools"]] == [
        "search_openalex",
        "search_crossref",
    ]
    assert scout_captured["middleware"][1].tool_name == "search_openalex"
    assert scout_captured["middleware"][1].run_limit == 3
    assert scout_captured["middleware"][2].tool_name == "search_crossref"
    assert scout_captured["middleware"][2].run_limit == 1
    assert isinstance(scout_captured["middleware"][3], ExecutedSearchTrackingMiddleware)
    assert len(scout_captured["middleware"]) == 4
    assert isinstance(scout_captured["response_format"], dict)
    assert scout_captured["response_format"]["title"] == "SearchReport"
    synthesizer = next(
        config for config in agent_configs if config["name"] == "research-synthesizer"
    )
    assert [tool.name for tool in synthesizer["tools"]] == [
        "get_active_research_project"
    ]
    reviewer = next(
        config for config in agent_configs if config["name"] == "evidence-reviewer"
    )
    assert [tool.name for tool in reviewer["tools"]] == [
        "get_active_research_project"
    ]
    assert len(reviewer["middleware"]) == 3
    assert isinstance(reviewer["middleware"][0], SerialToolExecutionMiddleware)
    assert isinstance(reviewer["middleware"][1], ModelCallLimitMiddleware)
    assert reviewer["middleware"][1].run_limit == 3
    assert reviewer["middleware"][1].exit_behavior == "end"
    assert reviewer["middleware"][2].tool_name == "get_active_research_project"
    assert reviewer["middleware"][2].run_limit == 1
    assert reviewer["middleware"][2].exit_behavior == "end"
