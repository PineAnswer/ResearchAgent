from __future__ import annotations

import json
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.application.research_service import ResearchService
from research_agent.domain.models import ResearchStage


class ResearchWorkflowGuardMiddleware(AgentMiddleware):
    """Enforce delegation boundaries that must not depend on model compliance."""

    allowed_subagents = {
        "literature-scout",
        "paper-reader",
        "research-synthesizer",
        "evidence-reviewer",
    }
    project_scoped_tools = {
        "get_research_project",
        "save_screening_decision",
        "commit_subagent_result",
        "save_artifact_and_transition",
        "save_paper_card",
        "advance_project_stage",
        "finish_inconclusive",
    }

    required_stage = {
        "literature-scout": ResearchStage.CREATED,
        "paper-reader": ResearchStage.SCREENED,
        "research-synthesizer": ResearchStage.EXTRACTED,
        "evidence-reviewer": ResearchStage.REVIEW_PENDING,
    }

    def __init__(
        self,
        service: ResearchService | None = None,
        runtime_state: ResearchRuntimeState | None = None,
    ) -> None:
        self.service = service
        self.runtime_state = runtime_state
        self._lock = threading.Lock()
        self._project_created: set[str] = set()
        self._scout_calls: dict[str, int] = {}

    @staticmethod
    def _thread_id(request: ToolCallRequest) -> str:
        configurable = request.runtime.config.get("configurable", {})
        return str(configurable.get("thread_id") or "unscoped")

    @staticmethod
    def _tool_name(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("name", ""))

    @staticmethod
    def _error(request: ToolCallRequest, error_code: str, instruction: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error_code": error_code,
                    "instruction": instruction,
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(request.tool_call.get("id", "workflow-guard")),
            name=ResearchWorkflowGuardMiddleware._tool_name(request),
            status="error",
        )

    def _before(self, request: ToolCallRequest) -> ToolMessage | None:
        name = self._tool_name(request)
        thread_id = self._thread_id(request)
        if name == "create_research_project":
            return None
        with self._lock:
            if (
                name == "task" or name in self.project_scoped_tools
            ) and thread_id not in self._project_created:
                return self._error(
                    request,
                    "project_must_be_created_first",
                    "先调用create_research_project，成功后再委派子Agent或操作项目。",
                )
        if name != "task":
            return None

        args = request.tool_call.get("args", {})
        subagent_type = str(args.get("subagent_type", ""))
        with self._lock:
            if subagent_type not in self.allowed_subagents:
                return self._error(
                    request,
                    "subagent_not_allowed",
                    "只能委派literature-scout、paper-reader、research-synthesizer或evidence-reviewer。",
                )
        if self.service is not None and self.runtime_state is not None:
            project_id = self.runtime_state.project_id(thread_id)
            if project_id is None:
                return self._error(
                    request,
                    "active_project_unavailable",
                    "当前线程没有绑定项目；重新创建项目后再继续。",
                )
            project = self.service.get_project(project_id)
            expected = self.required_stage[subagent_type]
            if project.stage is not expected:
                return self._error(
                    request,
                    "subagent_stage_not_ready",
                    f"{subagent_type}只能在{expected.value}阶段委派；"
                    f"当前阶段为{project.stage.value}。",
                )
            if self.runtime_state.pending_result(thread_id, subagent_type) is not None:
                return self._error(
                    request,
                    "subagent_result_must_be_committed",
                    f"先调用commit_subagent_result保存上一份{subagent_type}结果。",
                )
            if self.runtime_state.rejection_count(thread_id, subagent_type) >= 2:
                return self._error(
                    request,
                    "subagent_retry_limit_reached",
                    f"{subagent_type}已连续生成两份无效结果；停止重试并调用"
                    "finish_inconclusive保存失败原因。",
                )
        if subagent_type == "literature-scout":
            with self._lock:
                count = self._scout_calls.get(thread_id, 0)
                if count >= 1:
                    return self._error(
                        request,
                        "literature_scout_limit_reached",
                        "每个科研任务只允许委派一次literature-scout；使用首次返回结果继续流程。",
                    )
                self._scout_calls[thread_id] = count + 1
        return None

    def _mark_project_created(self, request: ToolCallRequest) -> None:
        thread_id = self._thread_id(request)
        with self._lock:
            self._project_created.add(thread_id)
            self._scout_calls[thread_id] = 0

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        blocked = self._before(request)
        if blocked is not None:
            return blocked
        result = handler(request)
        if self._tool_name(request) == "create_research_project":
            self._mark_project_created(request)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        blocked = self._before(request)
        if blocked is not None:
            return blocked
        result = await handler(request)
        if self._tool_name(request) == "create_research_project":
            self._mark_project_created(request)
        return result
