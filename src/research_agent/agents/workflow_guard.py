from __future__ import annotations

import json
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.application.paper_ids import normalize_paper_id
from research_agent.application.research_service import ResearchService
from research_agent.domain.models import ResearchStage


class ResearchWorkflowGuardMiddleware(AgentMiddleware):
    """Enforce delegation boundaries that must not depend on model compliance."""

    allowed_subagents = {
        "literature-scout",
        "paper-reader",
        "research-synthesizer",
        "evidence-reviewer",
        "research-outliner",
        "narrative-writer",
        "chief-editor",
    }
    project_scoped_tools = {
        "get_research_project",
        "save_screening_decision",
        "commit_subagent_result",
        "save_artifact_and_transition",
        "save_paper_card",
        "advance_project_stage",
        "record_research_issue",
        "finish_inconclusive",
    }

    required_stage = {
        "literature-scout": {ResearchStage.CREATED},
        "paper-reader": {ResearchStage.SCREENED},
        "research-synthesizer": {ResearchStage.EXTRACTED},
        "evidence-reviewer": {ResearchStage.REVIEW_PENDING},
        "research-outliner": {ResearchStage.REVIEWED},
        "narrative-writer": {ResearchStage.OUTLINED},
        "chief-editor": {ResearchStage.OUTLINED},
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
            if (
                name in {"save_screening_decision", "finish_inconclusive"}
                and self.service is not None
                and self.runtime_state is not None
            ):
                project_id = self.runtime_state.project_id(thread_id)
                if project_id is not None:
                    project = self.service.get_project(project_id)
                    if project.stage is ResearchStage.SEARCH_REVIEW_PENDING:
                        return self._error(
                            request,
                            "human_search_review_required",
                            "当前正在等待用户检索审核；只能通过search-feedback API确认候选集或停止任务。",
                        )
            return None

        args = request.tool_call.get("args", {})
        subagent_type = str(args.get("subagent_type", ""))
        with self._lock:
            if subagent_type not in self.allowed_subagents:
                return self._error(
                    request,
                    "subagent_not_allowed",
                    "只能委派已注册的检索、精读、综合、审查、提纲、写作或编辑Agent。",
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
            if project.stage not in expected:
                expected_text = "/".join(sorted(stage.value for stage in expected))
                return self._error(
                    request,
                    "subagent_stage_not_ready",
                    f"{subagent_type}只能在{expected_text}阶段委派；"
                    f"当前阶段为{project.stage.value}。",
                )
            if self.runtime_state.pending_result(thread_id, subagent_type) is not None:
                return self._error(
                    request,
                    "subagent_result_must_be_committed",
                    f"先调用commit_subagent_result保存上一份{subagent_type}结果。",
                )
            rejection_scope = None
            if subagent_type == "paper-reader":
                description = str(args.get("description", ""))
                rejection_scope = self._extract_reader_paper_id(description)
            if (
                self.runtime_state.rejection_count(
                    thread_id, subagent_type, rejection_scope
                )
                >= 2
            ):
                subject = (
                    f"paper-reader处理论文{rejection_scope}"
                    if rejection_scope
                    else subagent_type
                )
                next_step = (
                    "停止重试该论文并继续处理下一篇入选论文。"
                    if subagent_type == "paper-reader"
                    else "停止重试并调用record_research_issue保存可恢复问题。"
                )
                return self._error(
                    request,
                    "subagent_retry_limit_reached",
                    f"{subject}已连续生成两份无效结果；{next_step}",
                )
        if (
            subagent_type == "paper-reader"
            and self.service is not None
            and self.runtime_state is not None
        ):
            project_id = self.runtime_state.project_id(thread_id)
            if project_id is not None:
                blocked = self._reject_reader_outside_screening_decision(
                    request, project_id
                )
                if blocked is not None:
                    return blocked
        if subagent_type == "paper-reader":
            blocked = self._reject_conflicting_reader_description(request)
            if blocked is not None:
                return blocked
        if subagent_type == "research-synthesizer":
            blocked = self._reject_conflicting_synthesizer_description(request)
            if blocked is not None:
                return blocked
        if subagent_type == "literature-scout":
            with self._lock:
                count = self._scout_calls.get(thread_id, 0)
                retry_count = (
                    self.runtime_state.rejection_count(thread_id, subagent_type)
                    if self.runtime_state is not None
                    else 0
                )
                if count >= 1 and not (count == 1 and retry_count == 1):
                    return self._error(
                        request,
                        "literature_scout_limit_reached",
                        "每个科研任务只允许委派一次literature-scout；使用首次返回结果继续流程。",
                    )
                self._scout_calls[thread_id] = count + 1
        return None

    @staticmethod
    def _extract_reader_paper_id(description: str) -> str | None:
        patterns = (
            r"(?:paper_id|paper id|论文ID|论文id)\s*[:：]\s*([A-Za-z0-9_.:/-]+)",
            r"https?://openalex\.org/(W\d+)",
            r"\b(W\d{6,})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, description, flags=re.IGNORECASE)
            if match:
                return match.group(1).rstrip(".,;，。；)")
        return None

    def _reject_reader_outside_screening_decision(
        self, request: ToolCallRequest, project_id: str
    ) -> ToolMessage | None:
        if self.service is None:
            return None
        args = request.tool_call.get("args", {})
        description = str(args.get("description", ""))
        paper_id = self._extract_reader_paper_id(description)
        if not paper_id:
            return None
        screenings = self.service.repository.list_artifacts(
            project_id, "ScreeningDecision"
        )
        if not screenings:
            return None
        normalized_paper_id = normalize_paper_id(paper_id)
        included = {
            normalize_paper_id(item)
            for item in screenings[-1].payload.get("included_paper_ids", [])
        }
        if normalized_paper_id in included:
            return None
        return self._error(
            request,
            "paper_reader_not_in_screening_decision",
            (
                "paper-reader can only read papers included by the latest "
                f"ScreeningDecision. {paper_id!r} is not included; allowed IDs: "
                f"{', '.join(sorted(included)) or '(none)'}."
            ),
        )

    def _reject_conflicting_synthesizer_description(
        self, request: ToolCallRequest
    ) -> ToolMessage | None:
        args = request.tool_call.get("args", {})
        description = str(args.get("description", ""))
        if not description:
            return None
        normalized = description.casefold()
        if "get_research_project" in normalized:
            return self._error(
                request,
                "synthesizer_description_uses_unavailable_project_tool",
                "research-synthesizer只能调用无参数get_active_research_project；"
                "重新委派时只提供project_id、主题和研究问题。",
            )
        schema_markers = (
            "json schema",
            "```json",
            "supporting_evidence",
            "evidence_for",
            "evidence_against",
            "recommendations",
        )
        if any(marker in normalized for marker in schema_markers):
            return self._error(
                request,
                "synthesizer_description_defines_schema",
                "research-synthesizer任务描述不能自定义SynthesisReport JSON字段；"
                "它已绑定官方结构化输出。重新委派时只提供project_id、主题和研究问题。",
            )
        return None

    def _reject_conflicting_reader_description(
        self, request: ToolCallRequest
    ) -> ToolMessage | None:
        args = request.tool_call.get("args", {})
        description = str(args.get("description", ""))
        if not description:
            return None
        normalized = re.sub(r"\s+", "", description).lower()
        forbidden_patterns = (
            "不要包含paper_id",
            "不含paper_id",
            "不要包含paperid",
            "不含paperid",
            "使用简单格式",
            "简单格式如",
            "简单的字符串",
            "simpleformat",
        )
        mentions_evidence = "evidence_id" in normalized or "evidenceid" in normalized
        if mentions_evidence and any(pattern in normalized for pattern in forbidden_patterns):
            return self._error(
                request,
                "paper_reader_description_conflicts_with_evidence_id_policy",
                "paper-reader任务描述不能要求使用简单E1/E2或禁止paper_id前缀；"
                "重新委派时只传论文元数据，不要自定义PaperCard JSON字段。"
                "Evidence ID由paper-reader按paper_id:E序号生成。",
            )
        return None

    def _mark_project_created(self, request: ToolCallRequest) -> None:
        thread_id = self._thread_id(request)
        with self._lock:
            self._project_created.add(thread_id)
            self._scout_calls[thread_id] = 0

    def bind_existing_project(self, thread_id: str) -> None:
        """Authorize a persisted project rebound by the Supervisor for continuation."""
        with self._lock:
            self._project_created.add(thread_id)
            self._scout_calls.setdefault(thread_id, 0)

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
