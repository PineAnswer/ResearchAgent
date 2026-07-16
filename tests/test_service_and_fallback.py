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


def _enter_search_review(service, project_id: str, paper_ids: list[str]) -> None:
    service.save_artifact(
        project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {"paper_id": paper_id, "title": paper_id, "source": "test"}
                for paper_id in paper_ids
            ],
            "executed_queries": ["query"],
        },
    )
    service.transition(
        project_id,
        ResearchStage.SEARCH_REVIEW_PENDING,
        actor="human-search-review",
    )


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

    with pytest.raises((InvalidTransition, WorkflowPrerequisiteError)):
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
    _enter_search_review(service, searched.project_id, ["P1", "P2"])
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


def test_openalex_url_and_bare_id_match_for_screened_paper_cards(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    full_id = "https://openalex.org/W4409797280"
    bare_id = "W4409797280"
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [
                {"paper_id": full_id, "title": "Paper", "source": "OpenAlex"}
            ],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    _enter_search_review(service, project.project_id, [full_id])
    screening, screened = service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": [full_id],
            "excluded_paper_ids": [],
            "reasons": ["relevant"],
        },
        ResearchStage.SCREENED,
        actor="human-search-review",
    )

    assert screening.payload["included_paper_ids"] == [bare_id]

    service.save_artifact(
        screened.project_id,
        "PaperCard",
        {
            "paper_id": bare_id,
            "title": "Paper",
            "research_question": "question",
            "methods": [],
            "datasets": [],
            "findings": [
                {
                    "evidence_id": "E1",
                    "paper_id": full_id,
                    "claim": "claim",
                    "quote": "quote",
                    "page": None,
                    "section": "abstract",
                }
            ],
            "limitations": [],
        },
    )
    extracted = service.transition(
        screened.project_id,
        ResearchStage.EXTRACTED,
        actor="paper-reader",
    )

    assert extracted.stage is ResearchStage.EXTRACTED


def test_screening_context_returns_small_included_paper_metadata(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    full_id = "https://openalex.org/W4409797280"
    bare_id = "W4409797280"
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "query",
            "search_terms": ["query"],
            "candidates": [
                {
                    "paper_id": full_id,
                    "title": "Large Language Models Empowered Online Log Anomaly Detection",
                    "abstract": "LLM log anomaly detection in AIOps.",
                    "doi": "https://doi.org/10.1109/example",
                    "url": "https://doi.org/10.1109/example",
                    "source": "OpenAlex",
                },
                {"paper_id": "W0", "title": "Other", "source": "OpenAlex"},
            ],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    _enter_search_review(service, project.project_id, [full_id, "W0"])
    screening, _ = service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": [bare_id],
            "excluded_paper_ids": ["W0"],
            "reasons": ["relevant"],
        },
        ResearchStage.SCREENED,
        actor="human-search-review",
    )

    context = service.screening_context(project.project_id)

    assert context["screening_artifact_id"] == screening.artifact_id
    assert context["included_paper_ids"] == [bare_id]
    assert context["included_papers"] == [
        {
            "paper_id": bare_id,
            "title": "Large Language Models Empowered Online Log Anomaly Detection",
            "abstract": "LLM log anomaly detection in AIOps.",
            "doi": "https://doi.org/10.1109/example",
            "url": "https://doi.org/10.1109/example",
            "authors": [],
            "year": None,
            "source": "OpenAlex",
        }
    ]


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
    _enter_search_review(service, project.project_id, ["P1"])
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


def test_paper_card_normalizes_simple_evidence_ids_before_synthesis(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {"query": "q", "search_terms": ["q"], "candidates": [], "selection_notes": []},
        ResearchStage.SEARCHED,
        actor="scout",
    )
    _enter_search_review(service, project.project_id, ["P1", "P2"])
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["P1", "P2"],
            "excluded_paper_ids": [],
            "reasons": ["both relevant"],
        },
        ResearchStage.SCREENED,
        actor="supervisor",
    )
    for paper_id in ("P1", "P2"):
        service.save_artifact(
            project.project_id,
            "PaperCard",
            {
                "paper_id": paper_id,
                "title": paper_id,
                "research_question": "question",
                "methods": ["experiment"],
                "datasets": [],
                "findings": [
                    {
                        "evidence_id": "E1",
                        "paper_id": f"{paper_id}:E1",
                        "claim": f"{paper_id} finding",
                        "quote": f"{paper_id} evidence text.",
                        "page": 1,
                    }
                ],
                "limitations": [],
            },
        )

    snapshot = service.get_snapshot(project.project_id)
    evidence_ids = [
        finding["evidence_id"]
        for artifact in snapshot["artifacts"]
        if artifact["kind"] == "PaperCard"
        for finding in artifact["payload"]["findings"]
    ]
    finding_paper_ids = [
        finding["paper_id"]
        for artifact in snapshot["artifacts"]
        if artifact["kind"] == "PaperCard"
        for finding in artifact["payload"]["findings"]
    ]

    assert evidence_ids == ["P1:E1", "P2:E1"]
    assert finding_paper_ids == ["P1", "P2"]

    project = service.transition(project.project_id, ResearchStage.EXTRACTED, actor="reader")
    artifact, project = service.save_artifact_and_transition(
        project.project_id,
        "SynthesisReport",
        {
            "topic": "topic",
            "consensus": [
                {"statement": "both findings", "evidence_ids": ["P1:E1", "P2:E1"]}
            ],
            "conflicts": [],
            "method_comparison": [],
            "gaps": [],
        },
        ResearchStage.SYNTHESIZED,
        actor="synthesizer",
    )

    assert project.stage is ResearchStage.SYNTHESIZED
    assert artifact.payload["consensus"][0]["evidence_ids"] == ["P1:E1", "P2:E1"]


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
    _enter_search_review(service, project.project_id, ["P1"])
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
