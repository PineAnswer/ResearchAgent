import pytest

from research_agent.domain.models import (
    ResearchProject,
    ResearchStage,
    ReviewResult,
    ReviewVerdict,
)
from research_agent.domain.workflow import InvalidTransition, validate_transition


def make_project(stage: ResearchStage) -> ResearchProject:
    return ResearchProject(
        project_id="RP-test",
        topic="test",
        research_question="test?",
        stage=stage,
    )


def test_cannot_skip_search_and_complete() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(make_project(ResearchStage.CREATED), ResearchStage.COMPLETED)


def test_review_result_is_required() -> None:
    with pytest.raises(InvalidTransition, match="ReviewResult"):
        validate_transition(make_project(ResearchStage.REVIEW_PENDING), ResearchStage.REVIEWED)


def test_pass_review_allows_outlining_but_not_direct_completion() -> None:
    project = make_project(ResearchStage.REVIEWED)
    project.current_review = ReviewResult(verdict=ReviewVerdict.PASS)
    validate_transition(project, ResearchStage.OUTLINED)
    with pytest.raises(InvalidTransition):
        validate_transition(project, ResearchStage.COMPLETED)


def test_revise_review_returns_to_extraction() -> None:
    project = make_project(ResearchStage.REVIEWED)
    project.current_review = ReviewResult(verdict=ReviewVerdict.REVISE)
    validate_transition(project, ResearchStage.EXTRACTED)


def test_searched_project_can_end_inconclusive() -> None:
    validate_transition(
        make_project(ResearchStage.SEARCHED),
        ResearchStage.INCONCLUSIVE,
    )


def test_inconclusive_is_terminal() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(
            make_project(ResearchStage.INCONCLUSIVE),
            ResearchStage.SEARCHED,
        )
