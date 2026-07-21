from anthropic.types import Message, ToolUseBlock, Usage
from langchain.agents.structured_output import ToolStrategy

import research_agent.agents.supervisor as supervisor_module
from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.domain.models import LibraryAgentResponse
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.observable_chat_model import (
    ObservableChatAnthropic,
    structured_output_strategy,
)


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "model": "openai:gpt-5.6",
        "data_dir": tmp_path,
        "database_path": tmp_path / "agent.db",
        "filesystem_root": tmp_path / "filesystem",
    }
    values.update(overrides)
    return Settings(**values)


def test_provider_resolution_supports_explicit_and_automatic_routes(tmp_path) -> None:
    assert make_settings(tmp_path).resolved_model() == ("openai", "gpt-5.6")
    assert make_settings(
        tmp_path,
        model="claude-opus-4-8",
    ).resolved_model() == ("anthropic", "claude-opus-4-8")
    assert make_settings(
        tmp_path,
        model="anthropic:claude-opus-4-8",
        provider="anthropic",
    ).resolved_model() == ("anthropic", "claude-opus-4-8")


def test_provider_resolution_rejects_conflicting_configuration(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        model="openai:gpt-5.6",
        provider="anthropic",
    )

    try:
        settings.resolved_model()
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("provider/model conflicts must be rejected")


def test_openai_and_anthropic_use_separate_keys_and_urls(tmp_path, monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def fake_openai(**kwargs):
        captured["openai"] = kwargs
        return object()

    def fake_anthropic(**kwargs):
        captured["anthropic"] = kwargs
        return object()

    monkeypatch.setattr(supervisor_module, "ObservableChatOpenAI", fake_openai)
    monkeypatch.setattr(supervisor_module, "ObservableChatAnthropic", fake_anthropic)

    supervisor = object.__new__(ResearchSupervisor)
    supervisor.settings = make_settings(
        tmp_path,
        model="openai:gpt-5.6",
        provider="openai",
        openai_api_key="openai-test-key",
        anthropic_api_key="anthropic-test-key",
        base_url="https://relay.example/v1",
        anthropic_base_url="https://relay.example",
    )
    supervisor._build_model()

    supervisor.settings = make_settings(
        tmp_path,
        model="anthropic:claude-opus-4-8",
        provider="anthropic",
        openai_api_key="openai-test-key",
        anthropic_api_key="anthropic-test-key",
        base_url="https://relay.example/v1",
        anthropic_base_url="https://relay.example",
    )
    supervisor._build_model()

    assert captured["openai"] == {
        "model": "gpt-5.6",
        "api_key": "openai-test-key",
        "base_url": "https://relay.example/v1",
    }
    assert captured["anthropic"] == {
        "model": "claude-opus-4-8",
        "api_key": "anthropic-test-key",
        "base_url": "https://relay.example",
    }


def test_anthropic_never_falls_back_to_openai_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    supervisor = object.__new__(ResearchSupervisor)
    supervisor.settings = make_settings(
        tmp_path,
        model="anthropic:claude-opus-4-8",
        provider="anthropic",
    )

    try:
        supervisor._build_model()
    except ValueError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError("Claude must require its own credential")


def test_native_anthropic_tool_calls_remain_structured_dicts() -> None:
    model = ObservableChatAnthropic(
        model="claude-opus-4-8",
        api_key="test-key",
        base_url="https://example.invalid",
    )
    response = Message(
        id="msg-1",
        content=[
            ToolUseBlock(
                id="tool-1",
                input={"topic": "GeoAI", "research_question": "How reliable is it?"},
                name="create_project",
                type="tool_use",
            )
        ],
        model="claude-opus-4-8",
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=5, output_tokens=3),
    )

    result = model._format_output(response)
    call = result.generations[0].message.tool_calls[0]

    assert call["name"] == "create_project"
    assert call["args"] == {
        "topic": "GeoAI",
        "research_question": "How reliable is it?",
    }
    assert "raw_provider_response" in result.generations[0].generation_info


def test_claude_structured_output_uses_tool_strategy() -> None:
    model = ObservableChatAnthropic(model="claude-opus-4-8", api_key="test-key")

    strategy = structured_output_strategy(model, LibraryAgentResponse)

    assert isinstance(strategy, ToolStrategy)
    assert strategy.schema is LibraryAgentResponse
