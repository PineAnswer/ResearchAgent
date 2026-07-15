from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda


def thread_id_from_config(config: RunnableConfig | dict[str, Any] | None) -> str:
    configurable = (config or {}).get("configurable", {})
    return str(configurable.get("thread_id") or "unscoped")


def _json_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("Subagent structured_response must be a JSON object")
    return deepcopy(value)


def _task_text(inputs: Any) -> str:
    if not isinstance(inputs, dict):
        return ""
    messages = inputs.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _expected_paper_id(inputs: Any) -> str | None:
    text = _task_text(inputs)
    patterns = (
        r"paper_id[^:\n]*:\s*[\"']([^\"']+)[\"']",
        r"paper_id[^:\n]*:\s*([^\s,;]+)",
    )
    for pattern in patterns:
        if match := re.search(pattern, text, flags=re.IGNORECASE):
            return match.group(1).strip()
    return None


@dataclass
class RecordedSubagentResult:
    payload: dict[str, Any]
    consumed: bool = False


def _query_key(query: str) -> str:
    """Collapse case, word order, plural suffixes, and repeated tokens."""
    tokens = re.findall(r"[\w]+", query.casefold(), flags=re.UNICODE)
    normalized = {
        token[:-1] if token.endswith("s") and len(token) > 3 else token
        for token in tokens
    }
    return " ".join(sorted(normalized))


def _paper_key(paper_id: str, doi: str) -> str:
    if match := re.search(r"\bW\d+\b", paper_id, flags=re.IGNORECASE):
        return match.group(0).upper()
    normalized_doi = doi.casefold().removeprefix("https://doi.org/").strip()
    if normalized_doi:
        return f"doi:{normalized_doi}"
    return paper_id.casefold().strip() or "unknown-paper"


class ResearchRuntimeState:
    """Thread-scoped project, search, and structured subagent state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._project_ids: dict[str, str] = {}
        self._search_terms: dict[str, list[str]] = {}
        self._search_keys: dict[str, set[str]] = {}
        self._results: dict[tuple[str, str], RecordedSubagentResult] = {}
        self._rejections: dict[tuple[str, str], int] = {}
        self._paper_fetches: dict[tuple[str, str], set[str]] = {}

    def register_project(self, thread_id: str, project_id: str) -> None:
        with self._lock:
            self._project_ids[thread_id] = project_id
            self._search_terms[thread_id] = []
            self._search_keys[thread_id] = set()
            for key in [item for item in self._results if item[0] == thread_id]:
                del self._results[key]
            for key in [item for item in self._rejections if item[0] == thread_id]:
                del self._rejections[key]
            for key in [item for item in self._paper_fetches if item[0] == thread_id]:
                del self._paper_fetches[key]

    def project_id(self, thread_id: str) -> str | None:
        with self._lock:
            return self._project_ids.get(thread_id)

    def record_search(self, thread_id: str, query: str) -> bool:
        query = query.strip()
        if not query:
            return False
        with self._lock:
            key = _query_key(query)
            keys = self._search_keys.setdefault(thread_id, set())
            if key in keys:
                return False
            keys.add(key)
            terms = self._search_terms.setdefault(thread_id, [])
            terms.append(query)
            return True

    def search_terms(self, thread_id: str) -> list[str]:
        with self._lock:
            return list(self._search_terms.get(thread_id, []))

    def record_result(self, thread_id: str, subagent_type: str, payload: Any) -> None:
        with self._lock:
            self._results[(thread_id, subagent_type)] = RecordedSubagentResult(
                payload=_json_payload(payload)
            )

    def pending_result(self, thread_id: str, subagent_type: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is None or record.consumed:
                return None
            return deepcopy(record.payload)

    def mark_consumed(self, thread_id: str, subagent_type: str) -> None:
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is not None:
                record.consumed = True
            self._rejections.pop((thread_id, subagent_type), None)

    def reject_result(self, thread_id: str, subagent_type: str) -> int:
        """Consume an invalid result so a corrected subagent run can replace it."""
        with self._lock:
            record = self._results.get((thread_id, subagent_type))
            if record is not None:
                record.consumed = True
            key = (thread_id, subagent_type)
            count = self._rejections.get(key, 0) + 1
            self._rejections[key] = count
            return count

    def rejection_count(self, thread_id: str, subagent_type: str) -> int:
        with self._lock:
            return self._rejections.get((thread_id, subagent_type), 0)

    def reserve_paper_fetch(
        self,
        thread_id: str,
        paper_id: str,
        doi: str,
        url: str,
        max_attempts: int,
    ) -> str | None:
        """Reserve a distinct fetch request or return a structured error code."""
        with self._lock:
            key = (thread_id, _paper_key(paper_id, doi))
            signatures = self._paper_fetches.setdefault(key, set())
            signature = "|".join(
                [
                    paper_id.casefold().strip(),
                    doi.casefold().removeprefix("https://doi.org/").strip(),
                    url.casefold().strip(),
                ]
            )
            if signature in signatures:
                return "duplicate_paper_fetch"
            if len(signatures) >= max(1, max_attempts):
                return "paper_fetch_limit_reached"
            signatures.add(signature)
            return None

    def reset_paper_fetch(self, thread_id: str, paper_id: str, doi: str = "") -> None:
        """Allow a fresh subagent attempt while the download tool reuses its disk cache."""
        with self._lock:
            self._paper_fetches.pop((thread_id, _paper_key(paper_id, doi)), None)


class ExecutedSearchTrackingMiddleware(AgentMiddleware):
    """Record executed searches and block duplicate query intent."""

    def __init__(self, state: ResearchRuntimeState) -> None:
        self.state = state

    def _reserve(self, request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name", ""))
        if name not in {"search_openalex", "search_crossref"}:
            return None
        args = request.tool_call.get("args", {})
        query = str(args.get("query", ""))
        if self.state.record_search(
            thread_id_from_config(request.runtime.config), query
        ):
            return None
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error_code": "duplicate_search_query",
                    "query": query,
                    "instruction": "该查询与已执行查询重复；使用已有结果或换用真正互补的检索角度。",
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(request.tool_call.get("id", "duplicate-search")),
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        blocked = self._reserve(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        blocked = self._reserve(request)
        return blocked if blocked is not None else await handler(request)


class PaperFetchGuardMiddleware(AgentMiddleware):
    """Prevent one paper from causing unbounded or duplicate network fetches."""

    def __init__(self, state: ResearchRuntimeState, max_attempts_per_paper: int = 2):
        self.state = state
        self.max_attempts_per_paper = max(1, max_attempts_per_paper)

    def _before(self, request: ToolCallRequest) -> ToolMessage | None:
        if str(request.tool_call.get("name", "")) != "fetch_paper_text":
            return None
        args = request.tool_call.get("args", {})
        error_code = self.state.reserve_paper_fetch(
            thread_id_from_config(request.runtime.config),
            str(args.get("paper_id", "")),
            str(args.get("doi", "")),
            str(args.get("url", "")),
            self.max_attempts_per_paper,
        )
        if error_code is None:
            return None
        instruction = (
            "相同参数已经尝试过；禁止重复调用，立即使用此前结果或摘要生成PaperCard。"
            if error_code == "duplicate_paper_fetch"
            else "该论文已达到全文获取上限；停止猜测URL，使用摘要生成PaperCard。"
        )
        return ToolMessage(
            content=json.dumps(
                {"ok": False, "error_code": error_code, "instruction": instruction},
                ensure_ascii=False,
            ),
            tool_call_id=str(request.tool_call.get("id", "paper-fetch-guard")),
            name="fetch_paper_text",
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        blocked = self._before(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        blocked = self._before(request)
        return blocked if blocked is not None else await handler(request)


def recording_runnable(agent: Any, subagent_type: str, state: ResearchRuntimeState):
    """Wrap a structured agent and retain its exact JSON response for atomic commit."""

    def record(result: dict[str, Any], inputs: Any, config: RunnableConfig) -> dict[str, Any]:
        thread_id = thread_id_from_config(config)
        structured_response = result.get("structured_response")
        if structured_response is None:
            payload = {
                "_subagent_error": "structured_response_missing",
                "_instruction": (
                    "模型没有返回可解析的结构化对象；请提交该失败结果，"
                    "由系统释放后重新委派一次。"
                ),
            }
            if expected_id := _expected_paper_id(inputs):
                payload["_paper_id"] = expected_id
            result = {**result, "structured_response": payload}
        else:
            payload = _json_payload(structured_response)
        if subagent_type == "literature-scout":
            payload["search_terms"] = state.search_terms(thread_id)
        elif subagent_type == "paper-reader":
            if expected_id := _expected_paper_id(inputs):
                payload["paper_id"] = expected_id
        state.record_result(thread_id, subagent_type, payload)
        return result

    def invoke(inputs: Any, config: RunnableConfig) -> dict[str, Any]:
        return record(agent.invoke(inputs, config=config), inputs, config)

    async def ainvoke(inputs: Any, config: RunnableConfig) -> dict[str, Any]:
        result = await agent.ainvoke(inputs, config=config)
        return record(result, inputs, config)

    return RunnableLambda(invoke, afunc=ainvoke, name=f"record-{subagent_type}")
