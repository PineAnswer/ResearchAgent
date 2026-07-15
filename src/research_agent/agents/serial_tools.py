from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any
from weakref import WeakKeyDictionary

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage


class SerialToolExecutionMiddleware(AgentMiddleware):
    """Keep model decisions and actual tool execution strictly serial.

    If a model emits several calls in one message, only the first call is kept.
    The model must observe that result before selecting another tool. A lock is
    also kept around execution as a safety boundary for both synchronous and
    asynchronous graph entry points.

    Each Agent must own its own middleware instance. A Supervisor can then wait
    for a delegated subagent without holding the same lock used by that
    subagent's internal tools.
    """

    def __init__(self) -> None:
        self._sync_lock = threading.Lock()
        self._async_locks_guard = threading.Lock()
        self._async_locks: WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = WeakKeyDictionary()

    @staticmethod
    def _keep_first_tool_call(response: ModelResponse) -> ModelResponse:
        messages = []
        for message in response.result:
            if isinstance(message, AIMessage) and len(message.tool_calls) > 1:
                message = message.model_copy(update={"tool_calls": message.tool_calls[:1]})
            messages.append(message)
        return ModelResponse(
            result=messages,
            structured_response=response.structured_response,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._keep_first_tool_call(handler(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return self._keep_first_tool_call(await handler(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        with self._sync_lock:
            return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        loop = asyncio.get_running_loop()
        with self._async_locks_guard:
            lock = self._async_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._async_locks[loop] = lock

        async with lock:
            return await handler(request)
