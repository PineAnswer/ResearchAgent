from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import Any

from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.domain.models import ConversationRun, ResearchStage
from research_agent.infrastructure.sqlite_repository import (
    ActiveConversationRunError,
    SqliteResearchRepository,
)


STAGE_PHASES = {
    ResearchStage.CREATED: "thinking",
    ResearchStage.SEARCHED: "searching",
    ResearchStage.SEARCH_REVIEW_PENDING: "searching",
    ResearchStage.SCREENED: "reading",
    ResearchStage.EXTRACTED: "synthesizing",
    ResearchStage.SYNTHESIZED: "reviewing",
    ResearchStage.REVIEW_PENDING: "reviewing",
    ResearchStage.REVIEWED: "outlining",
    ResearchStage.OUTLINED: "writing",
    ResearchStage.NARRATED: "done",
    ResearchStage.COMPLETED: "done",
    ResearchStage.INCONCLUSIVE: "stopped",
}


def _message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _assistant_summary(result: dict[str, Any]) -> str:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        content = _message_content(message)
        if content:
            return content
    return ""


class ConversationRunManager:
    """Run different conversations concurrently and serialize each conversation."""

    def __init__(self, supervisor: ResearchSupervisor):
        self.supervisor = supervisor
        self.repository: SqliteResearchRepository = supervisor.repository
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._guard = asyncio.Lock()
        self._progress_lock = threading.RLock()
        self._progress_events: dict[str, list[dict[str, Any]]] = {}

    def _record_progress(self, run_id: str, event: dict[str, Any]) -> None:
        with self._progress_lock:
            events = self._progress_events.setdefault(run_id, [])
            events.append(dict(event))
            if len(events) > 120:
                del events[:-120]

    def progress_events(self, run_id: str | None) -> list[dict[str, Any]]:
        if not run_id:
            return []
        with self._progress_lock:
            return [dict(event) for event in self._progress_events.get(run_id, [])]

    async def start_initial(
        self,
        conversation_id: str,
        user_id: str,
        *,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
        year_from: int = 2024,
        year_to: int = 2026,
        quality_venues_only: bool = False,
        prefer_library_search: bool = False,
    ) -> ConversationRun:
        return await self._start(
            conversation_id,
            user_id,
            "initial",
            {
                "min_papers": min_papers,
                "max_papers": max_papers,
                "max_search_rounds": max_search_rounds,
                "year_from": year_from,
                "year_to": year_to,
                "quality_venues_only": quality_venues_only,
                "prefer_library_search": prefer_library_search,
            },
        )

    async def start_continue(
        self,
        conversation_id: str,
        user_id: str,
    ) -> ConversationRun:
        return await self._start(conversation_id, user_id, "continue", {})

    async def _start(
        self,
        conversation_id: str,
        user_id: str,
        kind: str,
        options: dict[str, Any],
    ) -> ConversationRun:
        with self.repository.user_scope(user_id):
            run = self.repository.create_conversation_run(conversation_id, kind)
        with self._progress_lock:
            self._progress_events[run.run_id] = []
            while len(self._progress_events) > 50:
                self._progress_events.pop(next(iter(self._progress_events)))
        async with self._guard:
            task = asyncio.create_task(
                self._execute(run.run_id, user_id, options),
                name=f"research-conversation-{conversation_id}",
            )
            self._tasks[run.run_id] = task
            task.add_done_callback(
                lambda _task, run_id=run.run_id: self._tasks.pop(run_id, None)
            )
        return run

    async def _execute(
        self,
        run_id: str,
        user_id: str,
        options: dict[str, Any],
    ) -> None:
        with self.repository.user_scope(user_id):
            run = self.repository.get_conversation_run(run_id)
            project = self.supervisor.service.get_project(run.project_id)
            self.repository.update_conversation_run(
                run_id,
                status="running",
                phase=(
                    "thinking"
                    if run.kind == "initial"
                    else STAGE_PHASES.get(project.stage, "thinking")
                ),
                message=(
                    "正在创建检索策略并分析研究问题"
                    if run.kind == "initial"
                    else "正在从已保存进度恢复研究"
                ),
                started_at=datetime.now(UTC),
            )
        try:
            with self.repository.user_scope(user_id):
                if run.kind == "initial":
                    result = await self.supervisor.astart_project(
                        run.project_id,
                        run.thread_id,
                        progress_callback=lambda event: self._record_progress(run_id, event),
                        **options,
                    )
                else:
                    result = await self.supervisor.acontinue_project(
                        run.project_id,
                        run.thread_id,
                        progress_callback=lambda event: self._record_progress(run_id, event),
                    )
                project = self.supervisor.service.get_project(run.project_id)
                if project.stage is ResearchStage.SEARCH_REVIEW_PENDING:
                    status = "awaiting_input"
                    message = "候选论文已准备好，等待人工审核"
                elif project.stage is ResearchStage.REVIEWED:
                    status = "interrupted"
                    if project.current_review and project.current_review.verdict.value == "REVISE":
                        message = "自动修订流程提前停止，可从已保存证据恢复"
                    else:
                        message = "综述写作流程提前停止，可从已保存审查结果恢复"
                elif project.stage is ResearchStage.COMPLETED:
                    status = "completed"
                    message = "综述已生成，研究已完成"
                elif project.stage is ResearchStage.INCONCLUSIVE:
                    status = "inconclusive"
                    message = "本轮研究已停止，可查看已保存产物"
                else:
                    status = "completed"
                    message = f"本轮运行结束，项目停在 {project.stage.value}"
                self.repository.update_conversation_run(
                    run_id,
                    status=status,
                    phase=(
                        "reviewing"
                        if project.stage is ResearchStage.REVIEWED
                        else STAGE_PHASES[project.stage]
                    ),
                    message=message,
                    finished_at=datetime.now(UTC),
                )
                if summary := _assistant_summary(result):
                    self.repository.append_conversation_message(
                        run.conversation_id,
                        "assistant",
                        summary,
                        run_id=run_id,
                    )
        except asyncio.CancelledError:
            with self.repository.user_scope(user_id):
                self.repository.update_conversation_run(
                    run_id,
                    status="interrupted",
                    phase="stopped",
                    message="服务停止导致本轮运行中断，可稍后恢复",
                    finished_at=datetime.now(UTC),
                )
            raise
        except Exception as exc:
            with self.repository.user_scope(user_id):
                project = self.supervisor.service.get_project(run.project_id)
                self.repository.update_conversation_run(
                    run_id,
                    status="failed",
                    phase=STAGE_PHASES.get(project.stage, "stopped"),
                    message="研究执行失败，已保存此前进度",
                    error=str(exc),
                    finished_at=datetime.now(UTC),
                )
                self.repository.append_conversation_message(
                    run.conversation_id,
                    "system",
                    f"运行失败：{exc}",
                    run_id=run_id,
                )

    async def shutdown(self) -> None:
        async with self._guard:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "ActiveConversationRunError",
    "ConversationRunManager",
]
