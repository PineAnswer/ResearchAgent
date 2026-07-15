from research_agent.application.fallback import OfflineFallback
import pytest

from research_agent.application.research_service import (
    InsufficientEvidenceError,
    ResearchService,
    WorkflowPrerequisiteError,
)
from research_agent.domain.models import ResearchStage
from research_agent.domain.workflow import InvalidTransition
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def test_fallback_creates_traceable_project_without_claiming_results(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    fallback = OfflineFallback(service)

    result = fallback.run("topic", "question", reason="missing API key")

    assert result["mode"] == "fallback"
    assert result["project"]["stage"] == "CREATED"
    assert result["notice"]["kind"] == "RuntimeFallback"


def test_fallback_reuses_an_existing_project(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    fallback = OfflineFallback(service)

    result = fallback.run(
        "topic",
        "question",
        reason="network timeout",
        project_id=project.project_id,
    )

    assert result["reused_project"] is True
    assert result["project"]["project_id"] == project.project_id
    assert service.get_snapshot(project.project_id)["artifacts"][0]["kind"] == "RuntimeFallback"


def test_service_requires_artifact_before_stage_transition(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")

    with pytest.raises(WorkflowPrerequisiteError, match="SearchReport"):
        service.transition(project.project_id, ResearchStage.SEARCHED, actor="scout")


def test_service_validates_artifact_schema(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")

    with pytest.raises(ValueError):
        service.save_artifact(project.project_id, "SearchReport", {"query": "missing fields"})


def test_service_normalizes_and_atomically_commits_search_report(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    llm_payload = {
        "research_question": "Which augmentation methods lack evidence?",
        "search_terms": ["few-shot remote sensing augmentation"],
        "papers": [
            {
                "title": "A paper",
                "doi": "10.1000/example",
                "authors": ["Author"],
                "year": 2024,
                "relevance": "high",
            }
        ],
        "summary": "One candidate was retained.",
    }

    artifact, updated = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        llm_payload,
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    assert updated.stage is ResearchStage.SEARCHED
    assert artifact.payload["query"] == llm_payload["research_question"]
    assert artifact.payload["candidates"][0]["paper_id"] == "10.1000/example"
    assert artifact.payload["candidates"][0]["source"] == "literature-scout"
    assert artifact.payload["selection_notes"] == ["One candidate was retained."]
    assert len(service.get_snapshot(project.project_id)["events"]) == 1


def test_atomic_commit_rolls_back_when_transition_is_illegal(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")

    with pytest.raises(InvalidTransition):
        service.save_artifact_and_transition(
            project.project_id,
            "ScreeningDecision",
            {
                "included_paper_ids": ["P1"],
                "excluded_paper_ids": [],
                "reasons": ["relevant"],
            },
            ResearchStage.SCREENED,
            actor="pi",
        )

    assert service.get_snapshot(project.project_id)["artifacts"] == []


def test_paper_card_cannot_be_saved_before_screening(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")

    with pytest.raises(WorkflowPrerequisiteError, match="SCREENED"):
        service.save_artifact(
            project.project_id,
            "PaperCard",
            {
                "paper_id": "P1",
                "title": "Paper",
                "research_question": "question",
                "methods": [],
                "datasets": [],
                "findings": [],
                "limitations": ["metadata only"],
            },
        )

    assert service.get_snapshot(project.project_id)["artifacts"] == []


def test_extracted_requires_a_paper_card_for_every_included_paper(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    _, searched = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [],
            "selection_notes": [],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    _, screened = service.save_artifact_and_transition(
        searched.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["P1", "P2"],
            "excluded_paper_ids": [],
            "reasons": ["both are relevant"],
        },
        ResearchStage.SCREENED,
        actor="pi",
    )
    service.save_artifact(
        screened.project_id,
        "PaperCard",
        {
            "paper_id": "P1",
            "title": "Paper 1",
            "research_question": "question",
            "methods": [],
            "datasets": [],
            "findings": [],
            "limitations": ["metadata only"],
        },
    )

    with pytest.raises(WorkflowPrerequisiteError, match="P2"):
        service.transition(
            screened.project_id,
            ResearchStage.EXTRACTED,
            actor="paper-reader",
        )

    assert service.get_project(project.project_id).stage is ResearchStage.SCREENED


def test_extracted_rejects_all_empty_findings(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [],
            "selection_notes": [],
        },
        ResearchStage.SEARCHED,
        actor="scout",
    )
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["P1"],
            "excluded_paper_ids": [],
            "reasons": ["relevant"],
        },
        ResearchStage.SCREENED,
        actor="supervisor",
    )
    service.save_artifact(
        project.project_id,
        "PaperCard",
        {
            "paper_id": "P1",
            "title": "Paper",
            "research_question": "question",
            "methods": [],
            "datasets": [],
            "findings": [],
            "limitations": ["no full text or abstract"],
        },
    )

    with pytest.raises(InsufficientEvidenceError, match="empty findings"):
        service.transition(project.project_id, ResearchStage.EXTRACTED, actor="reader")

    assert service.get_project(project.project_id).stage is ResearchStage.SCREENED


def test_synthesis_rejects_unknown_evidence_and_unsupported_numbers(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {"query": "q", "search_terms": ["q"], "candidates": [], "selection_notes": []},
        ResearchStage.SEARCHED,
        actor="scout",
    )
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {"included_paper_ids": ["P1"], "excluded_paper_ids": [], "reasons": ["yes"]},
        ResearchStage.SCREENED,
        actor="supervisor",
    )
    service.save_artifact(
        project.project_id,
        "PaperCard",
        {
            "paper_id": "P1",
            "title": "Paper",
            "research_question": "question",
            "methods": ["experiment"],
            "datasets": [],
            "findings": [
                {
                    "evidence_id": "P1:E1",
                    "paper_id": "P1",
                    "claim": "latency improves",
                    "quote": "The method reduces latency.",
                    "page": 3,
                }
            ],
            "limitations": [],
        },
    )
    project = service.transition(project.project_id, ResearchStage.EXTRACTED, actor="reader")
    payload = {
        "topic": "topic",
        "consensus": [{"statement": "latency improves", "evidence_ids": ["P1:E1"]}],
        "conflicts": [],
        "method_comparison": [],
        "gaps": [
            {
                "description": "No benchmark",
                "supporting_paper_ids": ["P1"],
                "conflicting_paper_ids": [],
                "evidence_ids": ["P1:E1"],
                "confidence": "LOW",
                "proposed_hypothesis": "Latency improves by 3x.",
            }
        ],
    }

    with pytest.raises(WorkflowPrerequisiteError, match="unsupported numeric"):
        service.save_artifact_and_transition(
            project.project_id,
            "SynthesisReport",
            payload,
            ResearchStage.SYNTHESIZED,
            actor="synthesizer",
        )
