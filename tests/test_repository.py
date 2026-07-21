import json
import sqlite3

import pytest

from research_agent.domain.models import ResearchStage, ReviewResult, ReviewVerdict
from research_agent.infrastructure.sqlite_repository import (
    ProjectNotFound,
    SqliteResearchRepository,
)


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
        ResearchStage.SEARCH_REVIEW_PENDING,
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
    project = repository.transition(project.project_id, ResearchStage.OUTLINED, actor="pi")
    project = repository.transition(project.project_id, ResearchStage.COMPLETED, actor="editor")

    assert project.stage is ResearchStage.COMPLETED
    assert len(repository.list_events(project.project_id)) == 9


def test_legacy_revision_pending_project_is_migrated_to_completed(tmp_path) -> None:
    database_path = tmp_path / "test.db"
    repository = SqliteResearchRepository(database_path)
    project = repository.create_project("topic", "question")
    repository.save_artifact(
        project.project_id,
        "NarrativeReview",
        {"sections": [{"section_id": "section-1", "content": "review"}]},
    )
    legacy_payload = project.model_dump(mode="json")
    legacy_payload["stage"] = "REVISION_PENDING"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE projects SET payload_json = ? WHERE project_id = ?",
            (json.dumps(legacy_payload, default=str), project.project_id),
        )

    migrated_repository = SqliteResearchRepository(database_path)
    migrated = migrated_repository.get_project(project.project_id)
    migration_event = migrated_repository.list_events(project.project_id)[-1]

    assert migrated.stage is ResearchStage.COMPLETED
    assert migration_event.from_stage is ResearchStage.NARRATED
    assert migration_event.to_stage == ResearchStage.COMPLETED.value
    assert migration_event.actor == "workflow-migration"


def test_delete_project_removes_project_artifacts_and_events(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    project = repository.create_project("topic", "question")
    repository.save_artifact(project.project_id, "SearchReport", {"candidates": []})
    repository.transition(
        project.project_id,
        ResearchStage.SEARCHED,
        actor="literature-scout",
    )

    repository.delete_project(project.project_id)

    with pytest.raises(ProjectNotFound):
        repository.get_project(project.project_id)
    assert repository.list_artifacts(project.project_id) == []
    assert repository.list_events(project.project_id) == []
    with pytest.raises(ProjectNotFound):
        repository.delete_project(project.project_id)
