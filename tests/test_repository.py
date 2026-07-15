from research_agent.domain.models import ResearchStage, ReviewResult, ReviewVerdict
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def test_repository_persists_project_events_and_artifacts(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    project = repository.create_project("topic", "question")
    repository.save_artifact(project.project_id, "SearchReport", {"candidates": []})
    project = repository.transition(
        project.project_id,
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    assert repository.get_project(project.project_id).stage is ResearchStage.SEARCHED
    assert repository.list_artifacts(project.project_id)[0].kind == "SearchReport"
    assert repository.list_events(project.project_id)[0].actor == "literature-scout"


def test_full_reviewed_flow(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    project = repository.create_project("topic", "question")
    for stage in [
        ResearchStage.SEARCHED,
        ResearchStage.SCREENED,
        ResearchStage.EXTRACTED,
        ResearchStage.SYNTHESIZED,
        ResearchStage.REVIEW_PENDING,
    ]:
        project = repository.transition(project.project_id, stage, actor="test")

    review = ReviewResult(verdict=ReviewVerdict.PASS)
    project = repository.transition(
        project.project_id,
        ResearchStage.REVIEWED,
        actor="evidence-reviewer",
        review=review,
    )
    project = repository.transition(project.project_id, ResearchStage.COMPLETED, actor="pi")

    assert project.stage is ResearchStage.COMPLETED
    assert len(repository.list_events(project.project_id)) == 7
