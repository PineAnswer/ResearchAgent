from __future__ import annotations

import json
import tempfile
from pathlib import Path

from research_agent.application.research_service import (
    ResearchService,
    WorkflowPrerequisiteError,
)
from research_agent.domain.models import ResearchStage, ReviewResult, ReviewVerdict
from research_agent.domain.workflow import InvalidTransition
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def run_offline_demo(database_path: Path | None = None) -> dict:
    """Exercise the deterministic workflow without an API key or Deep Agents install."""
    temp_dir = None
    if database_path is None:
        temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(temp_dir.name) / "demo.db"

    repository = SqliteResearchRepository(database_path)
    service = ResearchService(repository)
    project = service.create_project(
        topic="小样本遥感图像分类",
        research_question="证据增强的数据扩增能否稳定改善小样本遥感分类？",
    )

    premature_completion_blocked = False
    try:
        service.transition(project.project_id, ResearchStage.COMPLETED, actor="pi")
    except (InvalidTransition, WorkflowPrerequisiteError):
        premature_completion_blocked = True

    service.save_artifact(
        project.project_id,
        "SearchReport",
        {
            "query": project.research_question,
            "search_terms": ["few-shot remote sensing", "data augmentation"],
            "candidates": [],
            "selection_notes": ["离线演示不访问外部论文 API"],
        },
    )
    project = service.transition(
        project.project_id,
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    service.save_artifact(
        project.project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {
                    "paper_id": "DEMO-PAPER",
                    "title": "离线演示论文",
                    "source": "offline-demo",
                }
            ],
            "executed_queries": ["few-shot remote sensing", "data augmentation"],
        },
    )
    project = service.transition(
        project.project_id,
        ResearchStage.SEARCH_REVIEW_PENDING,
        actor="human-search-review",
    )

    service.save_artifact(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["DEMO-PAPER"],
            "excluded_paper_ids": [],
            "reasons": ["用于演示流程，不代表真实论文筛选"],
        },
    )
    project = service.transition(project.project_id, ResearchStage.SCREENED, actor="pi")

    service.save_artifact(
        project.project_id,
        "PaperCard",
        {
            "paper_id": "DEMO-PAPER",
            "title": "离线演示论文",
            "research_question": project.research_question,
            "methods": ["demo"],
            "datasets": [],
            "findings": [
                {
                    "evidence_id": "DEMO-PAPER:E1",
                    "paper_id": "DEMO-PAPER",
                    "claim": "演示证据",
                    "quote": "该内容仅用于离线流程验证。",
                    "section": "demo",
                }
            ],
            "limitations": ["该卡片只用于验证系统流程"],
        },
    )
    project = service.transition(
        project.project_id,
        ResearchStage.EXTRACTED,
        actor="paper-reader",
    )

    service.save_artifact(
        project.project_id,
        "SynthesisReport",
        {
            "topic": project.topic,
            "consensus": [
                {
                    "statement": "该离线项目只验证流程，不提供科研结论",
                    "evidence_ids": ["DEMO-PAPER:E1"],
                }
            ],
            "conflicts": [],
            "method_comparison": [],
            "gaps": [],
        },
    )
    project = service.transition(
        project.project_id,
        ResearchStage.SYNTHESIZED,
        actor="research-synthesizer",
    )
    project = service.transition(project.project_id, ResearchStage.REVIEW_PENDING, actor="pi")
    review = ReviewResult(
        verdict=ReviewVerdict.PASS,
        suggestions=["正式运行时补充 DOI 和页码证据"],
        verified_evidence_ids=["DEMO-PAPER:E1"],
    )
    service.save_artifact(
        project.project_id,
        "ReviewResult",
        review.model_dump(mode="json"),
    )
    project = service.transition(
        project.project_id,
        ResearchStage.REVIEWED,
        actor="evidence-reviewer",
        review=review,
    )

    section_id = "demo-findings"
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "ReviewOutline",
        {
            "title": "离线演示综述",
            "narrative_arc": "仅验证综述写作流程",
            "sections": [
                {
                    "section_id": section_id,
                    "heading": "演示结论",
                    "assigned_paper_ids": ["DEMO-PAPER"],
                    "assigned_evidence_ids": ["DEMO-PAPER:E1"],
                    "key_claims": ["该离线项目只验证流程，不提供科研结论"],
                    "target_words": 100,
                }
            ],
        },
        ResearchStage.OUTLINED,
        actor="research-outliner",
    )
    section_content = "该离线项目只验证流程，不提供科研结论 [DEMO-PAPER:E1]。"
    service.save_artifact(
        project.project_id,
        "SectionDraft",
        {
            "section_id": section_id,
            "heading": "演示结论",
            "content": section_content,
            "cited_evidence": ["DEMO-PAPER:E1"],
        },
    )
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "NarrativeReview",
        {
            "title": "离线演示综述",
            "abstract": "该综述仅用于验证离线工作流。",
            "sections": [
                {
                    "section_id": section_id,
                    "heading": "演示结论",
                    "content": section_content,
                    "cited_evidence": ["DEMO-PAPER:E1"],
                }
            ],
            "references": [],
            "word_count": len(section_content),
            "evidence_chain": {"DEMO-PAPER:E1": [section_id]},
        },
        ResearchStage.COMPLETED,
        actor="chief-editor",
    )

    result = {
        "project": project.model_dump(mode="json"),
        "premature_completion_blocked": premature_completion_blocked,
        "events": service.get_snapshot(project.project_id)["events"],
        "artifacts": service.get_snapshot(project.project_id)["artifacts"],
    }
    if temp_dir is not None:
        temp_dir.cleanup()
    return result


def main() -> None:
    print(json.dumps(run_offline_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
