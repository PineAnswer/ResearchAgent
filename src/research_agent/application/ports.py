from __future__ import annotations

from typing import Any, Protocol

from research_agent.domain.models import (
    ArtifactRecord,
    LibraryAttachment,
    LibraryArtifact,
    LibraryChunk,
    LibraryCollection,
    LibraryNote,
    LibraryPaper,
    ProjectPaper,
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

    def get_library_paper(self, library_id: str) -> LibraryPaper: ...

    def get_library_paper_by_key(self, canonical_key: str) -> LibraryPaper | None: ...

    def save_library_paper(
        self,
        paper: LibraryPaper,
        canonical_key: str,
    ) -> LibraryPaper: ...

    def list_library_papers(
        self,
        query: str = "",
        *,
        saved_only: bool = True,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[LibraryPaper]: ...

    def archive_library_paper(self, library_id: str) -> LibraryPaper: ...

    def restore_library_paper(self, library_id: str) -> LibraryPaper: ...

    def permanently_delete_library_paper(self, library_id: str) -> None: ...

    def create_library_collection(
        self, collection: LibraryCollection
    ) -> LibraryCollection: ...

    def list_library_collections(self) -> list[LibraryCollection]: ...

    def update_library_collection(
        self, collection: LibraryCollection
    ) -> LibraryCollection: ...

    def delete_library_collection(self, collection_id: str) -> None: ...

    def add_paper_to_collection(self, collection_id: str, library_id: str) -> None: ...

    def remove_paper_from_collection(self, collection_id: str, library_id: str) -> None: ...

    def list_paper_collection_ids(self, library_id: str) -> list[str]: ...

    def list_collection_paper_ids(self, collection_id: str) -> list[str]: ...

    def save_library_note(self, note: LibraryNote) -> LibraryNote: ...

    def list_library_notes(self, library_id: str) -> list[LibraryNote]: ...

    def delete_library_note(self, note_id: str) -> None: ...

    def save_library_attachment(
        self, attachment: LibraryAttachment
    ) -> LibraryAttachment: ...

    def get_library_attachment(self, attachment_id: str) -> LibraryAttachment: ...

    def list_library_attachments(self, library_id: str) -> list[LibraryAttachment]: ...

    def delete_library_attachment(self, attachment_id: str) -> None: ...

    def replace_library_chunks(
        self,
        library_id: str,
        attachment_id: str,
        chunks: list[LibraryChunk],
    ) -> list[LibraryChunk]: ...

    def list_library_chunks(
        self,
        *,
        library_ids: list[str] | None = None,
        attachment_id: str | None = None,
        chunk_ids: list[str] | None = None,
        limit: int = 5000,
    ) -> list[LibraryChunk]: ...

    def save_library_artifact(self, artifact: LibraryArtifact) -> LibraryArtifact: ...

    def list_library_artifacts(
        self,
        library_id: str,
        kind: str | None = None,
    ) -> list[LibraryArtifact]: ...

    def merge_library_papers(
        self,
        primary: LibraryPaper,
        duplicate_id: str,
        canonical_key: str,
    ) -> LibraryPaper: ...

    def link_project_paper(self, relation: ProjectPaper) -> ProjectPaper: ...

    def list_project_papers(
        self,
        project_id: str,
    ) -> list[tuple[ProjectPaper, LibraryPaper]]: ...

    def list_library_paper_projects(self, library_id: str) -> list[ProjectPaper]: ...

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
