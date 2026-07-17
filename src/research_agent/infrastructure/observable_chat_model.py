from __future__ import annotations

from typing import Any

from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI


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
