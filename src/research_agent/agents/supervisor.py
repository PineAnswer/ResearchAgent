from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.error import URLError

import httpx
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import InMemorySaver
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from research_agent.agents.prompts import PI_PROMPT, inject_skill
from research_agent.agents.registry import build_subagent_registry
from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.agents.serial_tools import SerialToolExecutionMiddleware
from research_agent.agents.workflow_guard import ResearchWorkflowGuardMiddleware
from research_agent.application.fallback import OfflineFallback
from research_agent.application.research_service import ResearchService
from research_agent.application.research_service import WorkflowPrerequisiteError
from research_agent.application.search_review import SearchReviewService
from research_agent.domain.models import ResearchStage
from research_agent.infrastructure.artifact_exporter import JsonArtifactExporter
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.run_logger import ResearchRunLogger
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.infrastructure.workspace import WorkspaceBootstrapper
from research_agent.tools.literature_tools import build_literature_tools
from research_agent.tools.project_tools import build_project_tools


class AgentUnavailableError(RuntimeError):
    """Raised when the model-backed Agent graph cannot be used."""


class AgentExecutionError(RuntimeError):
    """Carry the active project ID across an async Agent failure."""

    def __init__(self, original_error: BaseException, project_id: str | None):
        super().__init__(str(original_error))
        self.original_error = original_error
        self.project_id = project_id


FALLBACK_EXCEPTIONS = (
    AgentUnavailableError,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
    httpx.NetworkError,
    httpx.TimeoutException,
    URLError,
    ConnectionError,
    TimeoutError,
)


class ResearchSupervisor:
    """Shared orchestration entry for CLI and API."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.repository = SqliteResearchRepository(self.settings.database_path)
        self.exporter = JsonArtifactExporter(self.settings.data_dir / "outputs")
        self.service = ResearchService(self.repository, self.exporter)
        self.fallback = OfflineFallback(self.service)
        self.runtime_assets = WorkspaceBootstrapper(self.settings.filesystem_root).prepare()
        self.runtime_state = ResearchRuntimeState()
        self.checkpointer = InMemorySaver()
        self.literature_tools = build_literature_tools(
            self.settings.filesystem_root,
            openalex_api_key=self.settings.openalex_api_key,
            contact_email=self.settings.openalex_email,
            max_retries=self.settings.search_max_retries,
            backoff_seconds=self.settings.search_backoff_seconds,
            max_retry_wait_seconds=self.settings.search_max_retry_wait_seconds,
        )
        self.literature_tools_by_name = {
            tool.name: tool for tool in self.literature_tools
        }
        self._search_review_options_by_thread: dict[str, dict[str, int]] = {}
        self.search_review = SearchReviewService(
            self.service,
            self.literature_tools_by_name,
            max_rounds=self.settings.max_search_review_rounds,
            max_queries_per_round=self.settings.max_suggested_queries_per_round,
        )
        self.workflow_guard = ResearchWorkflowGuardMiddleware(
            self.service, self.runtime_state
        )
        self.initialization_error: str | None = None
        try:
            self.graph = self._build_graph()
        except Exception as exc:
            if not self.settings.enable_fallback:
                raise
            self.graph = None
            self.initialization_error = str(exc)

    def _build_model(self):
        if not self.settings.base_url:
            return self.settings.model

        from langchain_openai import ChatOpenAI

        model_name = self.settings.model.split(":", maxsplit=1)[-1]
        return ChatOpenAI(
            model=model_name,
            api_key=os.getenv("OPENAI_API_KEY", "not-set"),
            base_url=self.settings.base_url,
        )

    @staticmethod
    def _search_review_options(
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
    ) -> dict[str, int]:
        options: dict[str, int] = {}
        if min_papers is not None:
            options["min_papers"] = min_papers
        if max_papers is not None:
            options["max_papers"] = max_papers
        if max_search_rounds is not None:
            options["max_search_rounds"] = max_search_rounds
        return options

    def _register_search_review_options(
        self,
        thread_id: str,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
    ) -> None:
        options = self._search_review_options(
            min_papers=min_papers,
            max_papers=max_papers,
            max_search_rounds=max_search_rounds,
        )
        if options:
            self._search_review_options_by_thread[thread_id] = options

    def _begin_search_review(self, project_id: str, thread_id: str) -> dict[str, Any]:
        options = self._search_review_options_by_thread.get(thread_id, {})
        return self.search_review.begin_review(project_id, **options)

    def _build_graph(self):
        model = self._build_model()
        project_tools = build_project_tools(
            self.service,
            self.runtime_state,
            on_search_committed=self._begin_search_review,
        )
        all_tools = [*project_tools, *self.literature_tools]
        tools_by_name = {tool.name: tool for tool in all_tools}
        subagents = build_subagent_registry(
            tools_by_name,
            self.runtime_assets.skill_contents,
            model=model,
            runtime_state=self.runtime_state,
            max_openalex_searches=self.settings.max_openalex_searches,
            max_crossref_searches=self.settings.max_crossref_searches,
            max_paper_fetches_per_paper=self.settings.max_paper_fetches_per_paper,
        )
        hidden_supervisor_tools = {
            "save_project_artifact",
            "transition_project_stage",
            "save_artifact_and_transition",
            "save_paper_card",
            "get_active_research_project",
            "search_openalex",
            "search_crossref",
            "extract_pdf_text",
            "fetch_paper_text",
            "verify_doi",
        }
        supervisor_tools = [
            tool for tool in all_tools if tool.name not in hidden_supervisor_tools
        ]
        try:
            supervisor_prompt = inject_skill(
                PI_PROMPT,
                "research-protocol",
                self.runtime_assets.skill_contents["research-protocol"],
            )
        except KeyError as exc:
            raise ValueError("Missing Supervisor Skill: research-protocol") from exc

        return create_deep_agent(
            model=model,
            system_prompt=supervisor_prompt,
            tools=supervisor_tools,
            middleware=[
                SerialToolExecutionMiddleware(),
                self.workflow_guard,
            ],
            subagents=subagents,
            memory=self.runtime_assets.memory_paths,
            backend=FilesystemBackend(
                root_dir=str(self.settings.filesystem_root),
                virtual_mode=True,
            ),
            checkpointer=self.checkpointer,
            name="research-supervisor",
        )

    @staticmethod
    def build_prompt(
        topic: str,
        research_question: str,
        *,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
    ) -> str:
        limits: list[str] = []
        if min_papers is not None:
            limits.append(f"- 精读篇数下限：{min_papers}")
        if max_papers is not None:
            limits.append(f"- 精读篇数上限：{max_papers}")
        if max_search_rounds is not None:
            limits.append(f"- 系统检索-筛选迭代轮数上限：{max_search_rounds}")
        limit_text = ""
        if limits:
            limit_text = (
                "\n用户在前端设置了检索审核限制：\n"
                + "\n".join(limits)
                + "\n委派 literature-scout 时必须把这些限制传入任务描述。"
                " literature-scout 应在单次子任务内自动执行：检索→标题摘要级筛选→"
                "总结覆盖盲区/筛选意见→据此改写下一轮检索词；达到轮数上限、"
                "入选论文数满足上下限且覆盖盲区可接受，或工具上限触发后，才返回最终 SearchReport。"
                " 不要在每一轮后等待用户反馈。"
            )
        return (
            f"研究主题：{topic}\n"
            f"研究问题：{research_question}\n"
            f"{limit_text}\n"
            "请创建项目，并按证据驱动科研流程执行。"
        )

    @staticmethod
    def build_continue_prompt(
        project_id: str,
        screening_context: dict[str, Any] | None = None,
    ) -> str:
        context_text = ""
        if screening_context is not None:
            context_text = (
                "\n\n系统已从数据库预读并验证最新 ScreeningDecision。"
                "以下 screened_context 是继续阶段的权威输入；不要为了寻找"
                " ScreeningDecision 去读取或 grep 完整大快照。\n"
                "screened_context:\n"
                f"{json.dumps(screening_context, ensure_ascii=False, indent=2)}"
            )
        return (
            f"继续已有科研项目：{project_id}\n"
            "该项目已经完成人工候选论文审核并处于SCREENED阶段。"
            "禁止创建新项目或重新检索；直接从逐篇paper-reader开始继续。"
            "Continue from the latest ScreeningDecision only. "
            "Dispatch paper-reader only for included_paper_ids from screened_context. "
            "Ignore SearchReport/CandidateSetSnapshot candidates that are not included. "
            "If no included papers are available, finish with InsufficientEvidence."
            f"{context_text}"
        )

    @staticmethod
    def build_config(thread_id: str | None = None) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}}

    def _new_run_logger(
        self,
        topic: str,
        research_question: str,
        thread_id: str,
        show_progress: bool,
    ) -> ResearchRunLogger:
        return ResearchRunLogger(
            runs_root=self.settings.data_dir / "runs",
            topic=topic,
            research_question=research_question,
            thread_id=thread_id,
            console=show_progress,
        )

    def _invoke_graph(
        self,
        topic: str,
        research_question: str,
        thread_id: str,
        run_logger: ResearchRunLogger,
        search_review_options: dict[str, int] | None = None,
    ) -> dict:
        if self.graph is None:
            raise AgentUnavailableError(
                self.initialization_error or "Agent graph is unavailable"
            )
        search_review_options = search_review_options or {}
        config = self.build_config(thread_id)
        config["callbacks"] = [run_logger]
        result = self.graph.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": self.build_prompt(
                            topic,
                            research_question,
                            **search_review_options,
                        ),
                    }
                ]
            },
            config=config,
        )
        if run_logger.project_id:
            result["project_status"] = self.service.get_project(
                run_logger.project_id
            ).model_dump(mode="json")
        return result

    def _status_result(self, run_logger: ResearchRunLogger) -> dict[str, Any] | None:
        """Preserve the authoritative project stage even when graph execution fails."""
        if not run_logger.project_id:
            return None
        try:
            project = self.service.get_project(run_logger.project_id)
        except Exception:
            return None
        return {"project_status": project.model_dump(mode="json")}

    def invoke(
        self,
        topic: str,
        research_question: str,
        thread_id: str | None = None,
        show_progress: bool = False,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
    ) -> dict:
        active_thread_id = thread_id or uuid.uuid4().hex
        search_review_options = self._search_review_options(
            min_papers=min_papers,
            max_papers=max_papers,
            max_search_rounds=max_search_rounds,
        )
        self._register_search_review_options(active_thread_id, **search_review_options)
        run_logger = self._new_run_logger(
            topic,
            research_question,
            active_thread_id,
            show_progress,
        )
        try:
            result = self._invoke_graph(
                topic,
                research_question,
                active_thread_id,
                run_logger,
                search_review_options,
            )
        except Exception as exc:
            run_logger.finish(
                "error",
                result=self._status_result(run_logger),
                error=str(exc),
            )
            raise AgentExecutionError(exc, run_logger.project_id) from exc
        finally:
            self._search_review_options_by_thread.pop(active_thread_id, None)
        run_logger.finish("completed", result=result)
        return result

    async def ainvoke(
        self,
        topic: str,
        research_question: str,
        thread_id: str | None = None,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
    ) -> dict:
        if self.graph is None:
            raise AgentUnavailableError(
                self.initialization_error or "Agent graph is unavailable"
            )
        active_thread_id = thread_id or uuid.uuid4().hex
        search_review_options = self._search_review_options(
            min_papers=min_papers,
            max_papers=max_papers,
            max_search_rounds=max_search_rounds,
        )
        self._register_search_review_options(active_thread_id, **search_review_options)
        run_logger = self._new_run_logger(topic, research_question, active_thread_id, False)
        config = self.build_config(active_thread_id)
        config["callbacks"] = [run_logger]
        try:
            result = await self.graph.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self.build_prompt(
                                topic,
                                research_question,
                                **search_review_options,
                            ),
                        }
                    ]
                },
                config=config,
            )
            if run_logger.project_id:
                result["project_status"] = self.service.get_project(
                    run_logger.project_id
                ).model_dump(mode="json")
        except Exception as exc:
            run_logger.finish(
                "error",
                result=self._status_result(run_logger),
                error=str(exc),
            )
            raise AgentExecutionError(exc, run_logger.project_id) from exc
        finally:
            self._search_review_options_by_thread.pop(active_thread_id, None)
        run_logger.finish("completed", result=result)
        return result

    async def acontinue_project(
        self,
        project_id: str,
        thread_id: str | None = None,
    ) -> dict:
        """Continue a persisted project after the human accepted its candidate set."""
        if self.graph is None:
            raise AgentUnavailableError(
                self.initialization_error or "Agent graph is unavailable"
            )
        project = self.service.get_project(project_id)
        if project.stage is not ResearchStage.SCREENED:
            raise WorkflowPrerequisiteError(
                "Project continuation requires SCREENED after human search review; "
                f"current stage is {project.stage.value}"
            )
        screening_context = self.service.screening_context(project_id)
        active_thread_id = thread_id or uuid.uuid4().hex
        self.runtime_state.register_project(active_thread_id, project_id)
        self.workflow_guard.bind_existing_project(active_thread_id)
        run_logger = self._new_run_logger(
            project.topic,
            project.research_question,
            active_thread_id,
            False,
        )
        config = self.build_config(active_thread_id)
        config["callbacks"] = [run_logger]
        try:
            result = await self.graph.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self.build_continue_prompt(
                                project_id,
                                screening_context,
                            ),
                        }
                    ]
                },
                config=config,
            )
            result["project_status"] = self.service.get_project(project_id).model_dump(
                mode="json"
            )
        except Exception as exc:
            status = {
                "project_status": self.service.get_project(project_id).model_dump(
                    mode="json"
                )
            }
            run_logger.finish("error", result=status, error=str(exc))
            raise AgentExecutionError(exc, project_id) from exc
        summary = run_logger.finish("completed", result=result)
        result["run_log_dir"] = str(run_logger.run_dir)
        result["run_status"] = summary["status"]
        return result

    async def astream(
        self,
        topic: str,
        research_question: str,
        thread_id: str | None = None,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
    ) -> AsyncIterator[dict]:
        if self.graph is None:
            raise AgentUnavailableError(
                self.initialization_error or "Agent graph is unavailable"
            )
        active_thread_id = thread_id or uuid.uuid4().hex
        search_review_options = self._search_review_options(
            min_papers=min_papers,
            max_papers=max_papers,
            max_search_rounds=max_search_rounds,
        )
        self._register_search_review_options(active_thread_id, **search_review_options)
        run_logger = self._new_run_logger(topic, research_question, active_thread_id, False)
        config = self.build_config(active_thread_id)
        config["callbacks"] = [run_logger]
        try:
            async for event in self.graph.astream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self.build_prompt(
                                topic,
                                research_question,
                                **search_review_options,
                            ),
                        }
                    ]
                },
                config=config,
                stream_mode="updates",
            ):
                yield event
        except Exception as exc:
            run_logger.finish(
                "error",
                result=self._status_result(run_logger),
                error=str(exc),
            )
            raise AgentExecutionError(exc, run_logger.project_id) from exc
        finally:
            self._search_review_options_by_thread.pop(active_thread_id, None)
        result = {}
        active_project_id = run_logger.project_id or self.runtime_state.project_id(
            active_thread_id
        )
        if active_project_id:
            project_status = self.service.get_project(active_project_id).model_dump(
                mode="json"
            )
            result["project_status"] = project_status
            if project_status.get("stage") == "SEARCH_REVIEW_PENDING":
                yield {
                    "type": "awaiting_input",
                    "data": self.search_review.get_review(active_project_id),
                }
        run_logger.finish("completed", result=result)

    def invoke_with_fallback(
        self,
        topic: str,
        research_question: str,
        thread_id: str | None = None,
        show_progress: bool = False,
    ) -> dict:
        active_thread_id = thread_id or uuid.uuid4().hex
        run_logger = self._new_run_logger(
            topic,
            research_question,
            active_thread_id,
            show_progress,
        )
        try:
            result = self._invoke_graph(
                topic,
                research_question,
                active_thread_id,
                run_logger,
            )
        except Exception as exc:
            if not self.settings.enable_fallback or not self.should_fallback(exc):
                run_logger.finish(
                    "error",
                    result=self._status_result(run_logger),
                    error=str(exc),
                )
                raise
            fallback = self.fallback.run(
                topic,
                research_question,
                reason=str(exc),
                project_id=run_logger.project_id,
            )
            run_logger.emit("run.fallback", f"已进入离线降级：{exc}")
            run_logger.finish("fallback", result=fallback, error=str(exc))
            return {**fallback, "run_log_dir": str(run_logger.run_dir)}
        summary = run_logger.finish("completed", result=result)
        return {
            "mode": "agent",
            "status": summary["status"],
            "result": result,
            "run_log_dir": str(run_logger.run_dir),
        }

    @staticmethod
    def should_fallback(error: BaseException) -> bool:
        """Only availability failures may enter the offline fallback path."""
        if isinstance(error, AgentExecutionError):
            error = error.original_error
        return isinstance(error, FALLBACK_EXCEPTIONS)


def build_research_agent(settings: Settings | None = None):
    """Compatibility factory returning the compiled Deep Agents graph."""
    supervisor = ResearchSupervisor(settings)
    if supervisor.graph is None:
        raise RuntimeError(supervisor.initialization_error or "Agent graph is unavailable")
    return supervisor.graph
