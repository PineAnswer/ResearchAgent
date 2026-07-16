from __future__ import annotations

from typing import Any, Protocol

from research_agent.domain.models import (
    ArtifactRecord,
    ResearchProject,
    ResearchStage,
    ReviewResult,
    StateEvent,
)


class ResearchRepositoryPort(Protocol):
    """Persistence contract required by the application service."""

    def create_project(self, topic: str, research_question: str) -> ResearchProject: ...

    def get_project(self, project_id: str) -> ResearchProject: ...

    def list_projects(self, limit: int = 20) -> list[ResearchProject]: ...

    def delete_project(self, project_id: str) -> None: ...

    def transition(
        self,
        project_id: str,
        target: ResearchStage,
        actor: str,
        review: ReviewResult | None = None,
    ) -> ResearchProject: ...

    def reopen_interrupted_workflow(
        self,
        project_id: str,
        target: ResearchStage,
        actor: str,
        review: ReviewResult,
    ) -> ResearchProject: ...

    def save_artifact(
        self,
        project_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> ArtifactRecord: ...

    def save_artifact_and_transition(
        self,
        project_id: str,
        kind: str,
        payload: dict[str, Any],
        target: ResearchStage,
        actor: str,
        review: ReviewResult | None = None,
    ) -> tuple[ArtifactRecord, ResearchProject]: ...

    def list_artifacts(
        self,
        project_id: str,
        kind: str | None = None,
    ) -> list[ArtifactRecord]: ...

    def list_events(self, project_id: str) -> list[StateEvent]: ...


class ArtifactExporterPort(Protocol):
    """File export contract used after a successful database write."""

    def export_artifact(self, artifact: ArtifactRecord) -> None: ...

    def export_snapshot(self, project_id: str, snapshot: dict[str, Any]) -> None: ...

    def delete_project(self, project_id: str) -> None: ...
