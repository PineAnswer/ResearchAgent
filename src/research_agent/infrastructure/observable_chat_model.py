from __future__ import annotations

from typing import Any

from langchain.agents.structured_output import ToolStrategy
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI
from pydantic import BaseModel


def _provider_payload(response: Any) -> Any:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    return str(response)


class ObservableChatOpenAI(ChatOpenAI):
    """Keep the provider response on callback-only generation metadata."""

    def _create_chat_result(
        self,
        response: dict[str, Any] | Any,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        provider_response = _provider_payload(response)
        for generation in result.generations:
            generation.generation_info = {
                **(generation.generation_info or {}),
                "raw_provider_response": provider_response,
            }
        return result


class ObservableChatAnthropic(ChatAnthropic):
    """Keep the provider response on callback-only generation metadata.

    Mirrors `ObservableChatOpenAI` for the native Anthropic Messages API path,
    used when `RESEARCH_AGENT_MODEL` resolves to a Claude model.
    """

    def _format_output(self, data: Any, **kwargs: Any) -> ChatResult:
        result = super()._format_output(data, **kwargs)
        provider_response = _provider_payload(data)
        for generation in result.generations:
            generation.generation_info = {
                **(generation.generation_info or {}),
                "raw_provider_response": provider_response,
            }
        return result


def structured_output_strategy(model: BaseChatModel | str, schema: type[BaseModel]) -> Any:
    """Return a `response_format` value that is safe for the resolved model.

    Native OpenAI-style `response_format` (json_schema) is only implemented by
    a few first-party providers. OpenAI-compatible relays and most third-party
    gateways ignore or reject it, so the model call silently completes without
    any parsed `structured_response` (observed as `None` downstream). Function
    calling, by contrast, is required for these agents' tools to work at all,
    so `ToolStrategy` is the one mechanism that is reliable everywhere —
    including genuine OpenAI, Claude, and OpenAI-compatible relays. Use it
    unconditionally.
    """
    # The model argument stays in the signature for call-site compatibility;
    # every supported provider path now uses the same tool-calling strategy.
    return ToolStrategy(schema)
