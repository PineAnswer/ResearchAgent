from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain.tools import ToolRuntime
from pydantic import ValidationError

from research_agent.agents.runtime_state import ResearchRuntimeState, thread_id_from_config
from research_agent.application.research_service import (
    InsufficientEvidenceError,
    ResearchService,
    WorkflowPrerequisiteError,
)
from research_agent.domain.models import ResearchStage, ReviewResult
from research_agent.domain.workflow import InvalidTransition
from research_agent.infrastructure.sqlite_repository import ProjectNotFound


def build_project_tools(
    service: ResearchService,
    runtime_state: ResearchRuntimeState | None = None,
    on_search_committed: Callable[[str, str], dict[str, Any]] | None = None,
):
    from langchain_core.tools import tool

    state = runtime_state or ResearchRuntimeState()

    @tool
    def create_research_project(
        topic: str,
        research_question: str,
        runtime: ToolRuntime,
    ) -> str:
        """Create a research project and return its structured state as JSON."""
        project = service.create_project(topic, research_question)
        state.register_project(
            thread_id_from_config(runtime.config),
            project.project_id,
            user_id=project.user_id,
            conversation_id=project.conversation_id,
        )
        return project.model_dump_json()

    @tool
    def get_research_project(project_id: str) -> str:
        """Read a project by an explicit ID; missing IDs return a recoverable error."""
        try:
            response = service.get_snapshot(project_id)
        except ProjectNotFound:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "project_not_found",
                    "requested_project_id": project_id,
                    "instruction": (
                        "不要猜测或改写project_id；使用创建项目工具返回的原始project_id。"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(response, ensure_ascii=False)

    @tool
    def get_active_research_project(runtime: ToolRuntime) -> str:
        """Read the project bound to this run; no model-supplied project ID is accepted."""
        project_id = state.project_id(thread_id_from_config(runtime.config))
        if project_id is None:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "active_project_unavailable",
                    "instruction": "结束子任务并告知Supervisor当前运行没有绑定项目。",
                },
                ensure_ascii=False,
            )
        try:
            response = service.get_agent_context(project_id)
        except ProjectNotFound:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "active_project_not_found",
                    "project_id": project_id,
                    "instruction": "结束子任务并告知Supervisor当前项目不存在。",
                },
                ensure_ascii=False,
            )
        evidence_catalog = []
        for artifact in response.get("artifacts", []):
            if artifact.get("kind") != "PaperCard":
                continue
            payload = artifact.get("payload", {})
            for finding in payload.get("findings", []):
                evidence_id = finding.get("evidence_id")
                if evidence_id:
                    evidence_catalog.append(
                        {
                            "evidence_id": evidence_id,
                            "paper_id": finding.get("paper_id"),
                            "claim": finding.get("claim"),
                        }
                    )
        response["valid_evidence_ids"] = [
            item["evidence_id"] for item in evidence_catalog
        ]
        response["evidence_catalog"] = evidence_catalog
        response["evidence_reference_rule"] = (
            "SynthesisReport和ReviewResult只能引用valid_evidence_ids中的精确字符串；"
            "limitations、datasets、artifact_id和paper_id均不是evidence_id。"
        )
        return json.dumps(response, ensure_ascii=False)

    @tool
    def save_screening_decision(
        project_id: str,
        included_paper_ids: list[str],
        excluded_paper_ids: list[str],
        reasons: list[str],
        actor: str = "research-supervisor",
    ) -> str:
        """Validate and atomically save ScreeningDecision, then enter SCREENED."""
        try:
            artifact, project = service.save_artifact_and_transition(
                project_id=project_id,
                kind="ScreeningDecision",
                payload={
                    "included_paper_ids": included_paper_ids,
                    "excluded_paper_ids": excluded_paper_ids,
                    "reasons": reasons,
                },
                target=ResearchStage.SCREENED,
                actor=actor,
            )
        except (ValidationError, WorkflowPrerequisiteError, InvalidTransition, ValueError) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "screening_commit_rejected",
                    "message": str(exc),
                    "instruction": "检查候选论文ID和字符串列表字段，修正后最多重试一次。",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "artifact": artifact.model_dump(mode="json"),
                "project": project.model_dump(mode="json"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def commit_subagent_result(
        project_id: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str:
        """Commit the exact structured output returned by the latest subagent.

        Supported subagents are the registered search, reading, synthesis,
        review, outlining, writing, and editing agents. The model does not
        resubmit or reconstruct JSON fields.
        """
        thread_id = thread_id_from_config(runtime.config)
        active_id = state.project_id(thread_id)
        if active_id != project_id:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "active_project_mismatch",
                    "message": f"Active project is {active_id!r}, requested {project_id!r}",
                    "instruction": "使用create_research_project返回的原始project_id。",
                },
                ensure_ascii=False,
            )
        payload = state.pending_result(thread_id, subagent_type)
        if payload is None:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "subagent_result_unavailable",
                    "instruction": f"先串行委派{subagent_type}，再提交其结构化结果。",
                },
                ensure_ascii=False,
            )
        search_review = None
        deterministic_fallback = False
        rejection_scope = None
        if subagent_type == "paper-reader":
            rejection_scope = str(
                payload.get("_paper_id") or payload.get("paper_id") or ""
            ).strip() or None
        if (
            subagent_type == "literature-scout"
            and not payload.get("candidates")
            and state.has_search_source(thread_id, "search_library")
            and state.search_result_count(thread_id, "search_library") == 0
            and not state.has_search_source(thread_id, "search_openalex")
        ):
            rejection_count = state.reject_result(thread_id, subagent_type)
            retry_allowed = rejection_count < 2
            instruction = (
                "本地文献库结果为空，但尚未执行外部学术检索。重新委派"
                "literature-scout一次，并明确告知它本地检索已经完成；首次工具调用"
                "必须是search_openalex，禁止再次调用search_library。"
                if retry_allowed
                else "外部检索仍未执行；停止重试并调用finish_inconclusive。"
            )
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "external_search_required",
                    "message": (
                        "Empty local-library results cannot be committed before "
                        "an OpenAlex search is attempted."
                    ),
                    "rejection_count": rejection_count,
                    "retry_allowed": retry_allowed,
                    "instruction": instruction,
                },
                ensure_ascii=False,
            )
        try:
            if subagent_type == "literature-scout":
                artifact, project = service.save_artifact_and_transition(
                    project_id,
                    "SearchReport",
                    payload,
                    ResearchStage.SEARCHED,
                    actor="literature-scout",
                )
                if payload.get("candidates") and on_search_committed is not None:
                    search_review = on_search_committed(project_id, thread_id)
                    project = service.get_project(project_id)
            elif subagent_type == "paper-reader":
                artifact = service.save_artifact(project_id, "PaperCard", payload)
                project = service.get_project(project_id)
            elif subagent_type == "research-synthesizer":
                artifact, project = service.save_artifact_and_transition(
                    project_id,
                    "SynthesisReport",
                    payload,
                    ResearchStage.SYNTHESIZED,
                    actor="research-synthesizer",
                )
            elif subagent_type == "evidence-reviewer":
                review = ReviewResult.model_validate(payload)
                artifact, project = service.save_artifact_and_transition(
                    project_id,
                    "ReviewResult",
                    payload,
                    ResearchStage.REVIEWED,
                    actor="evidence-reviewer",
                    review=review,
                )
            elif subagent_type == "research-outliner":
                artifact, project = service.save_artifact_and_transition(
                    project_id,
                    "ReviewOutline",
                    payload,
                    ResearchStage.OUTLINED,
                    actor="research-outliner",
                )
            elif subagent_type == "narrative-writer":
                artifact = service.save_artifact(project_id, "SectionDraft", payload)
                project = service.get_project(project_id)
            elif subagent_type == "chief-editor":
                if payload.get("_subagent_error"):
                    artifact, project = service.assemble_narrative_review(project_id)
                    deterministic_fallback = True
                else:
                    try:
                        artifact, project = service.save_artifact_and_transition(
                            project_id,
                            "NarrativeReview",
                            payload,
                            ResearchStage.COMPLETED,
                            actor="chief-editor",
                        )
                    except (ValidationError, WorkflowPrerequisiteError, ValueError):
                        artifact, project = service.assemble_narrative_review(project_id)
                        deterministic_fallback = True
            else:
                raise ValueError(f"Unsupported subagent_type: {subagent_type}")
        except (
            ValidationError,
            WorkflowPrerequisiteError,
            InvalidTransition,
            ProjectNotFound,
            ValueError,
        ) as exc:
            if subagent_type == "paper-reader" and rejection_scope:
                state.reset_paper_fetch(thread_id, rejection_scope)
            rejection_count = state.reject_result(
                thread_id, subagent_type, rejection_scope
            )
            retry_allowed = rejection_count < 2
            if retry_allowed:
                if subagent_type == "literature-scout":
                    instruction = (
                        "该无效检索结果已被系统丢弃。根据message修正任务说明后，"
                        "允许纠正性地重新委派literature-scout一次；只把搜索工具真实"
                        "返回的paper_id或DOI写入candidate_ids，不要复制或改写候选论文"
                        "元数据。随后再次调用commit_subagent_result。"
                    )
                else:
                    instruction = (
                        "该无效结果已被系统丢弃。根据message修正任务说明后，"
                        f"重新委派{subagent_type}一次，再调用commit_subagent_result；"
                        "禁止Supervisor手工重建JSON。"
                    )
            elif subagent_type == "paper-reader":
                instruction = (
                    "当前论文已连续两次生成无效结果，停止重试该论文；"
                    "继续处理ScreeningDecision中的下一篇入选论文。全部论文处理后再尝试"
                    "推进EXTRACTED；禁止Supervisor手工重建JSON。"
                )
            else:
                instruction = (
                    "该子Agent已连续两次生成无效结果，停止重试并调用"
                    "finish_inconclusive保存失败原因；禁止Supervisor手工重建JSON。"
                )
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "subagent_commit_rejected",
                    "subagent_type": subagent_type,
                    "message": str(exc),
                    "rejection_count": rejection_count,
                    "retry_allowed": retry_allowed,
                    "rejection_scope": rejection_scope,
                    "skip_current_paper": (
                        subagent_type == "paper-reader" and not retry_allowed
                    ),
                    "instruction": instruction,
                },
                ensure_ascii=False,
            )
        state.mark_consumed(thread_id, subagent_type, rejection_scope)
        return json.dumps(
            {
                "artifact": artifact.model_dump(mode="json"),
                "project": project.model_dump(mode="json"),
                "search_review": search_review,
                "deterministic_fallback": deterministic_fallback,
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def save_project_artifact(project_id: str, kind: str, payload_json: str) -> str:
        """Save a JSON artifact. Allowed kinds: SearchReport, ScreeningDecision, ScreeningLog, PaperCard, SynthesisReport, ReviewResult, RuntimeFallback."""
        payload = json.loads(payload_json)
        record = service.save_artifact(project_id, kind, payload)
        return record.model_dump_json()

    @tool
    def transition_project_stage(
        project_id: str,
        target_stage: str,
        actor: str,
        review_json: str = "",
    ) -> str:
        """Advance a project through the enforced state machine; review_json is required for REVIEWED."""
        review = ReviewResult.model_validate_json(review_json) if review_json else None
        project = service.transition(
            project_id=project_id,
            target=ResearchStage(target_stage),
            actor=actor,
            review=review,
        )
        return project.model_dump_json()

    @tool
    def save_artifact_and_transition(
        project_id: str,
        kind: str,
        payload_json: str,
        target_stage: str,
        actor: str,
        review_json: str = "",
    ) -> str:
        """Atomically validate/save an artifact and advance its project stage.

        Use this tool whenever a stage requires a new artifact. It guarantees that
        the transition starts only after the artifact has validated successfully.
        """
        try:
            payload = json.loads(payload_json)
            review = None
            if review_json:
                review = ReviewResult.model_validate_json(review_json)
            elif kind == "ReviewResult":
                review = ReviewResult.model_validate(payload)
            artifact, project = service.save_artifact_and_transition(
                project_id=project_id,
                kind=kind,
                payload=payload,
                target=ResearchStage(target_stage),
                actor=actor,
                review=review,
            )
        except json.JSONDecodeError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "invalid_artifact_json",
                    "kind": kind,
                    "target_stage": target_stage,
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                    "instruction": (
                        "重新生成合法JSON；字符串内部的双引号必须转义。"
                        "不要改写官方产物字段或跳过科研阶段。"
                    ),
                },
                ensure_ascii=False,
            )
        except (
            ValidationError,
            WorkflowPrerequisiteError,
            InvalidTransition,
            ProjectNotFound,
            ValueError,
        ) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "artifact_commit_rejected",
                    "kind": kind,
                    "target_stage": target_stage,
                    "message": str(exc),
                    "instruction": (
                        "检查官方产物Schema、真实project_id和当前阶段，"
                        "修正后最多重试一次。"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "artifact": artifact.model_dump(mode="json"),
                "project": project.model_dump(mode="json"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def save_paper_card(project_id: str, payload_json: str) -> str:
        """Validate and save one PaperCard without advancing the project stage."""
        try:
            payload = json.loads(payload_json)
            artifact = service.save_artifact(project_id, "PaperCard", payload)
        except WorkflowPrerequisiteError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "paper_card_stage_not_ready",
                    "message": str(exc),
                    "instruction": (
                        "先保存SearchReport和ScreeningDecision并推进到SCREENED；"
                        "随后逐篇委派paper-reader。"
                    ),
                },
                ensure_ascii=False,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "invalid_paper_card",
                    "message": str(exc),
                    "required_fields": [
                        "paper_id",
                        "title",
                        "research_question",
                        "methods",
                        "datasets",
                        "findings",
                        "limitations",
                    ],
                    "instruction": (
                        "不要把SearchReport候选论文直接保存为PaperCard；"
                        "先委派paper-reader，再原样保存其结构化返回值。"
                    ),
                },
                ensure_ascii=False,
            )
        return artifact.model_dump_json()

    @tool
    def advance_project_stage(
        project_id: str,
        target_stage: str,
        actor: str,
    ) -> str:
        """Advance a stage that does not require a newly submitted artifact.

        Allowed targets are EXTRACTED after all PaperCards, REVIEW_PENDING,
        and COMPLETED for legacy NARRATED projects with a saved NarrativeReview.
        """
        try:
            target = ResearchStage(target_stage)
            allowed_targets = {
                ResearchStage.EXTRACTED,
                ResearchStage.REVIEW_PENDING,
                ResearchStage.COMPLETED,
            }
            if target not in allowed_targets:
                allowed = ", ".join(sorted(item.value for item in allowed_targets))
                raise ValueError(
                    f"advance_project_stage cannot target {target.value}; allowed: {allowed}"
                )
            project = service.transition(project_id, target, actor)
        except InsufficientEvidenceError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "insufficient_evidence",
                    "requested_stage": target_stage,
                    "message": str(exc),
                    "instruction": "保持当前SCREENED阶段并立即调用finish_inconclusive。",
                },
                ensure_ascii=False,
            )
        except (
            WorkflowPrerequisiteError,
            InvalidTransition,
            ProjectNotFound,
            ValueError,
        ) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "stage_transition_rejected",
                    "requested_stage": target_stage,
                    "message": str(exc),
                    "instruction": (
                        "检查当前项目阶段和所需产物；零篇入选论文时调用"
                        "finish_inconclusive，不得重试EXTRACTED。"
                    ),
                },
                ensure_ascii=False,
            )
        return project.model_dump_json()

    @tool
    def finish_inconclusive(
        project_id: str,
        reason: str,
        queries_attempted: list[str],
        search_failures: list[str],
        recommendation: str,
    ) -> str:
        """Save insufficient-evidence details and end the project normally."""
        payload = {
            "reason": reason,
            "queries_attempted": queries_attempted,
            "search_failures": search_failures,
            "recommendation": recommendation,
        }
        try:
            artifact, project = service.save_artifact_and_transition(
                project_id=project_id,
                kind="InsufficientEvidence",
                payload=payload,
                target=ResearchStage.INCONCLUSIVE,
                actor="research-supervisor",
            )
        except (
            ValidationError,
            WorkflowPrerequisiteError,
            InvalidTransition,
            ProjectNotFound,
            ValueError,
        ) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_code": "inconclusive_transition_rejected",
                    "message": str(exc),
                    "instruction": (
                        "已保存SearchReport且尚未COMPLETED的项目可以结束为INCONCLUSIVE。"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "mode": "inconclusive",
                "artifact": artifact.model_dump(mode="json"),
                "project": project.model_dump(mode="json"),
            },
            ensure_ascii=False,
            default=str,
        )

    return [
        create_research_project,
        get_research_project,
        get_active_research_project,
        save_screening_decision,
        commit_subagent_result,
        save_project_artifact,
        transition_project_stage,
        save_artifact_and_transition,
        save_paper_card,
        advance_project_stage,
        finish_inconclusive,
    ]
