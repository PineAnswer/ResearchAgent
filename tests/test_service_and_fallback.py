from research_agent.application.fallback import OfflineFallback
import pytest

from research_agent.application.research_service import (
    InsufficientEvidenceError,
    ResearchService,
    WorkflowPrerequisiteError,
    _numeric_claims,
)
from research_agent.domain.models import (
    ResearchStage,
    ReviewResult,
    ReviewVerdict,
)
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


def test_numeric_claim_extraction_ignores_technical_identifiers_and_matches_exact_values() -> None:
    assert _numeric_claims("2D、3DVG、FFL-3DOG、ResNet-50 与 GPT-4") == []
    assert _numeric_claims("提升 3x、准确率 95 %，样本数 1,024") == ["3x", "95%", "1024"]
    assert "3" not in _numeric_claims("结果发表于 2023 年")


def _create_reviewed_project(service: ResearchService):
    project = service.create_project("topic", "question")
    for stage in (
        ResearchStage.SEARCHED,
        ResearchStage.SEARCH_REVIEW_PENDING,
        ResearchStage.SCREENED,
        ResearchStage.EXTRACTED,
        ResearchStage.SYNTHESIZED,
        ResearchStage.REVIEW_PENDING,
    ):
        project = service.repository.transition(project.project_id, stage, actor="test")
    review = ReviewResult(
        verdict=ReviewVerdict.PASS,
        verified_evidence_ids=["P1:E1"],
    )
    project = service.repository.transition(
        project.project_id,
        ResearchStage.REVIEWED,
        actor="evidence-reviewer",
        review=review,
    )
    service.save_artifact(
        project.project_id,
        "ReviewResult",
        review.model_dump(mode="json"),
    )
    return project


def _save_narrative_inputs(service: ResearchService, project_id: str):
    service.repository.save_artifact(
        project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {
                    "paper_id": "P1",
                    "title": "Evidence Paper",
                    "authors": ["A. Author"],
                    "year": 2025,
                    "doi": "10.1000/example",
                    "source": "test",
                }
            ]
        },
    )
    service.repository.save_artifact(
        project_id,
        "PaperCard",
        {
            "paper_id": "P1",
            "title": "Evidence Paper",
            "research_question": "question",
            "methods": ["experiment"],
            "datasets": [],
            "findings": [
                {
                    "evidence_id": "P1:E1",
                    "paper_id": "P1",
                    "claim": "supported finding",
                    "quote": "The finding is supported.",
                    "page": 2,
                }
            ],
            "limitations": [],
        },
    )
    _, project = service.save_artifact_and_transition(
        project_id,
        "ReviewOutline",
        {
            "title": "Evidence Review",
            "narrative_arc": "evidence first",
            "sections": [
                {
                    "section_id": "sec-1",
                    "heading": "Findings",
                    "assigned_paper_ids": ["P1"],
                    "assigned_evidence_ids": ["P1:E1"],
                    "key_claims": ["supported finding"],
                    "target_words": 300,
                }
            ],
        },
        ResearchStage.OUTLINED,
        actor="research-outliner",
    )
    service.save_artifact(
        project_id,
        "SectionDraft",
        {
            "section_id": "sec-1",
            "heading": "Findings",
            "content": "Persisted evidence-backed text [P1:E1].",
            "cited_evidence": ["P1:E1"],
        },
    )
    return project


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
            "library_id": "",
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


def test_completion_requires_narrative_and_fact_check_for_every_section(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "ReviewOutline",
        {
            "title": "Review",
            "narrative_arc": "evidence first",
            "sections": [
                {
                    "section_id": "sec-1",
                    "heading": "Findings",
                    "assigned_paper_ids": ["P1"],
                    "assigned_evidence_ids": ["P1:E1"],
                    "key_claims": ["finding"],
                    "target_words": 300,
                }
            ],
        },
        ResearchStage.OUTLINED,
        actor="research-outliner",
    )
    service.save_artifact(
        project.project_id,
        "SectionDraft",
        {
            "section_id": "sec-1",
            "heading": "Findings",
            "content": "Evidence-backed text [P1:E1].",
            "cited_evidence": ["P1:E1"],
        },
    )
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "NarrativeReview",
        {
            "title": "Review",
            "abstract": "Abstract",
            "sections": [
                {
                    "section_id": "sec-1",
                    "heading": "Findings",
                    "content": "Evidence-backed text [P1:E1].",
                    "cited_evidence": ["P1:E1"],
                }
            ],
            "references": [],
            "word_count": 4,
        },
        ResearchStage.NARRATED,
        actor="chief-editor",
    )

    with pytest.raises(WorkflowPrerequisiteError, match="FactCheckReport"):
        service.transition(project.project_id, ResearchStage.COMPLETED, actor="pi")

    service.save_artifact(
        project.project_id,
        "FactCheckReport",
        {"section_id": "sec-1", "verdict": "PASS", "issues": []},
    )
    completed = service.transition(
        project.project_id,
        ResearchStage.COMPLETED,
        actor="pi",
    )

    assert completed.stage is ResearchStage.COMPLETED


def test_revise_fact_check_requires_targeted_revision_and_recheck(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    _save_narrative_inputs(service, project.project_id)
    _, project = service.assemble_narrative_review(project.project_id)
    service.save_artifact(
        project.project_id,
        "FactCheckReport",
        {
            "section_id": "sec-1",
            "verdict": "REVISE",
            "issues": [
                {
                    "claim": "overstated claim",
                    "evidence_id": "P1:E1",
                    "problem": "overclaim",
                    "correction": "Use cautious wording.",
                }
            ],
        },
    )

    with pytest.raises(WorkflowPrerequisiteError, match="blocked by REVISE"):
        service.transition(project.project_id, ResearchStage.COMPLETED, actor="pi")

    project = service.transition(
        project.project_id,
        ResearchStage.REVISION_PENDING,
        actor="research-supervisor",
    )
    assert project.stage is ResearchStage.REVISION_PENDING

    with pytest.raises(
        WorkflowPrerequisiteError,
        match="corrected drafts for: sec-1",
    ):
        service.assemble_narrative_review(project.project_id)

    service.save_artifact(
        project.project_id,
        "SectionDraft",
        {
            "section_id": "sec-1",
            "heading": "Findings",
            "content": "The evidence cautiously supports this finding [P1:E1].",
            "cited_evidence": ["P1:E1"],
        },
    )
    revised_artifact, project = service.assemble_narrative_review(project.project_id)
    assert project.stage is ResearchStage.NARRATED
    assert revised_artifact.payload["sections"][0]["content"] == (
        "The evidence cautiously supports this finding [P1:E1]."
    )
    service.save_artifact(
        project.project_id,
        "FactCheckReport",
        {"section_id": "sec-1", "verdict": "PASS", "issues": []},
    )
    completed = service.transition(
        project.project_id,
        ResearchStage.COMPLETED,
        actor="research-supervisor",
    )
    assert completed.stage is ResearchStage.COMPLETED


def test_prepare_continuation_repairs_legacy_false_completion(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    project = service.repository.transition(
        project.project_id,
        ResearchStage.OUTLINED,
        actor="legacy-supervisor",
    )
    project = service.repository.transition(
        project.project_id,
        ResearchStage.NARRATED,
        actor="legacy-supervisor",
    )
    service.repository.transition(
        project.project_id,
        ResearchStage.COMPLETED,
        actor="legacy-supervisor",
    )

    continuation = service.prepare_continuation(project.project_id)

    assert continuation["mode"] == "narrative"
    assert continuation["project"].stage is ResearchStage.REVIEWED
    assert continuation["context"]["current_stage"] == "REVIEWED"
    recovery_event = service.get_snapshot(project.project_id)["events"][-1]
    assert recovery_event["from_stage"] == "COMPLETED"
    assert recovery_event["to_stage"] == "REVIEWED"
    assert recovery_event["actor"] == "workflow-recovery"


def test_prepare_continuation_recovers_operational_writing_failure(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    project = _save_narrative_inputs(service, project.project_id)
    _, project = service.save_artifact_and_transition(
        project.project_id,
        "InsufficientEvidence",
        {
            "reason": "chief-editor连续两次生成无效结果，缺少title和sections字段。",
            "queries_attempted": [],
            "search_failures": [],
            "recommendation": "重新委派chief-editor并返回完整结构化结果。",
        },
        ResearchStage.INCONCLUSIVE,
        actor="research-supervisor",
    )

    continuation = service.prepare_continuation(project.project_id)

    assert continuation["project"].stage is ResearchStage.OUTLINED
    assert continuation["context"]["saved_section_draft_ids"] == ["sec-1"]
    assert continuation["context"]["recovered_from"] == "INCONCLUSIVE"
    recovery_event = service.get_snapshot(project.project_id)["events"][-1]
    assert recovery_event["from_stage"] == "INCONCLUSIVE"
    assert recovery_event["to_stage"] == "OUTLINED"
    assert recovery_event["actor"] == "workflow-recovery"


def test_prepare_continuation_recovers_partial_reading_without_skipping_missing_papers(
    tmp_path,
) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "q",
            "search_terms": ["q"],
            "candidates": [
                {"paper_id": "P1", "title": "One", "source": "test"},
                {"paper_id": "P2", "title": "Two", "source": "test"},
            ],
        },
        ResearchStage.SEARCHED,
        actor="scout",
    )
    _enter_search_review(service, project.project_id, ["P1", "P2"])
    service.save_artifact_and_transition(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["P1", "P2"],
            "excluded_paper_ids": [],
            "reasons": ["relevant"],
        },
        ResearchStage.SCREENED,
        actor="human-search-review",
    )
    service.save_artifact(
        project.project_id,
        "PaperCard",
        {
            "paper_id": "P1",
            "title": "One",
            "research_question": "question",
            "methods": [],
            "findings": [
                {
                    "evidence_id": "P1:E1",
                    "paper_id": "P1",
                    "claim": "finding",
                    "quote": "finding",
                }
            ],
        },
    )
    service.save_artifact_and_transition(
        project.project_id,
        "InsufficientEvidence",
        {
            "reason": "paper-reader structured_response failed twice",
            "recommendation": "retry the missing subagent result",
        },
        ResearchStage.INCONCLUSIVE,
        actor="research-supervisor",
    )

    continuation = service.prepare_continuation(project.project_id)

    assert continuation["project"].stage is ResearchStage.SCREENED
    assert continuation["context"]["saved_paper_card_ids"] == ["P1"]


def test_prepare_continuation_does_not_reopen_true_evidence_failure(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    project = _save_narrative_inputs(service, project.project_id)
    service.save_artifact_and_transition(
        project.project_id,
        "InsufficientEvidence",
        {
            "reason": "可用论文的证据范围不足以回答研究问题。",
            "queries_attempted": ["query"],
            "search_failures": [],
            "recommendation": "扩大检索范围并补充论文。",
        },
        ResearchStage.INCONCLUSIVE,
        actor="research-supervisor",
    )

    with pytest.raises(WorkflowPrerequisiteError, match="recoverable operational"):
        service.prepare_continuation(project.project_id)

    assert service.get_project(project.project_id).stage is ResearchStage.INCONCLUSIVE


def test_deterministic_narrative_assembly_preserves_saved_drafts(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    project = _save_narrative_inputs(service, project.project_id)

    artifact, updated = service.assemble_narrative_review(project.project_id)

    assert updated.stage is ResearchStage.NARRATED
    assert artifact.kind == "NarrativeReview"
    assert artifact.payload["sections"][0]["content"] == (
        "Persisted evidence-backed text [P1:E1]."
    )
    assert artifact.payload["evidence_chain"] == {"P1:E1": ["sec-1"]}
    assert artifact.payload["references"] == [
        {
            "paper_id": "P1",
            "text": "A. Author (2025) Evidence Paper. DOI: 10.1000/example.",
            "bibtex": "",
        }
    ]
    assert "1 篇入选文献" in artifact.payload["abstract"]
    assert artifact.payload["word_count"] > 0


def test_agent_context_omits_search_history_and_keeps_current_writing_inputs(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    project = _create_reviewed_project(service)
    project = _save_narrative_inputs(service, project.project_id)

    context = service.get_agent_context(project.project_id)

    assert "events" not in context
    assert [artifact["kind"] for artifact in context["artifacts"]] == [
        "PaperCard",
        "ReviewOutline",
        "SectionDraft",
    ]
