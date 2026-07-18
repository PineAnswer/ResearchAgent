from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from csv import DictReader
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
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
from research_agent.application.search_review import SearchReviewService
from research_agent.domain.models import (
    LibraryAgentResponse,
    LibraryPaperAnalysis,
)
from research_agent.infrastructure.artifact_exporter import JsonArtifactExporter
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.run_logger import ResearchRunLogger
from research_agent.infrastructure.observable_chat_model import ObservableChatOpenAI
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.infrastructure.workspace import WorkspaceBootstrapper
from research_agent.tools.library_tools import build_library_tools
from research_agent.tools.literature_tools import build_literature_tools, extract_pdf_pages
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
    BotoCoreError,
    ClientError,
    httpx.NetworkError,
    httpx.TimeoutException,
    URLError,
    ConnectionError,
    TimeoutError,
)


LIBRARY_READER_PROMPT = """
你是文献库 AI 精读助手。只能依据输入的逐页 PDF 原文生成结构化精读卡。
提取研究方法、数据集、主要结论、局限和关键词。每条 finding 必须包含：
1. 简洁、忠于原文的 claim；
2. 从同一页逐字复制的 quote，不得改写；
3. quote 所在的真实 page 页码；
4. 可识别时填写 section。
无法由输入原文支持的内容不要输出，不得利用外部知识补全。
""".strip()


LIBRARY_AGENT_PROMPT = """
你是可调用工具的 Ask Library Agent。你必须先拆解问题，再迭代检索文献库，必要时读取
单篇上下文或追加更精确的段落检索。只允许依据工具返回的文献库材料回答，禁止使用外部
知识补全事实。

工作规则：
1. 先调用 search_library 判断相关论文和覆盖情况；不能直接作答。
2. 再调用 retrieve_library_passages 获取能够支撑答案的原文，复杂问题可换角度迭代检索。
3. 需要论文整体方法、局限或历史证据时调用 get_library_paper_context。
4. cited_source_ids 只能填写工具结果中真实出现的 source_id，按首次使用顺序排列。
5. answer 中每个事实性结论后必须写 [[source_id]]；没有来源支撑就明确说材料不足。
6. 不得把论文标题、DOI、library_id 或自行编造的编号当作 source_id。
7. coverage_note 说明已覆盖的范围和仍缺失的证据。
""".strip()


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
        self.library_toolset = build_library_tools(self.service.library)
        self.library_tools = self.library_toolset.tools
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
        provider, _, model_name = self.settings.model.partition(":")
        if provider.lower() == "bedrock":
            return self._build_bedrock_model(model_name or self.settings.model)

        if not self.settings.base_url:
            return self.settings.model

        return ObservableChatOpenAI(
            model=model_name,
            api_key=os.getenv("OPENAI_API_KEY", "not-set"),
            base_url=self.settings.base_url,
        )

    def _build_bedrock_model(self, model_name: str):
        from langchain_aws import ChatBedrockConverse

        kwargs: dict[str, Any] = {
            "model": model_name,
            "region_name": self.settings.aws_region or "us-east-1",
            "temperature": 0,
            "max_tokens": 4096,
        }
        if self.settings.aws_profile:
            kwargs["credentials_profile_name"] = self.settings.aws_profile
        kwargs.update(self._load_aws_credentials_from_csv(self.settings.aws_credentials_csv))
        return ChatBedrockConverse(**kwargs)

    @staticmethod
    def _load_aws_credentials_from_csv(csv_path: Path | None) -> dict[str, str]:
        if csv_path is None:
            return {}
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            row = next(DictReader(handle), None)
        if not row:
            raise ValueError(f"AWS credentials CSV is empty: {csv_path}")

        access_key = (
            row.get("Access key ID")
            or row.get("Access key id")
            or row.get("AWS Access Key ID")
            or row.get("aws_access_key_id")
        )
        secret_key = (
            row.get("Secret access key")
            or row.get("Secret Access Key")
            or row.get("AWS Secret Access Key")
            or row.get("aws_secret_access_key")
        )
        session_token = row.get("Session token") or row.get("AWS Session Token")
        if not access_key or not secret_key:
            raise ValueError(
                "AWS credentials CSV must include Access key ID and Secret access key columns"
            )

        credentials = {
            "aws_access_key_id": access_key.strip(),
            "aws_secret_access_key": secret_key.strip(),
        }
        if session_token:
            credentials["aws_session_token"] = session_token.strip()
        return credentials

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
        all_tools = [*project_tools, *self.literature_tools, *self.library_tools]
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
            "search_library",
            "retrieve_library_passages",
            "get_library_paper_context",
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
    def build_narrative_continue_prompt(
        project_id: str,
        narrative_context: dict[str, Any],
    ) -> str:
        return (
            f"继续已有科研项目：{project_id}\n"
            "该项目已完成论文精读、证据综合和PASS证据审查。"
            "禁止创建新项目、重新检索、重新筛选、重新精读、重新综合或重新审查。\n"
            "从 narrative_context.current_stage 继续综述写作："
            "REVIEWED先生成提纲；OUTLINED只补写尚未保存的SectionDraft再交给chief-editor；"
            "NARRATED只核查尚未保存FactCheckReport的章节。"
            "必须复用已保存产物并跳过context中列出的已完成章节。"
            "仅当NarrativeReview的每一节都有FactCheckReport后，才调用"
            "advance_project_stage推进到COMPLETED。\n\n"
            "narrative_context:\n"
            f"{json.dumps(narrative_context, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def build_config(thread_id: str | None = None) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id or uuid.uuid4().hex},
            "recursion_limit": 160,
        }

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
        """Continue a persisted project from screening or an interrupted writing stage."""
        if self.graph is None:
            raise AgentUnavailableError(
                self.initialization_error or "Agent graph is unavailable"
            )
        continuation = self.service.prepare_continuation(project_id)
        project = continuation["project"]
        if continuation["mode"] == "screening":
            continue_prompt = self.build_continue_prompt(
                project_id,
                continuation["context"],
            )
        else:
            continue_prompt = self.build_narrative_continue_prompt(
                project_id,
                continuation["context"],
            )
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
                            "content": continue_prompt,
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

    def _library_attachment_path(self, attachment_id: str) -> Path:
        attachment = self.repository.get_library_attachment(attachment_id)
        root = (Path(self.settings.data_dir) / "library-attachments").resolve()
        expected = (root / attachment.library_id / attachment.attachment_id).resolve()
        if expected.is_relative_to(root) and expected.is_file():
            return expected
        if root.is_dir():
            for candidate in root.glob(f"*/{attachment.attachment_id}"):
                resolved = candidate.resolve()
                if resolved.is_relative_to(root) and resolved.is_file():
                    return resolved
        raise FileNotFoundError(attachment_id)

    @staticmethod
    def _extractive_library_analysis(
        paper: dict[str, Any],
        chunks: list[Any],
        reason: str = "",
    ) -> LibraryPaperAnalysis:
        summary = str(paper.get("abstract") or "").strip()
        if not summary and chunks:
            summary = str(chunks[0].text).strip()[:1200]
        limitations = ["当前精读卡由本地文本索引生成，尚未完成模型结构化抽取。"]
        if reason:
            limitations.append(f"AI 精读不可用：{reason[:300]}")
        return LibraryPaperAnalysis(
            summary=summary,
            limitations=limitations,
        )

    @staticmethod
    def _ground_library_analysis(
        analysis: LibraryPaperAnalysis,
        pages: list[dict[str, Any]],
    ) -> LibraryPaperAnalysis:
        page_text = {
            int(item["page"]): re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            for item in pages
            if item.get("page") is not None
        }
        grounded = []
        for finding in analysis.findings:
            if finding.page is None or not finding.quote.strip():
                continue
            source = page_text.get(int(finding.page), "").casefold()
            quote = re.sub(r"\s+", " ", finding.quote).strip().casefold()
            if quote and quote in source:
                grounded.append(finding)
        return analysis.model_copy(update={"findings": grounded})

    async def _generate_library_analysis(
        self,
        paper: dict[str, Any],
        pages: list[dict[str, Any]],
        chunks: list[Any],
    ) -> tuple[LibraryPaperAnalysis, str]:
        fallback = self._extractive_library_analysis(paper, chunks)
        if self.graph is None or not chunks:
            return fallback, "extractive"
        source_parts: list[str] = []
        total = 0
        for item in pages:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            block = f"\n\n--- PAGE {item.get('page')} ---\n{text}"
            if total + len(block) > 60_000:
                break
            source_parts.append(block)
            total += len(block)
        if not source_parts:
            return fallback, "extractive"
        try:
            model = self._build_model()
            if isinstance(model, str):
                model = init_chat_model(model)
            structured_model = model.with_structured_output(LibraryPaperAnalysis)
            result = await structured_model.ainvoke(
                [
                    SystemMessage(content=LIBRARY_READER_PROMPT),
                    HumanMessage(
                        content=(
                            f"论文标题：{paper.get('title', '')}\n"
                            f"已有摘要：{paper.get('abstract', '')}\n"
                            "以下是带真实页码的 PDF 原文："
                            + "".join(source_parts)
                        )
                    ),
                ]
            )
            analysis = LibraryPaperAnalysis.model_validate(result)
            return self._ground_library_analysis(analysis, pages), "agent"
        except Exception as exc:
            return self._extractive_library_analysis(paper, chunks, str(exc)), "extractive"

    async def ingest_library_attachment(self, attachment_id: str) -> dict[str, Any]:
        """Extract, index, and structure one uploaded library PDF."""
        attachment = self.repository.get_library_attachment(attachment_id)
        paper = self.repository.get_library_paper(attachment.library_id)
        attachment = attachment.model_copy(deep=True)
        attachment.full_text_status = "extracting"
        attachment.error = ""
        attachment.updated_at = datetime.now(UTC)
        await asyncio.to_thread(self.repository.save_library_attachment, attachment)
        try:
            path = await asyncio.to_thread(self._library_attachment_path, attachment_id)
            pages = await asyncio.to_thread(extract_pdf_pages, path, 100)
            if not any(str(item.get("text") or "").strip() for item in pages):
                raise ValueError("PDF 中没有可提取文本，可能是扫描版或文件损坏")
            chunks = await asyncio.to_thread(
                self.service.library.index_attachment_pages,
                paper.library_id,
                attachment_id,
                pages,
            )
            analysis, mode = await self._generate_library_analysis(
                paper.model_dump(mode="json"),
                pages,
                chunks,
            )
            artifact = await asyncio.to_thread(
                self.service.library.save_paper_analysis,
                paper.library_id,
                attachment_id,
                analysis,
                mode=mode,
            )
            attachment.full_text_status = "indexed"
            attachment.page_count = len(pages)
            attachment.chunk_count = len(chunks)
            attachment.error = ""
            attachment.updated_at = datetime.now(UTC)
            await asyncio.to_thread(self.repository.save_library_attachment, attachment)
            return {
                "attachment": attachment.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
                "artifact": artifact.model_dump(mode="json"),
                "mode": mode,
            }
        except Exception as exc:
            attachment.full_text_status = "failed"
            attachment.error = str(exc)[:1000]
            attachment.updated_at = datetime.now(UTC)
            await asyncio.to_thread(self.repository.save_library_attachment, attachment)
            return {
                "attachment": attachment.model_dump(mode="json"),
                "analysis": None,
                "artifact": None,
                "mode": "failed",
            }

    @staticmethod
    def _library_answer_is_traceable(answer: str, source_ids: set[str]) -> bool:
        factual_lines = []
        for raw_line in answer.splitlines():
            line = re.sub(r"^\s*(?:#{1,6}|[-*+] |\d+[.)]\s*)", "", raw_line).strip()
            if len(line) < 8 or line.endswith(("：", ":")):
                continue
            if any(
                phrase in line
                for phrase in ("材料不足", "证据不足", "未检索到", "无法回答")
            ):
                continue
            factual_lines.append(line)
        return bool(factual_lines) and all(
            any(f"[[{source_id}]]" in line for source_id in source_ids)
            for line in factual_lines
        )

    async def answer_library_question(
        self,
        library_ids: list[str],
        question: str,
    ) -> dict[str, Any]:
        """Run a scoped, tool-using Agent over selected papers or the full library."""
        fallback = self.service.library.answer_library_question(library_ids, question)
        if self.graph is None:
            return {**fallback, "mode": "extractive"}
        toolset = build_library_tools(
            self.service.library,
            allowed_library_ids=library_ids or None,
        )
        try:
            model = self._build_model()
            if isinstance(model, str):
                model = init_chat_model(model)
            agent = create_agent(
                model=model,
                tools=toolset.tools,
                system_prompt=LIBRARY_AGENT_PROMPT,
                response_format=LibraryAgentResponse.model_json_schema(),
                middleware=[
                    SerialToolExecutionMiddleware(),
                    ModelCallLimitMiddleware(run_limit=10, exit_behavior="end"),
                    ToolCallLimitMiddleware(
                        tool_name="search_library",
                        run_limit=3,
                        exit_behavior="end",
                    ),
                    ToolCallLimitMiddleware(
                        tool_name="retrieve_library_passages",
                        run_limit=5,
                        exit_behavior="end",
                    ),
                    ToolCallLimitMiddleware(
                        tool_name="get_library_paper_context",
                        run_limit=4,
                        exit_behavior="end",
                    ),
                ],
                name="library-research-agent",
            )
            result = await agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                f"用户问题：{question.strip()}\n"
                                + (
                                    f"检索范围仅限这些 library_id：{library_ids}"
                                    if library_ids
                                    else "检索范围：整个文献库"
                                )
                            )
                        )
                    ]
                }
            )
            structured = result.get("structured_response") if isinstance(result, dict) else None
            response = LibraryAgentResponse.model_validate(structured)
            cited_ids = list(dict.fromkeys(response.cited_source_ids))
            answer_markers = set(re.findall(r"\[\[([^\]]+)\]\]", response.answer))
            valid_ids = [
                item
                for item in cited_ids
                if item in toolset.source_registry and item in answer_markers
            ]
            if answer_markers - set(valid_ids):
                return {**fallback, "mode": "extractive"}
            if (
                not response.answer.strip()
                or not valid_ids
                or not self._library_answer_is_traceable(
                    response.answer,
                    set(valid_ids),
                )
            ):
                return {**fallback, "mode": "extractive"}
            citations: list[dict[str, Any]] = []
            answer = response.answer.strip()
            for index, source_id in enumerate(valid_ids, start=1):
                source = toolset.source_registry[source_id]
                answer = answer.replace(f"[[{source_id}]]", f"[{index}]")
                quote = str(source.get("text") or "").strip()
                citations.append(
                    {
                        "citation": f"[{index}]",
                        "source_id": source_id,
                        "source_type": source.get("source_type"),
                        "library_id": source.get("library_id"),
                        "title": source.get("title"),
                        "page": source.get("page"),
                        "attachment_id": source.get("attachment_id"),
                        "quote": quote[:800],
                    }
                )
            return {
                "question": question.strip(),
                "answer": answer,
                "citations": citations,
                "used_library_ids": response.used_library_ids,
                "coverage_note": response.coverage_note,
                "mode": "agent",
            }
        except Exception as exc:
            return {
                **fallback,
                "mode": "extractive",
                "coverage_note": f"Ask Library Agent 不可用，已返回本地检索结果：{str(exc)[:240]}",
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
