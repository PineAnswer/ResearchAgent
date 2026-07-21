import research_agent.agents.supervisor as supervisor_module
import research_agent.agents.registry as registry_module
import pytest
from langchain.agents.middleware import ModelCallLimitMiddleware
from research_agent.agents.prompts import PI_PROMPT, inject_skill
from research_agent.agents.registry import build_subagent_registry
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
    assert "REVISION_PENDING" in PI_PROMPT
    assert "research-outliner、narrative-writer、chief-editor、fact-checker" in PI_PROMPT


def test_narrative_continuation_prompt_skips_completed_work() -> None:
    prompt = ResearchSupervisor.build_narrative_continue_prompt(
        "RP-test",
        {
            "current_stage": "OUTLINED",
            "saved_section_draft_ids": ["sec-1"],
            "fact_checked_section_ids": [],
        },
    )

    assert "禁止创建新项目、重新检索" in prompt
    assert "OUTLINED只补写尚未保存的SectionDraft" in prompt
    assert '"saved_section_draft_ids": [\n    "sec-1"\n  ]' in prompt


def test_supervisor_uses_configured_graph_recursion_limit(tmp_path) -> None:
    supervisor = object.__new__(ResearchSupervisor)
    supervisor.settings = Settings(
        model="test-model",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
        graph_recursion_limit=640,
    )

    config = supervisor.build_config("thread-a")

    assert config["configurable"]["thread_id"] == "thread-a"
    assert config["recursion_limit"] == 640


def test_skill_injection_rejects_empty_content_and_missing_subagent_skills() -> None:
    with pytest.raises(ValueError, match="Skill content is empty: test-skill"):
        inject_skill("base", "test-skill", "  ")

    with pytest.raises(ValueError, match="Missing subagent Skills"):
        build_subagent_registry({}, {}, model="test-model")


def test_supervisor_loads_aws_credentials_csv(tmp_path) -> None:
    csv_path = tmp_path / "aws.csv"
    csv_path.write_text(
        "Access key ID,Secret access key\nAKIAtest1234567890,secret-test-key\n",
        encoding="utf-8",
    )

    credentials = ResearchSupervisor._load_aws_credentials_from_csv(csv_path)

    assert credentials == {
        "aws_access_key_id": "AKIAtest1234567890",
        "aws_secret_access_key": "secret-test-key",
    }


@pytest.mark.parametrize(
    ("configured_model", "expected_model"),
    [
        ("gpt-5.6", "gpt-5.6"),
        ("openai:gpt-5.6", "gpt-5.6"),
    ],
)
def test_custom_base_url_accepts_raw_or_provider_prefixed_model(
    tmp_path, monkeypatch, configured_model: str, expected_model: str
) -> None:
    captured: dict = {}

    def fake_chat_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(supervisor_module, "ObservableChatOpenAI", fake_chat_model)
    supervisor = object.__new__(ResearchSupervisor)
    supervisor.settings = Settings(
        model=configured_model,
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
        base_url="https://relay.example/v1",
    )

    supervisor._build_model()

    assert captured == {
        "model": expected_model,
        "api_key": "test-key",
        "base_url": "https://relay.example/v1",
    }


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
    assert "finalize_narrative_revision" in exposed_names
    assert "save_artifact_and_transition" not in exposed_names
    assert "save_paper_card" not in exposed_names
    assert "save_project_artifact" not in exposed_names
    assert "transition_project_stage" not in exposed_names
    assert "search_openalex" not in exposed_names
    assert "search_crossref" not in exposed_names
    assert "search_library" not in exposed_names
    assert "retrieve_library_passages" not in exposed_names
    assert "verify_doi" not in exposed_names
    assert "fetch_paper_text" not in exposed_names
    assert "get_active_research_project" not in exposed_names
    assert len(captured["middleware"]) == 2
    assert isinstance(captured["middleware"][0], SerialToolExecutionMiddleware)
    assert isinstance(captured["middleware"][1], ResearchWorkflowGuardMiddleware)
    assert "skills" not in captured
    assert '<skill name="research-protocol">' in captured["system_prompt"]
    assert "禁止为了继续流程而跳过前置产物" in captured["system_prompt"]
    assert "每一节都有对应 FactCheckReport" in captured["system_prompt"]
    configured_subagents = captured["subagents"]
    assert len(configured_subagents) == 8
    assert all(set(agent) == {"name", "description", "runnable"} for agent in configured_subagents)
    assert len(agent_configs) == 8
    assert all(
        isinstance(config["middleware"][0], SerialToolExecutionMiddleware)
        for config in agent_configs
    )
    reader = next(config for config in agent_configs if config["name"] == "paper-reader")
    assert '<skill name="paper-reading">' in reader["system_prompt"]
    assert "摘要证据不能冒充全文实验细节" in reader["system_prompt"]
    assert len(reader["middleware"]) == 5
    assert isinstance(reader["middleware"][0], SerialToolExecutionMiddleware)
    assert isinstance(reader["middleware"][1], ModelCallLimitMiddleware)
    assert reader["middleware"][1].run_limit == 4
    assert reader["middleware"][1].exit_behavior == "end"
    assert reader["middleware"][2].tool_name == "retrieve_library_passages"
    assert reader["middleware"][2].run_limit == 1
    assert reader["middleware"][2].exit_behavior == "continue"
    assert reader["middleware"][3].tool_name == "extract_pdf_text"
    assert reader["middleware"][3].run_limit == 1
    assert reader["middleware"][3].exit_behavior == "continue"
    assert isinstance(reader["middleware"][4], PaperFetchGuardMiddleware)
    assert reader["middleware"][4].max_attempts_per_paper == 2
    assert [tool.name for tool in reader["tools"]] == [
        "retrieve_library_passages",
        "fetch_paper_text",
        "extract_pdf_text",
    ]
    scout_captured = next(
        config for config in agent_configs if config["name"] == "literature-scout"
    )
    assert '<skill name="literature-search">' in scout_captured["system_prompt"]
    assert "将研究问题拆成主题词、方法词、对象词和限制条件" in scout_captured[
        "system_prompt"
    ]
    assert [tool.name for tool in scout_captured["tools"]] == [
        "search_library",
        "search_openalex",
        "search_crossref",
    ]
    assert isinstance(scout_captured["middleware"][1], ModelCallLimitMiddleware)
    assert scout_captured["middleware"][1].run_limit == 9
    assert scout_captured["middleware"][1].exit_behavior == "end"
    assert scout_captured["middleware"][2].tool_name == "search_library"
    assert scout_captured["middleware"][2].run_limit == 2
    assert scout_captured["middleware"][2].exit_behavior == "continue"
    assert "首次返回空数组后禁止再次调用" in scout_captured["system_prompt"]
    assert "search_openalex：最多3次" in scout_captured["system_prompt"]
    assert "检索迭代轮数与单个工具调用上限是不同概念" in scout_captured[
        "system_prompt"
    ]
    assert scout_captured["middleware"][3].tool_name == "search_openalex"
    assert scout_captured["middleware"][3].run_limit == 3
    assert scout_captured["middleware"][3].exit_behavior == "continue"
    assert scout_captured["middleware"][4].tool_name == "search_crossref"
    assert scout_captured["middleware"][4].run_limit == 1
    assert scout_captured["middleware"][4].exit_behavior == "continue"
    assert isinstance(scout_captured["middleware"][5], ExecutedSearchTrackingMiddleware)
    assert len(scout_captured["middleware"]) == 6
    assert isinstance(scout_captured["response_format"], dict)
    assert scout_captured["response_format"]["title"] == "SearchReport"
    synthesizer = next(
        config for config in agent_configs if config["name"] == "research-synthesizer"
    )
    assert '<skill name="research-synthesis">' in synthesizer["system_prompt"]
    assert "只有元数据且findings为空的PaperCard不能支撑综合结论" in synthesizer[
        "system_prompt"
    ]
    assert [tool.name for tool in synthesizer["tools"]] == [
        "get_active_research_project"
    ]
    reviewer = next(
        config for config in agent_configs if config["name"] == "evidence-reviewer"
    )
    assert '<skill name="evidence-review">' in reviewer["system_prompt"]
    assert "研究空白只来自模型推测" in reviewer["system_prompt"]
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
    chief_editor = next(
        config for config in agent_configs if config["name"] == "chief-editor"
    )
    assert len(chief_editor["middleware"]) == 3
    assert isinstance(chief_editor["middleware"][0], SerialToolExecutionMiddleware)
    assert isinstance(chief_editor["middleware"][1], ModelCallLimitMiddleware)
    assert chief_editor["middleware"][1].run_limit == 4
    assert chief_editor["middleware"][2].tool_name == "get_active_research_project"
    assert chief_editor["middleware"][2].run_limit == 2
