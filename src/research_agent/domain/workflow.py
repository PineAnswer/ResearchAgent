from __future__ import annotations

from research_agent.domain.models import (
    ResearchProject,
    ResearchStage,
    ReviewResult,
    ReviewVerdict,
)


class InvalidTransition(ValueError):
    """Raised when a project attempts an illegal stage transition."""


ALLOWED_TRANSITIONS: dict[ResearchStage, set[ResearchStage]] = {
    ResearchStage.CREATED: {ResearchStage.SEARCHED},
    ResearchStage.SEARCHED: {ResearchStage.SCREENED, ResearchStage.INCONCLUSIVE},
    ResearchStage.SCREENED: {ResearchStage.EXTRACTED, ResearchStage.INCONCLUSIVE},
    ResearchStage.EXTRACTED: {ResearchStage.SYNTHESIZED, ResearchStage.INCONCLUSIVE},
    ResearchStage.SYNTHESIZED: {ResearchStage.REVIEW_PENDING, ResearchStage.INCONCLUSIVE},
    ResearchStage.REVIEW_PENDING: {ResearchStage.REVIEWED, ResearchStage.INCONCLUSIVE},
    ResearchStage.REVIEWED: {
        ResearchStage.COMPLETED,
        ResearchStage.EXTRACTED,
        ResearchStage.INCONCLUSIVE,
    },
    ResearchStage.COMPLETED: set(),
    ResearchStage.INCONCLUSIVE: set(),
}


def validate_transition(
    project: ResearchProject,
    target: ResearchStage,
    review: ReviewResult | None = None,
) -> None:
    allowed = ALLOWED_TRANSITIONS[project.stage]
    if target not in allowed:
        allowed_text = ", ".join(sorted(stage.value for stage in allowed)) or "none"
        raise InvalidTransition(
            f"Illegal transition {project.stage.value} -> {target.value}; allowed: {allowed_text}"
        )

    if target is ResearchStage.REVIEWED and review is None:
        raise InvalidTransition("A structured ReviewResult is required before REVIEWED")

    if project.stage is ResearchStage.REVIEWED and target is ResearchStage.COMPLETED:
        active_review = review or project.current_review
        if active_review is None or active_review.verdict is not ReviewVerdict.PASS:
            raise InvalidTransition("Only a PASS review can move a project to COMPLETED")

    if project.stage is ResearchStage.REVIEWED and target is ResearchStage.EXTRACTED:
        if project.current_review is None or project.current_review.verdict is not ReviewVerdict.REVISE:
            raise InvalidTransition("Only a REVISE review can send a project back to EXTRACTED")
