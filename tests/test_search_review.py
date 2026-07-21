import json
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from research_agent.agents.runtime_state import ResearchRuntimeState
from research_agent.application.research_service import (
    ResearchService,
    WorkflowPrerequisiteError,
)
from research_agent.application.search_review import SearchReviewService
from research_agent.domain.models import ResearchStage, SearchFeedback
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.tools.project_tools import build_project_tools


@tool
def fake_search_openalex(query: str, limit: int = 5) -> str:
    """Return deterministic candidates for human-review tests."""
    del limit
    return json.dumps(
        [
            {
                "paper_id": "P1" if "duplicate" in query else "P2",
                "title": "Existing" if "duplicate" in query else "Supplemental",
                "authors": [],
                "abstract": "",
                "source": "OpenAlex",
            }
        ]
    )


@tool
def fake_verify_doi(doi: str) -> str:
    """Resolve one DOI without network access."""
    return json.dumps(
        {
            "doi": doi,
            "title": "DOI paper",
            "authors": [{"given": "Ada", "family": "Lovelace"}],
            "url": f"https://doi.org/{doi}",
        }
    )


def _review_service(tmp_path, *, max_rounds: int = 3):
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
        max_rounds=max_rounds,
        max_queries_per_round=2,
    )
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["initial query"],
            "candidates": [
                {"paper_id": "P1", "title": "Existing", "source": "OpenAlex"}
            ],
            "screening_decisions": {"P1": "include"},
            "screening_reasons": {"P1": "Matches the research question."},
            "selection_notes": [],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    review.begin_review(project.project_id)
    return service, review, project.project_id


class _RejectingVenueIndex:
    def enrich_candidate(self, candidate):
        return {
            **candidate,
            "venue": candidate.get("venue", ""),
            "ccf_rank": None,
            "sci_quartile": None,
            "nature_portfolio": False,
        }

    @staticmethod
    def qualifies_for_quality_filter(candidate):
        return False


def test_initial_search_enters_persisted_human_review(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path)

    result = review.get_review(project_id)

    assert result["awaiting_input"] is True
    assert result["project"]["stage"] == "SEARCH_REVIEW_PENDING"
    assert result["candidate_set"]["candidates"][0]["paper_id"] == "P1"
    assert result["candidate_set"]["query_rounds"] == [["initial query"]]
    assert result["candidate_set"]["agent_included_paper_ids"] == ["P1"]
    assert result["candidate_set"]["agent_approved"] is True
    assert service.get_snapshot(project_id)["artifacts"][-1]["kind"] == (
        "CandidateSetSnapshot"
    )


def test_review_records_system_search_terms_by_iteration(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
    )
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["initial query", "refined query"],
            "search_iteration_log": [
                {"query": "initial query", "count": 5, "new_count": 5},
                {"query": "refined query", "count": 3, "new_count": 2},
            ],
            "candidates": [
                {"paper_id": "P1", "title": "Existing", "source": "OpenAlex"}
            ],
            "screening_decisions": {"P1": "include"},
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    result = review.begin_review(project.project_id, max_search_rounds=3)

    assert result["candidate_set"]["query_rounds"] == [
        ["initial query"],
        ["refined query"],
    ]
    assert result["candidate_set"]["search_round"] == 2


def test_empty_filtered_result_stays_searched_and_supports_manual_recovery(
    tmp_path,
) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
        venue_index=_RejectingVenueIndex(),
    )
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["initial query"],
            "candidates": [
                {
                    "paper_id": "P1",
                    "title": "Relevant but unranked",
                    "year": 2025,
                    "source": "OpenAlex",
                }
            ],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    blocked = review.begin_review(
        project.project_id,
        year_from=2024,
        year_to=2026,
        quality_venues_only=True,
    )

    assert blocked["awaiting_input"] is False
    assert blocked["manual_recovery_allowed"] is True
    assert "没有论文满足" in blocked["message"]
    assert blocked["candidate_set"]["candidates"] == []
    assert blocked["candidate_set"]["filtered_candidates"][0]["paper_id"] == "P1"
    assert service.get_project(project.project_id).stage is ResearchStage.SEARCHED

    recovered = review.apply_feedback(
        project.project_id,
        SearchFeedback(
            action="accept",
            added_papers=[
                {
                    "paper_id": "P1",
                    "title": "Relevant but unranked",
                    "year": 2025,
                }
            ],
        ),
    )

    assert recovered["ready_to_continue"] is True
    assert recovered["project"]["stage"] == "SCREENED"
    assert recovered["screening"]["payload"]["included_paper_ids"] == ["P1"]


def test_legacy_empty_snapshot_exposes_original_results_for_manual_recovery(
    tmp_path,
) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
    )
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["query"],
            "candidates": [
                {"paper_id": "P1", "title": "Legacy result", "source": "OpenAlex"}
            ],
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    service.save_artifact_and_transition(
        project.project_id,
        "CandidateSetSnapshot",
        {"candidates": [], "quality_venues_only": True},
        ResearchStage.SEARCH_REVIEW_PENDING,
        actor="human-search-review",
    )

    result = review.get_review(project.project_id)

    assert result["awaiting_input"] is False
    assert result["manual_recovery_allowed"] is True
    assert result["candidate_set"]["filtered_candidates"][0]["paper_id"] == "P1"
    assert "旧版记录" in result["candidate_set"]["filtered_candidate_reasons"]["P1"][0]


def test_feedback_can_refine_add_remove_without_supplemental_search(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path)

    result = review.apply_feedback(
        project_id,
        SearchFeedback(
            action="refine",
            added_papers=[
                {
                    "paper_id": "P3",
                    "title": "User paper",
                    "source": "user",
                    "abstract": "Unverified user-written summary.",
                }
            ],
            excluded_paper_ids=["P1"],
            comment="Remove the off-topic paper.",
        ),
    )

    candidate_ids = {
        item["paper_id"] for item in result["candidate_set"]["candidates"]
    }
    assert candidate_ids == {"P3"}
    manual = next(
        item
        for item in result["candidate_set"]["candidates"]
        if item["paper_id"] == "P3"
    )
    assert manual["source"] == "user-unverified"
    assert manual["abstract"] == ""
    assert result["candidate_set"]["search_round"] == 1
    assert result["candidate_set"]["query_rounds"] == [["initial query"]]
    assert result["new_queries"] == []

    kinds = [item["kind"] for item in service.get_snapshot(project_id)["artifacts"]]
    assert "SearchFeedback" in kinds
    assert "SupplementalSearchReport" not in kinds


def test_feedback_rejects_user_triggered_supplemental_queries(tmp_path) -> None:
    _service, review, project_id = _review_service(tmp_path)

    with pytest.raises(WorkflowPrerequisiteError, match="不再触发新的检索"):
        review.apply_feedback(
            project_id,
            SearchFeedback(action="refine", suggested_queries=["supplemental query"]),
        )


def test_verified_doi_can_be_added_and_user_can_accept(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path)

    result = review.apply_feedback(
        project_id,
        SearchFeedback(
            action="accept",
            added_papers=[{"doi": "10.1000/example"}],
            excluded_paper_ids=["P1"],
            comment="Use the DOI paper.",
        ),
    )

    assert result["ready_to_continue"] is True
    assert result["project"]["stage"] == "SCREENED"
    assert result["screening"]["payload"]["included_paper_ids"] == [
        "10.1000/example"
    ]
    assert service.get_project(project_id).stage is ResearchStage.SCREENED

    undone = review.undo_last_feedback(project_id)
    assert undone["undone_action"] == "accept"
    assert undone["project"]["stage"] == "SEARCH_REVIEW_PENDING"
    assert [item["paper_id"] for item in undone["candidate_set"]["candidates"]] == ["P1"]
    assert review.get_review(project_id)["can_undo"] is False


def test_openalex_url_candidates_are_screened_as_bare_ids(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
    )
    project = service.create_project("topic", "question")
    service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["query"],
            "candidates": [
                {
                    "paper_id": "https://openalex.org/W4409797280",
                    "title": "OpenAlex paper",
                    "source": "OpenAlex",
                }
            ],
            "screening_decisions": {"https://openalex.org/W4409797280": "include"},
            "screening_reasons": {
                "https://openalex.org/W4409797280": "Relevant."
            },
        },
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    review.begin_review(project.project_id)

    result = review.apply_feedback(
        project.project_id,
        SearchFeedback(action="accept"),
    )

    assert result["candidate_set"]["agent_included_paper_ids"] == ["W4409797280"]
    assert result["screening"]["payload"]["included_paper_ids"] == ["W4409797280"]


def test_feedback_controls_candidate_count_bounds(tmp_path) -> None:
    _service, review, project_id = _review_service(tmp_path)

    with pytest.raises(WorkflowPrerequisiteError, match="between 2 and 3"):
        review.apply_feedback(
            project_id,
            SearchFeedback(action="accept", min_papers=2, max_papers=3),
        )


def test_stop_can_be_undone_after_review(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path, max_rounds=1)

    stopped = review.apply_feedback(
        project_id,
        SearchFeedback(action="stop", comment="Enough searching."),
    )
    assert stopped["project"]["stage"] == "INCONCLUSIVE"
    assert service.get_project(project_id).stage is ResearchStage.INCONCLUSIVE

    restored = review.undo_last_feedback(project_id)
    assert restored["undone_action"] == "stop"
    assert service.get_project(project_id).stage is ResearchStage.SEARCH_REVIEW_PENDING


def test_scout_commit_callback_opens_review_and_consumes_result(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
    )
    state = ResearchRuntimeState()
    tools = {
        item.name: item
        for item in build_project_tools(
            service,
            state,
            on_search_committed=lambda project_id, _thread_id: review.begin_review(
                project_id
            ),
        )
    }
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-a"}})
    project = json.loads(
        tools["create_research_project"].func("topic", "question", runtime=runtime)
    )
    state.record_result(
        "thread-a",
        "literature-scout",
        {
            "query": "question",
            "search_terms": ["query"],
            "candidates": [
                {"paper_id": "P1", "title": "Paper", "source": "OpenAlex"}
            ],
            "selection_notes": [],
        },
    )

    committed = json.loads(
        tools["commit_subagent_result"].func(
            project["project_id"], "literature-scout", runtime=runtime
        )
    )

    assert committed["project"]["stage"] == "SEARCH_REVIEW_PENDING"
    assert committed["search_review"]["awaiting_input"] is True
    assert state.pending_result("thread-a", "literature-scout") is None


def test_empty_scout_result_opens_review_instead_of_ending_project(tmp_path) -> None:
    service = ResearchService(SqliteResearchRepository(tmp_path / "test.db"))
    review = SearchReviewService(
        service,
        {
            "search_openalex": fake_search_openalex,
            "verify_doi": fake_verify_doi,
        },
    )
    state = ResearchRuntimeState()
    tools = {
        item.name: item
        for item in build_project_tools(
            service,
            state,
            on_search_committed=lambda project_id, _thread_id: review.begin_review(
                project_id
            ),
        )
    }
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-empty"}})
    project = json.loads(
        tools["create_research_project"].func("topic", "question", runtime=runtime)
    )
    state.record_result(
        "thread-empty",
        "literature-scout",
        {
            "query": "question",
            "search_terms": ["query"],
            "candidate_ids": [],
            "candidates": [],
            "screening_decisions": {},
            "screening_reasons": {},
            "coverage_gaps": ["No results yet."],
            "search_iteration_log": [],
            "selection_notes": ["Keep the project open for manual refinement."],
        },
    )

    committed = json.loads(
        tools["commit_subagent_result"].func(
            project["project_id"],
            "literature-scout",
            runtime=runtime,
        )
    )

    assert committed["project"]["stage"] == "SEARCH_REVIEW_PENDING"
    assert committed["search_review"]["candidate_set"]["candidates"] == []
    assert service.get_project(project["project_id"]).stage is (
        ResearchStage.SEARCH_REVIEW_PENDING
    )
