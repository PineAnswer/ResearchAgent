from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage

from research_agent.agents.serial_tools import SerialToolExecutionMiddleware


def _parallel_tool_response() -> ModelResponse:
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "first", "args": {}, "id": "call-1", "type": "tool_call"},
                    {"name": "second", "args": {}, "id": "call-2", "type": "tool_call"},
                ],
            )
        ]
    )


def test_serial_middleware_keeps_only_first_model_tool_call() -> None:
    middleware = SerialToolExecutionMiddleware()

    response = middleware.wrap_model_call(None, lambda _request: _parallel_tool_response())

    message = response.result[0]
    assert isinstance(message, AIMessage)
    assert [call["name"] for call in message.tool_calls] == ["first"]


def test_serial_middleware_keeps_only_first_async_model_tool_call() -> None:
    async def run_call() -> ModelResponse:
        middleware = SerialToolExecutionMiddleware()

        async def handler(_request):
            return _parallel_tool_response()

        return await middleware.awrap_model_call(None, handler)

    response = asyncio.run(run_call())

    message = response.result[0]
    assert isinstance(message, AIMessage)
    assert [call["name"] for call in message.tool_calls] == ["first"]


def test_serial_middleware_limits_sync_tool_calls_to_one() -> None:
    middleware = SerialToolExecutionMiddleware()
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def handler(_request):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.01)
        with state_lock:
            active -= 1
        return "ok"

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(
            executor.map(
                lambda _: middleware.wrap_tool_call(None, handler),
                range(6),
            )
        )

    assert results == ["ok"] * 6
    assert maximum_active == 1


def test_serial_middleware_limits_async_tool_calls_to_one() -> None:
    async def run_calls() -> tuple[list[str], int]:
        middleware = SerialToolExecutionMiddleware()
        active = 0
        maximum_active = 0

        async def handler(_request):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "ok"

        results = await asyncio.gather(
            *(middleware.awrap_tool_call(None, handler) for _ in range(6))
        )
        return results, maximum_active

    results, maximum_active = asyncio.run(run_calls())

    assert results == ["ok"] * 6
    assert maximum_active == 1
