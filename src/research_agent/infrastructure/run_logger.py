from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


_SENSITIVE_LOG_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "cookie",
    "access_token",
    "refresh_token",
)


def _redact_log_value(value: Any) -> Any:
    value = _json_safe(value)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(marker in key.casefold() for marker in _SENSITIVE_LOG_KEYS)
                else _redact_log_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+",
            "Bearer [REDACTED]",
            value,
        )
    return value


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _tool_name(serialized: dict[str, Any]) -> str:
    name = serialized.get("name")
    if isinstance(name, str) and name:
        return name
    identifier = serialized.get("id")
    if isinstance(identifier, list) and identifier:
        return str(identifier[-1])
    return "unknown-tool"


def _message_diagnostics(response: Any) -> dict[str, Any]:
    """Extract tool-call details that LLMResult serialization can omit."""
    generations = getattr(response, "generations", [])
    generation = generations[0][0] if generations and generations[0] else None
    message = getattr(generation, "message", None)
    generation_info = getattr(generation, "generation_info", None) or {}
    response_metadata = (
        getattr(message, "response_metadata", None) or {} if message is not None else {}
    )
    tool_calls = getattr(message, "tool_calls", None) or [] if message is not None else []
    invalid_tool_calls = (
        getattr(message, "invalid_tool_calls", None) or [] if message is not None else []
    )
    additional_kwargs = (
        getattr(message, "additional_kwargs", None) or {} if message is not None else {}
    )
    finish_reason = response_metadata.get("finish_reason") or generation_info.get(
        "finish_reason"
    )
    raw_provider_response = generation_info.get("raw_provider_response")
    parse_status = "parsed_tool_calls" if tool_calls else "no_tool_calls"
    if invalid_tool_calls:
        parse_status = "invalid_tool_calls"
    elif finish_reason == "tool_calls" and not tool_calls:
        parse_status = "tool_call_finish_without_parsed_calls"
    return {
        "content": getattr(message, "content", None) if message is not None else None,
        "tool_calls": tool_calls,
        "invalid_tool_calls": invalid_tool_calls,
        "additional_kwargs": additional_kwargs,
        "response_metadata": _redact_log_value(response_metadata),
        "generation_info": _redact_log_value(generation_info),
        "finish_reason": finish_reason,
        "parse_status": parse_status,
        "raw_provider_response_captured": raw_provider_response is not None,
        "raw_provider_response": _redact_log_value(raw_provider_response),
    }


class ResearchRunLogger(BaseCallbackHandler):
    """LangChain callback that prints progress and writes a complete run transcript."""

    def __init__(
        self,
        runs_root: str | Path,
        topic: str,
        research_question: str,
        thread_id: str,
        console: bool = False,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        self.run_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
        self.run_dir = Path(runs_root).resolve() / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.messages_path = self.run_dir / "messages.jsonl"
        self.result_path = self.run_dir / "final-result.json"
        self.run_path = self.run_dir / "run.json"
        self.report_path = self.run_dir / "final-report.md"
        self.console = console
        self.event_sink = event_sink
        self._lock = threading.RLock()
        self._tool_names: dict[str, str] = {}
        self._tool_inputs: dict[str, dict[str, Any]] = {}
        self._paper_total: int | None = None
        self._paper_completed = 0
        self._paper_order: dict[str, int] = {}
        self._paper_attempts: dict[str, int] = {}
        self._search_round = 0
        self.project_id: str | None = None
        self._run_record = {
            "run_id": self.run_id,
            "thread_id": thread_id,
            "topic": topic,
            "research_question": research_question,
            "started_at": _now(),
            "status": "running",
        }
        self._write_json(self.run_path, self._run_record)
        self.emit("run.started", "科研任务开始", {"thread_id": thread_id})
        if self.console:
            self._console(f"[日志] {self.run_dir}")

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock, path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(_json_safe(payload), ensure_ascii=False, default=str))
            handle.write("\n")

    @staticmethod
    def _console(message: str) -> None:
        try:
            print(message, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(safe, flush=True)

    def emit(self, event_type: str, message: str, data: Any = None) -> None:
        record = {
            "timestamp": _now(),
            "type": event_type,
            "message": message,
            "data": _json_safe(data),
        }
        self._append_jsonl(self.events_path, record)
        if self.event_sink is not None:
            try:
                self.event_sink(dict(record))
            except Exception:
                # Observability must never interrupt the research workflow.
                pass
        if self.console:
            self._console(f"[进度] {message}")

    def transcript(self, event_type: str, data: Any) -> None:
        self._append_jsonl(
            self.messages_path,
            {"timestamp": _now(), "type": event_type, "data": _json_safe(data)},
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        self.transcript(
            "llm.request",
            {
                "model": serialized,
                "messages": messages,
                "run_id": kwargs.get("run_id"),
                "parent_run_id": kwargs.get("parent_run_id"),
            },
        )
        self.emit("llm.thinking", "LLM正在分析当前上下文")

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        diagnostics = _message_diagnostics(response)
        self.transcript(
            "llm.response",
            {
                "response": _redact_log_value(response),
                "message_diagnostics": diagnostics,
                "run_id": kwargs.get("run_id"),
                "parent_run_id": kwargs.get("parent_run_id"),
            },
        )
        generations = getattr(response, "generations", [])
        message = None
        if generations and generations[0]:
            message = getattr(generations[0][0], "message", None)
        tool_calls = getattr(message, "tool_calls", []) if message is not None else []
        if tool_calls:
            names = [str(item.get("name", "unknown")) for item in tool_calls]
            if len(names) > 1:
                self.emit(
                    "llm.tool_choice_batch",
                    f"LLM拟调用{len(names)}个工具；系统将尝试串行执行第一个：{names[0]}",
                    tool_calls,
                )
            else:
                self.emit(
                    "llm.tool_choice",
                    f"LLM拟调用：{names[0]}（是否执行以tool/search.started事件为准）",
                    tool_calls,
                )
            return
        if diagnostics["invalid_tool_calls"]:
            self.emit(
                "llm.invalid_tool_calls",
                "LLM返回了无法解析的工具调用",
                diagnostics,
            )
            return
        if diagnostics["parse_status"] == "tool_call_finish_without_parsed_calls":
            self.emit(
                "llm.tool_call_parse_gap",
                "模型声明工具调用，但LangChain未解析出tool_calls",
                diagnostics,
            )
            return
        content = getattr(message, "content", "") if message is not None else ""
        if content:
            preview = str(content).replace("\n", " ")[:240]
            self.emit("llm.reply", f"LLM回复：{preview}", {"content": content})

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self.transcript("llm.error", {"error": repr(error), **kwargs})
        self.emit("llm.error", f"LLM调用失败：{error}")

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        name = _tool_name(serialized)
        run_id = str(kwargs.get("run_id"))
        inputs = kwargs.get("inputs")
        normalized_inputs = inputs if isinstance(inputs, dict) else _parse_json(input_str)
        if not isinstance(normalized_inputs, dict):
            normalized_inputs = {"input": normalized_inputs}
        self._tool_names[run_id] = name
        self._tool_inputs[run_id] = normalized_inputs
        self.transcript(
            "tool.request",
            {
                "tool": name,
                "inputs": normalized_inputs,
                "run_id": run_id,
                "parent_run_id": kwargs.get("parent_run_id"),
            },
        )
        self._report_tool_start(name, normalized_inputs)

    def _report_tool_start(self, name: str, inputs: dict[str, Any]) -> None:
        if name in {
            "search_openalex",
            "search_crossref",
            "search_semantic_scholar",
            "search_arxiv",
            "search_multi_source",
        }:
            source_names = {
                "search_openalex": "OpenAlex",
                "search_crossref": "Crossref",
                "search_semantic_scholar": "Semantic Scholar",
                "search_arxiv": "arXiv",
                "search_multi_source": "OpenAlex、Crossref、Semantic Scholar 和 arXiv",
            }
            query = (
                " | ".join(str(item) for item in inputs.get("queries", []))
                if name == "search_multi_source"
                else inputs.get("query", "")
            )
            if name == "search_multi_source":
                self._search_round += 1
                inputs["_search_round"] = self._search_round
                queries = [str(item) for item in inputs.get("queries", []) if str(item)]
                self.emit(
                    "search.started",
                    f"第{self._search_round}轮检索开始，共{len(queries)}组检索词",
                    {
                        "scope": "portfolio",
                        "round": self._search_round,
                        "queries": queries,
                        "sources": ["OpenAlex", "Crossref", "Semantic Scholar", "arXiv"],
                    },
                )
                return
            self.emit(
                "search.started",
                f"正在使用{source_names[name]}搜索：{query}",
                {"scope": "source", "source": source_names[name], "query": query},
            )
            return
        if name == "task":
            subagent = inputs.get("subagent_type", "子Agent")
            if subagent == "paper-reader":
                description = str(inputs.get("description", ""))
                id_match = re.search(
                    r"paper_id[^:\n]*:\s*[\"']?([^\s\"']+)",
                    description,
                    flags=re.IGNORECASE,
                )
                title_match = re.search(r"(?:^|\n)\s*-?\s*title\s*:\s*(.+)", description)
                paper_id = id_match.group(1).strip() if id_match else "unknown-paper"
                title = title_match.group(1).strip() if title_match else paper_id
                attempt = self._paper_attempts.get(paper_id, 0) + 1
                self._paper_attempts[paper_id] = attempt
                index = self._paper_order.get(paper_id)
                ordinal = f"第{index}/{self._paper_total}篇" if index else "论文"
                self.emit(
                    "paper.started",
                    f"正在处理{ordinal}：《{title}》（尝试{attempt}）",
                    {"paper_id": paper_id, "title": title, "attempt": attempt},
                )
            else:
                self.emit("subagent.started", f"正在委派{subagent}")
            return
        if name in {"transition_project_stage", "advance_project_stage"}:
            target = inputs.get("target_stage", "未知阶段")
            self.emit("stage.transition", f"正在推进科研阶段：{target}")
            return
        if name == "finish_inconclusive":
            self.emit("run.inconclusive", "正在以证据不足状态结束科研任务")
            return
        if name == "fetch_paper_text":
            self.emit(
                "pdf.fetch_started",
                f"正在获取开放全文：{inputs.get('paper_id', '')}",
                inputs,
            )
            return
        if name == "commit_subagent_result":
            subagent = str(inputs.get("subagent_type", "subagent"))
            self.emit("artifact.committing", f"正在原样提交{subagent}结构化结果")
            return
        if name == "save_screening_decision":
            included = inputs.get("included_paper_ids", [])
            self._paper_total = len(included) if isinstance(included, list) else None
            if isinstance(included, list):
                self._paper_order = {str(item): index for index, item in enumerate(included, 1)}
            self.emit(
                "screening.saving",
                f"正在保存筛选结果，入选{self._paper_total or 0}篇论文",
            )
            return
        if name == "save_project_artifact":
            kind = str(inputs.get("kind", "Artifact"))
            payload = _parse_json(inputs.get("payload_json", {}))
            if kind == "ScreeningDecision" and isinstance(payload, dict):
                included = payload.get("included_paper_ids", [])
                self._paper_total = len(included) if isinstance(included, list) else None
                if isinstance(included, list):
                    self._paper_order = {
                        str(item): index for index, item in enumerate(included, 1)
                    }
                self.emit(
                    "screening.saving",
                    f"正在保存筛选结果，入选{self._paper_total or 0}篇论文",
                )
            else:
                self.emit("artifact.saving", f"正在保存产物：{kind}")
            return
        if name == "save_artifact_and_transition":
            kind = str(inputs.get("kind", "Artifact"))
            target = str(inputs.get("target_stage", "未知阶段"))
            payload = _parse_json(inputs.get("payload_json", {}))
            if kind == "ScreeningDecision" and isinstance(payload, dict):
                included = payload.get("included_paper_ids", [])
                self._paper_total = len(included) if isinstance(included, list) else None
            self.emit(
                "artifact.committing",
                f"正在校验并提交{kind}，成功后阶段更新为{target}",
            )
            return
        if name == "save_paper_card":
            self.emit("artifact.saving", "正在保存PaperCard")
            return
        self.emit("tool.started", f"正在调用工具：{name}")

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id"))
        name = self._tool_names.pop(run_id, "unknown-tool")
        inputs = self._tool_inputs.pop(run_id, {})
        self.transcript(
            "tool.response",
            {
                "tool": name,
                "output": output,
                "run_id": run_id,
                "parent_run_id": kwargs.get("parent_run_id"),
            },
        )
        self._report_tool_end(name, inputs, output)

    def _report_tool_end(self, name: str, inputs: dict[str, Any], output: Any) -> None:
        parsed = _parse_json(getattr(output, "content", output))
        if name == "create_research_project" and isinstance(parsed, dict):
            project_id = parsed.get("project_id", "未知项目")
            self.project_id = str(project_id)
            output_dir = self.run_dir.parent.parent / "outputs" / str(project_id)
            self.emit(
                "project.created",
                f"项目已创建：{project_id}；导出目录：{output_dir}",
                parsed,
            )
            return
        if (
            name in {"get_research_project", "get_active_research_project"}
            and isinstance(parsed, dict)
            and parsed.get("ok") is False
        ):
            error_code = str(parsed.get("error_code", "project_lookup_failed"))
            self.emit(
                "project.lookup_failed",
                f"项目读取未成功：{error_code}",
                parsed,
            )
            return
        if (
            name
            in {
                "search_openalex",
                "search_crossref",
                "search_semantic_scholar",
                "search_arxiv",
            }
            and isinstance(parsed, dict)
            and parsed.get("ok") is False
        ):
            source = {
                "search_openalex": "OpenAlex",
                "search_crossref": "Crossref",
                "search_semantic_scholar": "Semantic Scholar",
                "search_arxiv": "arXiv",
            }[name]
            error_code = str(parsed.get("error_code", "search_error"))
            attempts = parsed.get("attempts", 1)
            status_code = parsed.get("status_code")
            event_type = (
                "search.rate_limited" if error_code == "rate_limited" else "search.failed"
            )
            self.emit(
                event_type,
                f"{source}检索未成功：{error_code}；尝试{attempts}次；HTTP {status_code}",
                parsed,
            )
            return
        if name in {
            "search_openalex",
            "search_crossref",
            "search_semantic_scholar",
            "search_arxiv",
        } and isinstance(parsed, list):
            source = {
                "search_openalex": "OpenAlex",
                "search_crossref": "Crossref",
                "search_semantic_scholar": "Semantic Scholar",
                "search_arxiv": "arXiv",
            }[name]
            titles = [str(item.get("title", "无标题")) for item in parsed if isinstance(item, dict)]
            self.emit(
                "search.results",
                f"{source}返回{len(parsed)}篇：" + "；".join(titles[:8]),
                {"count": len(parsed), "papers": parsed},
            )
            return
        if name == "search_multi_source" and isinstance(parsed, dict):
            papers = parsed.get("candidates", [])
            statuses = parsed.get("source_status", [])
            search_round = int(inputs.get("_search_round") or self._search_round or 1)
            failure_count = sum(
                1
                for status in statuses
                if isinstance(status, dict) and status.get("ok") is False
            )
            self.emit(
                "search.results",
                f"第{search_round}轮完成：合并去重后得到{len(papers)}篇候选论文",
                {
                    "scope": "portfolio",
                    "round": search_round,
                    "queries": parsed.get("queries", inputs.get("queries", [])),
                    "count": len(papers),
                    "partial_failures": failure_count,
                    "papers": papers,
                    "source_status": statuses,
                },
            )
            self.emit(
                "search.synthesizing",
                f"正在综合第{search_round}轮结果并分析覆盖盲区",
                {
                    "scope": "portfolio",
                    "round": search_round,
                    "candidate_count": len(papers),
                },
            )
            return
        if name == "fetch_paper_text" and isinstance(parsed, dict):
            if parsed.get("available") is True:
                self.emit(
                    "pdf.fetched",
                    f"开放全文获取并提取成功，共{len(parsed.get('pages', []))}页",
                    {
                        "source_url": parsed.get("source_url"),
                        "local_pdf_path": parsed.get("local_pdf_path"),
                        "page_count": len(parsed.get("pages", [])),
                    },
                )
            else:
                self.emit(
                    "pdf.unavailable",
                    f"开放全文不可用：{parsed.get('error_code', 'unknown')}",
                    parsed,
                )
            return
        if name == "save_screening_decision" and isinstance(parsed, dict):
            if parsed.get("ok") is False:
                self.emit("screening.save_failed", "筛选结果保存失败", parsed)
            else:
                stage = parsed.get("project", {}).get("stage", "SCREENED")
                self.emit(
                    "screening.completed",
                    f"筛选结果已保存，入选{self._paper_total or 0}篇；阶段更新为{stage}",
                )
            return
        if name == "commit_subagent_result" and isinstance(parsed, dict):
            if parsed.get("ok") is False:
                self.emit("artifact.commit_failed", "子Agent结构化结果提交失败", parsed)
                return
            artifact = parsed.get("artifact", {})
            project = parsed.get("project", {})
            kind = str(artifact.get("kind", "Artifact"))
            if kind == "SearchReport":
                payload = artifact.get("payload", {})
                iterations = payload.get("search_iteration_log", [])
                candidates = payload.get("candidates", [])
                self.emit(
                    "search.summary",
                    f"检索综合完成：共{len(iterations) or self._search_round}轮，形成{len(candidates)}篇候选论文",
                    {
                        "scope": "portfolio",
                        "rounds": len(iterations) or self._search_round,
                        "candidate_count": len(candidates),
                        "search_terms": payload.get("search_terms", []),
                        "search_iteration_log": iterations,
                        "coverage_gaps": payload.get("coverage_gaps", []),
                    },
                )
                return
            if kind == "PaperCard":
                self._paper_completed += 1
                payload = artifact.get("payload", {})
                title = payload.get("title", "无标题")
                evidence_count = len(payload.get("findings", []))
                suffix = f"/{self._paper_total}" if self._paper_total else ""
                self.emit(
                    "paper.completed",
                    f"第{self._paper_completed}{suffix}篇完成：《{title}》；提取{evidence_count}条证据",
                )
            else:
                self.emit(
                    "artifact.committed",
                    f"{kind}已原样保存；阶段更新为{project.get('stage', '未知')}",
                )
            return
        if name == "save_project_artifact":
            kind = str(inputs.get("kind", "Artifact"))
            if kind == "PaperCard":
                payload = _parse_json(inputs.get("payload_json", {}))
                self._paper_completed += 1
                suffix = f"/{self._paper_total}" if self._paper_total else ""
                title = payload.get("title", "无标题") if isinstance(payload, dict) else "无标题"
                self.emit(
                    "paper.completed",
                    f"第{self._paper_completed}{suffix}篇处理完成：《{title}》",
                )
            elif kind == "ScreeningDecision":
                self.emit(
                    "screening.completed",
                    f"筛选结果已保存，入选{self._paper_total or 0}篇论文",
                )
            else:
                self.emit("artifact.saved", f"产物已保存并导出：{kind}")
            return
        if (
            name == "save_artifact_and_transition"
            and isinstance(parsed, dict)
            and parsed.get("ok") is False
        ):
            error_code = str(parsed.get("error_code", "artifact_commit_rejected"))
            self.emit(
                "artifact.commit_failed",
                f"产物提交被拒绝：{error_code}",
                parsed,
            )
            return
        if name == "save_artifact_and_transition" and isinstance(parsed, dict):
            artifact = parsed.get("artifact", {})
            project = parsed.get("project", {})
            kind = artifact.get("kind", inputs.get("kind", "Artifact"))
            stage = project.get("stage", inputs.get("target_stage", "未知阶段"))
            if kind == "ScreeningDecision":
                self.emit(
                    "screening.completed",
                    f"筛选结果已保存，入选{self._paper_total or 0}篇论文；阶段更新为{stage}",
                )
            else:
                self.emit(
                    "artifact.committed",
                    f"{kind}已保存并导出；科研阶段已更新为{stage}",
                )
            return
        if name == "save_paper_card":
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                error_code = str(parsed.get("error_code", "paper_card_save_failed"))
                self.emit(
                    "paper.save_failed",
                    f"PaperCard未保存：{error_code}",
                    parsed,
                )
                return
            payload = _parse_json(inputs.get("payload_json", {}))
            self._paper_completed += 1
            suffix = f"/{self._paper_total}" if self._paper_total else ""
            title = payload.get("title", "无标题") if isinstance(payload, dict) else "无标题"
            self.emit(
                "paper.completed",
                f"第{self._paper_completed}{suffix}篇处理完成：《{title}》",
            )
            return
        if name == "finish_inconclusive" and isinstance(parsed, dict):
            if parsed.get("ok") is False:
                self.emit(
                    "run.inconclusive_failed",
                    "证据不足状态提交失败",
                    parsed,
                )
            else:
                project = parsed.get("project", {})
                self.emit(
                    "run.inconclusive",
                    f"科研任务因证据不足正常结束：{project.get('stage', 'INCONCLUSIVE')}",
                    parsed,
                )
            return
        if (
            name in {"transition_project_stage", "advance_project_stage"}
            and isinstance(parsed, dict)
            and parsed.get("ok") is False
        ):
            self.emit(
                "stage.rejected",
                f"科研阶段推进被拒绝：{parsed.get('error_code', 'stage_transition_rejected')}",
                parsed,
            )
            return
        if name in {"transition_project_stage", "advance_project_stage"} and isinstance(
            parsed, dict
        ):
            self.emit("stage.changed", f"科研阶段已更新为：{parsed.get('stage', '未知')}")
            return
        if name == "extract_pdf_text" and isinstance(parsed, list):
            self.emit("pdf.extracted", f"PDF文本提取完成，共{len(parsed)}页")
            return
        if name == "extract_pdf_text" and isinstance(parsed, dict):
            self.emit(
                "pdf.unavailable",
                f"本地PDF不可用：{parsed.get('error_code', 'unknown')}",
                parsed,
            )
            return
        self.emit("tool.completed", f"工具完成：{name}")

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id"))
        name = self._tool_names.pop(run_id, "unknown-tool")
        self._tool_inputs.pop(run_id, None)
        self.transcript("tool.error", {"tool": name, "error": repr(error), **kwargs})
        self.emit("tool.error", f"工具失败：{name}；{error}")

    @staticmethod
    def _last_ai_content(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        for message in reversed(result.get("messages", [])):
            message_type = (
                message.get("type") if isinstance(message, dict) else getattr(message, "type", "")
            )
            content = (
                message.get("content")
                if isinstance(message, dict)
                else getattr(message, "content", "")
            )
            if message_type == "ai" and isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    @staticmethod
    def _effective_status(
        requested_status: str,
        project_status: dict[str, Any],
        review: dict[str, Any],
    ) -> str:
        if requested_status != "completed":
            return requested_status
        stage = project_status.get("stage")
        verdict = review.get("verdict")
        if stage == "COMPLETED" and verdict == "PASS":
            return "completed"
        if stage == "INCONCLUSIVE":
            return "inconclusive"
        if stage == "SEARCH_REVIEW_PENDING":
            return "awaiting_input"
        if verdict == "REVISE":
            return "needs_revision"
        return "incomplete"

    def _write_report(
        self,
        content: str,
        status: str,
        project_status: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        if not content:
            return None, None
        stage = project_status.get("stage", "UNKNOWN")
        label = "正式完成" if status == "completed" else "运行草稿"
        report = (
            "# 科研 Agent 运行报告\n\n"
            f"> 状态：{status}；科研阶段：{stage}；性质：{label}。\n\n"
            "> 只有状态为 completed、阶段为 COMPLETED 且审查为 PASS 时，"
            "该报告才是正式最终产物。\n\n---\n\n"
            f"{content}\n"
        )
        self.report_path.write_text(report, encoding="utf-8")
        project_report = None
        project_id = project_status.get("project_id") or self.project_id
        if project_id:
            project_root = self.run_dir.parent.parent / "outputs" / str(project_id)
            project_root.mkdir(parents=True, exist_ok=True)
            project_path = project_root / "final-report.md"
            project_path.write_text(report, encoding="utf-8")
            project_report = str(project_path)
        return str(self.report_path), project_report

    def finish(
        self,
        status: str,
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        project_status = result.get("project_status", {}) if isinstance(result, dict) else {}
        review = project_status.get("current_review") or {}
        effective_status = self._effective_status(status, project_status, review)
        if isinstance(result, dict):
            result["run_status"] = effective_status
            self._write_json(self.result_path, result)
        report_file, project_report_file = self._write_report(
            self._last_ai_content(result), effective_status, project_status
        )
        finished_at = _now()
        summary = {
            "run_id": self.run_id,
            "status": effective_status,
            "project_stage": project_status.get("stage"),
            "review_verdict": review.get("verdict"),
            "finished_at": finished_at,
            "error": error,
            "events_file": str(self.events_path),
            "messages_file": str(self.messages_path),
            "result_file": str(self.result_path) if result is not None else None,
            "report_file": report_file,
            "project_report_file": project_report_file,
        }
        self._write_json(self.run_dir / "summary.json", summary)
        self._run_record.update(
            {
                "status": effective_status,
                "finished_at": finished_at,
                "project_id": project_status.get("project_id") or self.project_id,
                "project_stage": project_status.get("stage"),
                "review_verdict": review.get("verdict"),
                "error": error,
            }
        )
        self._write_json(self.run_path, self._run_record)
        if effective_status not in {
            "completed",
            "inconclusive",
            "needs_revision",
            "incomplete",
            "awaiting_input",
        }:
            message = f"科研任务结束：{effective_status}"
        elif project_status.get("stage") == "COMPLETED" and review.get("verdict") == "PASS":
            message = "科研项目已通过审查并完成"
        elif project_status.get("stage") == "INCONCLUSIVE":
            message = "本轮执行结束：证据不足，项目为INCONCLUSIVE"
        elif review.get("verdict") == "REVISE":
            message = "本轮执行结束：审查结果为REVISE，报告需要修订"
        else:
            message = (
                "本轮执行未完成全部科研阶段："
                f"当前为{project_status.get('stage', 'UNKNOWN')}"
            )
        self.emit("run.finished", message, summary)
        return summary
