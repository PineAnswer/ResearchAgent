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


def test_initial_search_enters_persisted_human_review(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path)

    result = review.get_review(project_id)

    assert result["awaiting_input"] is True
    assert result["project"]["stage"] == "SEARCH_REVIEW_PENDING"
    assert result["candidate_set"]["candidates"][0]["paper_id"] == "P1"
    assert result["candidate_set"]["agent_included_paper_ids"] == ["P1"]
    assert result["candidate_set"]["agent_approved"] is True
    assert service.get_snapshot(project_id)["artifacts"][-1]["kind"] == (
        "CandidateSetSnapshot"
    )


def test_feedback_can_refine_add_remove_and_deduplicate_queries(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path)

    result = review.apply_feedback(
        project_id,
        SearchFeedback(
            action="refine",
            suggested_queries=["supplemental query"],
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
    assert candidate_ids == {"P2", "P3"}
    manual = next(
        item
        for item in result["candidate_set"]["candidates"]
        if item["paper_id"] == "P3"
    )
    assert manual["source"] == "user-unverified"
    assert manual["abstract"] == ""
    assert result["candidate_set"]["search_round"] == 1
    assert result["new_queries"] == ["supplemental query"]

    duplicate = review.apply_feedback(
        project_id,
        SearchFeedback(action="refine", suggested_queries=["query supplemental"]),
    )
    assert duplicate["new_queries"] == []
    assert duplicate["candidate_set"]["search_round"] == 1
    kinds = [item["kind"] for item in service.get_snapshot(project_id)["artifacts"]]
    assert "SearchFeedback" in kinds
    assert "SupplementalSearchReport" in kinds


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


def test_search_round_limit_and_stop_are_enforced(tmp_path) -> None:
    service, review, project_id = _review_service(tmp_path, max_rounds=1)
    review.apply_feedback(
        project_id,
        SearchFeedback(action="refine", suggested_queries=["first supplement"]),
    )

    with pytest.raises(WorkflowPrerequisiteError, match="round limit"):
        review.apply_feedback(
            project_id,
            SearchFeedback(action="refine", suggested_queries=["second supplement"]),
        )

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
