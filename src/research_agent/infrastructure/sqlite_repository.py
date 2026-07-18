from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

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
from research_agent.domain.workflow import InvalidTransition, validate_transition


class ProjectNotFound(KeyError):
    pass


class LibraryPaperNotFound(KeyError):
    pass


class LibraryCollectionNotFound(KeyError):
    pass


class SqliteResearchRepository:
    """Persist research projects, artifacts, and append-only workflow events."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    from_stage TEXT NOT NULL,
                    to_stage TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL,
                    review_verdict TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS library_papers (
                    library_id TEXT PRIMARY KEY,
                    canonical_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    saved INTEGER NOT NULL DEFAULT 1,
                    archived_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_papers (
                    project_id TEXT NOT NULL,
                    library_id TEXT NOT NULL,
                    source_paper_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, library_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id),
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                );

                CREATE TABLE IF NOT EXISTS library_collections (
                    collection_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_id TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(parent_id) REFERENCES library_collections(collection_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS library_collection_papers (
                    collection_id TEXT NOT NULL,
                    library_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(collection_id, library_id),
                    FOREIGN KEY(collection_id) REFERENCES library_collections(collection_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS library_notes (
                    note_id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS library_attachments (
                    attachment_id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS library_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    attachment_id TEXT NOT NULL,
                    page INTEGER,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(attachment_id) REFERENCES library_attachments(attachment_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS library_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    attachment_id TEXT,
                    kind TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(attachment_id) REFERENCES library_attachments(attachment_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_library_papers_saved_updated
                    ON library_papers(saved, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_project_papers_library
                    ON project_papers(library_id);
                CREATE INDEX IF NOT EXISTS idx_collection_papers_library
                    ON library_collection_papers(library_id);
                CREATE INDEX IF NOT EXISTS idx_library_notes_paper
                    ON library_notes(library_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_library_attachments_paper
                    ON library_attachments(library_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_library_chunks_paper
                    ON library_chunks(library_id, attachment_id, page, chunk_index);
                CREATE INDEX IF NOT EXISTS idx_library_artifacts_paper
                    ON library_artifacts(library_id, kind, created_at DESC);
                """
            )

    def create_project(self, topic: str, research_question: str) -> ResearchProject:
        prefix = datetime.now(UTC).strftime("RP-%Y%m%d")
        project = ResearchProject(
            project_id=f"{prefix}-{uuid.uuid4().hex[:8]}",
            topic=topic.strip(),
            research_question=research_question.strip(),
        )
        payload = project.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects(project_id, payload_json, updated_at) VALUES (?, ?, ?)",
                (project.project_id, payload, project.updated_at.isoformat()),
            )
        return project

    def get_project(self, project_id: str) -> ResearchProject:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return ResearchProject.model_validate_json(row["payload_json"])

    def list_projects(self, limit: int = 20) -> list[ResearchProject]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM projects ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [
            ResearchProject.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def delete_project(self, project_id: str) -> None:
        """Delete one project and all of its database-owned records atomically."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            connection.execute(
                "DELETE FROM state_events WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                "DELETE FROM artifacts WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                "DELETE FROM project_papers WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                "DELETE FROM projects WHERE project_id = ?", (project_id,)
            )

    def get_library_paper(self, library_id: str) -> LibraryPaper:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM library_papers WHERE library_id = ?",
                (library_id,),
            ).fetchone()
        if row is None:
            raise LibraryPaperNotFound(library_id)
        return LibraryPaper.model_validate_json(row["payload_json"])

    def get_library_paper_by_key(self, canonical_key: str) -> LibraryPaper | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM library_papers WHERE canonical_key = ?",
                (canonical_key,),
            ).fetchone()
        return LibraryPaper.model_validate_json(row["payload_json"]) if row else None

    def save_library_paper(
        self,
        paper: LibraryPaper,
        canonical_key: str,
    ) -> LibraryPaper:
        payload = paper.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_papers(
                    library_id, canonical_key, payload_json, saved, archived_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(library_id) DO UPDATE SET
                    canonical_key = excluded.canonical_key,
                    payload_json = excluded.payload_json,
                    saved = excluded.saved,
                    archived_at = excluded.archived_at,
                    updated_at = excluded.updated_at
                """,
                (
                    paper.library_id,
                    canonical_key,
                    payload,
                    int(paper.saved),
                    paper.archived_at.isoformat() if paper.archived_at else None,
                    paper.updated_at.isoformat(),
                ),
            )
        return paper

    def list_library_papers(
        self,
        query: str = "",
        *,
        saved_only: bool = True,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[LibraryPaper]:
        clauses: list[str] = []
        params: list[Any] = []
        if saved_only:
            clauses.append("saved = 1")
        if not include_archived:
            clauses.append("archived_at IS NULL")
        if query.strip():
            clauses.append("LOWER(payload_json) LIKE ?")
            params.append(f"%{query.strip().casefold()}%")
        sql = "SELECT payload_json FROM library_papers"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [LibraryPaper.model_validate_json(row["payload_json"]) for row in rows]

    def archive_library_paper(self, library_id: str) -> LibraryPaper:
        paper = self.get_library_paper(library_id)
        now = datetime.now(UTC)
        paper.archived_at = now
        paper.updated_at = now
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE library_papers
                SET payload_json = ?, archived_at = ?, updated_at = ?
                WHERE library_id = ?
                """,
                (paper.model_dump_json(), now.isoformat(), now.isoformat(), library_id),
            )
        return paper

    def restore_library_paper(self, library_id: str) -> LibraryPaper:
        paper = self.get_library_paper(library_id)
        paper.archived_at = None
        paper.saved = True
        paper.updated_at = datetime.now(UTC)
        return self.save_library_paper(
            paper,
            self._canonical_key_for_stored_paper(library_id),
        )

    def _canonical_key_for_stored_paper(self, library_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT canonical_key FROM library_papers WHERE library_id = ?",
                (library_id,),
            ).fetchone()
        if row is None:
            raise LibraryPaperNotFound(library_id)
        return str(row["canonical_key"])

    def permanently_delete_library_paper(self, library_id: str) -> None:
        paper = self.get_library_paper(library_id)
        if paper.archived_at is None:
            raise ValueError("Paper must be archived before permanent deletion")
        with self._connect() as connection:
            connection.execute("DELETE FROM project_papers WHERE library_id = ?", (library_id,))
            connection.execute("DELETE FROM library_collection_papers WHERE library_id = ?", (library_id,))
            connection.execute("DELETE FROM library_notes WHERE library_id = ?", (library_id,))
            connection.execute("DELETE FROM library_chunks WHERE library_id = ?", (library_id,))
            connection.execute("DELETE FROM library_artifacts WHERE library_id = ?", (library_id,))
            connection.execute("DELETE FROM library_attachments WHERE library_id = ?", (library_id,))
            connection.execute("DELETE FROM library_papers WHERE library_id = ?", (library_id,))

    def create_library_collection(
        self, collection: LibraryCollection
    ) -> LibraryCollection:
        if collection.parent_id:
            self._get_library_collection(collection.parent_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_collections(
                    collection_id, name, parent_id, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    collection.collection_id,
                    collection.name,
                    collection.parent_id,
                    collection.model_dump_json(),
                    collection.updated_at.isoformat(),
                ),
            )
        return collection

    def _get_library_collection(self, collection_id: str) -> LibraryCollection:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM library_collections WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        if row is None:
            raise LibraryCollectionNotFound(collection_id)
        return LibraryCollection.model_validate_json(row["payload_json"])

    def list_library_collections(self) -> list[LibraryCollection]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json, parent_id FROM library_collections ORDER BY name COLLATE NOCASE"
            ).fetchall()
        collections = []
        for row in rows:
            collection = LibraryCollection.model_validate_json(row["payload_json"])
            collection.parent_id = row["parent_id"]
            collections.append(collection)
        return collections

    def update_library_collection(
        self, collection: LibraryCollection
    ) -> LibraryCollection:
        self._get_library_collection(collection.collection_id)
        if collection.parent_id == collection.collection_id:
            raise ValueError("A collection cannot contain itself")
        if collection.parent_id:
            self._get_library_collection(collection.parent_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE library_collections
                SET name = ?, parent_id = ?, payload_json = ?, updated_at = ?
                WHERE collection_id = ?
                """,
                (
                    collection.name,
                    collection.parent_id,
                    collection.model_dump_json(),
                    collection.updated_at.isoformat(),
                    collection.collection_id,
                ),
            )
        return collection

    def delete_library_collection(self, collection_id: str) -> None:
        self._get_library_collection(collection_id)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM library_collections WHERE collection_id = ?",
                (collection_id,),
            )

    def add_paper_to_collection(self, collection_id: str, library_id: str) -> None:
        self._get_library_collection(collection_id)
        self.get_library_paper(library_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO library_collection_papers(
                    collection_id, library_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (collection_id, library_id, datetime.now(UTC).isoformat()),
            )

    def remove_paper_from_collection(self, collection_id: str, library_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM library_collection_papers WHERE collection_id = ? AND library_id = ?",
                (collection_id, library_id),
            )

    def list_paper_collection_ids(self, library_id: str) -> list[str]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT collection_id FROM library_collection_papers WHERE library_id = ?",
                (library_id,),
            ).fetchall()
        return [str(row["collection_id"]) for row in rows]

    def list_collection_paper_ids(self, collection_id: str) -> list[str]:
        self._get_library_collection(collection_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT library_id FROM library_collection_papers WHERE collection_id = ?",
                (collection_id,),
            ).fetchall()
        return [str(row["library_id"]) for row in rows]

    def save_library_note(self, note: LibraryNote) -> LibraryNote:
        self.get_library_paper(note.library_id)
        if note.project_id:
            self.get_project(note.project_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_notes(note_id, library_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (note.note_id, note.library_id, note.model_dump_json(), note.updated_at.isoformat()),
            )
        return note

    def list_library_notes(self, library_id: str) -> list[LibraryNote]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM library_notes WHERE library_id = ? ORDER BY updated_at DESC",
                (library_id,),
            ).fetchall()
        return [LibraryNote.model_validate_json(row["payload_json"]) for row in rows]

    def delete_library_note(self, note_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM library_notes WHERE note_id = ?", (note_id,))
            if cursor.rowcount == 0:
                raise KeyError(note_id)

    def save_library_attachment(
        self, attachment: LibraryAttachment
    ) -> LibraryAttachment:
        self.get_library_paper(attachment.library_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_attachments(
                    attachment_id, library_id, payload_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(attachment_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    attachment.attachment_id,
                    attachment.library_id,
                    attachment.model_dump_json(),
                    attachment.updated_at.isoformat(),
                ),
            )
        return attachment

    def list_library_attachments(self, library_id: str) -> list[LibraryAttachment]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM library_attachments WHERE library_id = ? ORDER BY updated_at DESC",
                (library_id,),
            ).fetchall()
        return [LibraryAttachment.model_validate_json(row["payload_json"]) for row in rows]

    def get_library_attachment(self, attachment_id: str) -> LibraryAttachment:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM library_attachments WHERE attachment_id = ?",
                (attachment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(attachment_id)
        return LibraryAttachment.model_validate_json(row["payload_json"])

    def delete_library_attachment(self, attachment_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM library_attachments WHERE attachment_id = ?",
                (attachment_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(attachment_id)

    def replace_library_chunks(
        self,
        library_id: str,
        attachment_id: str,
        chunks: list[LibraryChunk],
    ) -> list[LibraryChunk]:
        self.get_library_paper(library_id)
        attachment = self.get_library_attachment(attachment_id)
        if attachment.library_id != library_id:
            raise ValueError("Attachment does not belong to the requested paper")
        if any(
            chunk.library_id != library_id or chunk.attachment_id != attachment_id
            for chunk in chunks
        ):
            raise ValueError("Chunk ownership does not match its attachment")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM library_chunks WHERE attachment_id = ?",
                (attachment_id,),
            )
            connection.executemany(
                """
                INSERT INTO library_chunks(
                    chunk_id, library_id, attachment_id, page, chunk_index,
                    text, content_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.library_id,
                        chunk.attachment_id,
                        chunk.page,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.content_hash,
                        chunk.model_dump_json(),
                        chunk.created_at.isoformat(),
                    )
                    for chunk in chunks
                ],
            )
        return chunks

    def list_library_chunks(
        self,
        *,
        library_ids: list[str] | None = None,
        attachment_id: str | None = None,
        chunk_ids: list[str] | None = None,
        limit: int = 5000,
    ) -> list[LibraryChunk]:
        clauses: list[str] = []
        params: list[Any] = []
        if library_ids:
            placeholders = ",".join("?" for _ in library_ids)
            clauses.append(f"library_id IN ({placeholders})")
            params.extend(library_ids)
        if attachment_id:
            clauses.append("attachment_id = ?")
            params.append(attachment_id)
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            clauses.append(f"chunk_id IN ({placeholders})")
            params.extend(chunk_ids)
        sql = "SELECT payload_json FROM library_chunks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY library_id, attachment_id, page, chunk_index LIMIT ?"
        params.append(max(1, min(int(limit), 20_000)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [LibraryChunk.model_validate_json(row["payload_json"]) for row in rows]

    def save_library_artifact(self, artifact: LibraryArtifact) -> LibraryArtifact:
        self.get_library_paper(artifact.library_id)
        if artifact.attachment_id:
            attachment = self.get_library_attachment(artifact.attachment_id)
            if attachment.library_id != artifact.library_id:
                raise ValueError("Artifact attachment belongs to another paper")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_artifacts(
                    artifact_id, library_id, attachment_id, kind, mode,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    attachment_id = excluded.attachment_id,
                    kind = excluded.kind,
                    mode = excluded.mode,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    artifact.artifact_id,
                    artifact.library_id,
                    artifact.attachment_id,
                    artifact.kind,
                    artifact.mode,
                    artifact.model_dump_json(),
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def list_library_artifacts(
        self,
        library_id: str,
        kind: str | None = None,
    ) -> list[LibraryArtifact]:
        self.get_library_paper(library_id)
        params: list[Any] = [library_id]
        sql = "SELECT payload_json FROM library_artifacts WHERE library_id = ?"
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [LibraryArtifact.model_validate_json(row["payload_json"]) for row in rows]

    def merge_library_papers(
        self,
        primary: LibraryPaper,
        duplicate_id: str,
        canonical_key: str,
    ) -> LibraryPaper:
        if primary.library_id == duplicate_id:
            raise ValueError("Primary and duplicate paper must differ")
        self.get_library_paper(primary.library_id)
        self.get_library_paper(duplicate_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO library_collection_papers(collection_id, library_id, created_at)
                SELECT collection_id, ?, created_at
                FROM library_collection_papers WHERE library_id = ?
                """,
                (primary.library_id, duplicate_id),
            )
            duplicate_relations = connection.execute(
                "SELECT * FROM project_papers WHERE library_id = ?",
                (duplicate_id,),
            ).fetchall()
            for relation in duplicate_relations:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO project_papers(
                        project_id, library_id, source_paper_id, status, reason,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation["project_id"],
                        primary.library_id,
                        relation["source_paper_id"],
                        relation["status"],
                        relation["reason"],
                        relation["created_at"],
                        relation["updated_at"],
                    ),
                )
            note_rows = connection.execute(
                "SELECT note_id, payload_json FROM library_notes WHERE library_id = ?",
                (duplicate_id,),
            ).fetchall()
            for row in note_rows:
                note = LibraryNote.model_validate_json(row["payload_json"])
                note.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_notes SET library_id = ?, payload_json = ? WHERE note_id = ?",
                    (primary.library_id, note.model_dump_json(), row["note_id"]),
                )
            attachment_rows = connection.execute(
                "SELECT attachment_id, payload_json FROM library_attachments WHERE library_id = ?",
                (duplicate_id,),
            ).fetchall()
            for row in attachment_rows:
                attachment = LibraryAttachment.model_validate_json(row["payload_json"])
                attachment.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_attachments SET library_id = ?, payload_json = ? WHERE attachment_id = ?",
                    (
                        primary.library_id,
                        attachment.model_dump_json(),
                        row["attachment_id"],
                    ),
                )
            chunk_rows = connection.execute(
                "SELECT chunk_id, payload_json FROM library_chunks WHERE library_id = ?",
                (duplicate_id,),
            ).fetchall()
            for row in chunk_rows:
                chunk = LibraryChunk.model_validate_json(row["payload_json"])
                chunk.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_chunks SET library_id = ?, payload_json = ? WHERE chunk_id = ?",
                    (primary.library_id, chunk.model_dump_json(), row["chunk_id"]),
                )
            artifact_rows = connection.execute(
                "SELECT artifact_id, payload_json FROM library_artifacts WHERE library_id = ?",
                (duplicate_id,),
            ).fetchall()
            for row in artifact_rows:
                artifact = LibraryArtifact.model_validate_json(row["payload_json"])
                artifact.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_artifacts SET library_id = ?, payload_json = ? WHERE artifact_id = ?",
                    (primary.library_id, artifact.model_dump_json(), row["artifact_id"]),
                )
            connection.execute("DELETE FROM project_papers WHERE library_id = ?", (duplicate_id,))
            connection.execute("DELETE FROM library_collection_papers WHERE library_id = ?", (duplicate_id,))
            connection.execute("DELETE FROM library_papers WHERE library_id = ?", (duplicate_id,))
            connection.execute(
                """
                UPDATE library_papers
                SET canonical_key = ?, payload_json = ?, saved = ?, archived_at = ?, updated_at = ?
                WHERE library_id = ?
                """,
                (
                    canonical_key,
                    primary.model_dump_json(),
                    int(primary.saved),
                    primary.archived_at.isoformat() if primary.archived_at else None,
                    primary.updated_at.isoformat(),
                    primary.library_id,
                ),
            )
        return primary

    def link_project_paper(self, relation: ProjectPaper) -> ProjectPaper:
        self.get_project(relation.project_id)
        self.get_library_paper(relation.library_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_papers(
                    project_id, library_id, source_paper_id, status, reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, library_id) DO UPDATE SET
                    source_paper_id = excluded.source_paper_id,
                    status = excluded.status,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (
                    relation.project_id,
                    relation.library_id,
                    relation.source_paper_id,
                    relation.status,
                    relation.reason,
                    relation.created_at.isoformat(),
                    relation.updated_at.isoformat(),
                ),
            )
        return relation

    def list_project_papers(self, project_id: str) -> list[tuple[ProjectPaper, LibraryPaper]]:
        self.get_project(project_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT pp.*, lp.payload_json
                FROM project_papers AS pp
                JOIN library_papers AS lp ON lp.library_id = pp.library_id
                WHERE pp.project_id = ?
                ORDER BY pp.updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [
            (
                ProjectPaper(
                    project_id=row["project_id"],
                    library_id=row["library_id"],
                    source_paper_id=row["source_paper_id"],
                    status=row["status"],
                    reason=row["reason"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                ),
                LibraryPaper.model_validate_json(row["payload_json"]),
            )
            for row in rows
        ]

    def list_library_paper_projects(self, library_id: str) -> list[ProjectPaper]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM project_papers
                WHERE library_id = ? ORDER BY updated_at DESC
                """,
                (library_id,),
            ).fetchall()
        return [
            ProjectPaper(
                project_id=row["project_id"],
                library_id=row["library_id"],
                source_paper_id=row["source_paper_id"],
                status=row["status"],
                reason=row["reason"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def transition(
        self,
        project_id: str,
        target: ResearchStage,
        actor: str,
        review: ReviewResult | None = None,
    ) -> ResearchProject:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)

            project = ResearchProject.model_validate_json(row["payload_json"])
            validate_transition(project, target, review)
            from_stage = project.stage
            project.stage = target
            if review is not None:
                project.current_review = review
            project.updated_at = datetime.now(UTC)
            payload = project.model_dump_json()
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            connection.execute(
                "UPDATE projects SET payload_json = ?, updated_at = ? WHERE project_id = ?",
                (payload, project.updated_at.isoformat(), project_id),
            )
            connection.execute(
                """
                INSERT INTO state_events(
                    project_id, from_stage, to_stage, actor, created_at,
                    artifact_hash, review_verdict
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    from_stage.value,
                    target.value,
                    actor,
                    project.updated_at.isoformat(),
                    payload_hash,
                    review.verdict.value if review else None,
                ),
            )
        return project

    def reopen_interrupted_workflow(
        self,
        project_id: str,
        target: ResearchStage,
        actor: str,
        review: ReviewResult,
    ) -> ResearchProject:
        """Reopen a false completion or recoverable operational interruption."""
        allowed_targets = {
            ResearchStage.REVIEWED,
            ResearchStage.OUTLINED,
            ResearchStage.NARRATED,
        }
        allowed_sources = {
            ResearchStage.COMPLETED,
            ResearchStage.INCONCLUSIVE,
        }
        if target not in allowed_targets:
            raise InvalidTransition(
                f"Recovery cannot target {target.value}; allowed: "
                + ", ".join(sorted(stage.value for stage in allowed_targets))
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)

            project = ResearchProject.model_validate_json(row["payload_json"])
            if project.stage not in allowed_sources:
                raise InvalidTransition(
                    "Workflow recovery requires COMPLETED or INCONCLUSIVE; current stage is "
                    + project.stage.value
                )

            from_stage = project.stage
            project.stage = target
            project.current_review = review
            project.updated_at = datetime.now(UTC)
            payload = project.model_dump_json()
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            connection.execute(
                "UPDATE projects SET payload_json = ?, updated_at = ? WHERE project_id = ?",
                (payload, project.updated_at.isoformat(), project_id),
            )
            connection.execute(
                """
                INSERT INTO state_events(
                    project_id, from_stage, to_stage, actor, created_at,
                    artifact_hash, review_verdict
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    from_stage.value,
                    target.value,
                    actor,
                    project.updated_at.isoformat(),
                    payload_hash,
                    review.verdict.value,
                ),
            )
        return project

    def save_artifact_and_transition(
        self,
        project_id: str,
        kind: str,
        payload: dict[str, Any],
        target: ResearchStage,
        actor: str,
        review: ReviewResult | None = None,
    ) -> tuple[ArtifactRecord, ResearchProject]:
        """Persist an artifact and its stage transition in one SQLite transaction."""
        record = ArtifactRecord(project_id=project_id, kind=kind, payload=payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)

            project = ResearchProject.model_validate_json(row["payload_json"])
            validate_transition(project, target, review)

            cursor = connection.execute(
                """
                INSERT INTO artifacts(project_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            )
            record.artifact_id = cursor.lastrowid

            from_stage = project.stage
            project.stage = target
            if review is not None:
                project.current_review = review
            project.updated_at = datetime.now(UTC)
            project_payload = project.model_dump_json()
            payload_hash = hashlib.sha256(project_payload.encode("utf-8")).hexdigest()

            connection.execute(
                "UPDATE projects SET payload_json = ?, updated_at = ? WHERE project_id = ?",
                (project_payload, project.updated_at.isoformat(), project_id),
            )
            connection.execute(
                """
                INSERT INTO state_events(
                    project_id, from_stage, to_stage, actor, created_at,
                    artifact_hash, review_verdict
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    from_stage.value,
                    target.value,
                    actor,
                    project.updated_at.isoformat(),
                    payload_hash,
                    review.verdict.value if review else None,
                ),
            )
        return record, project

    def save_artifact(self, project_id: str, kind: str, payload: dict[str, Any]) -> ArtifactRecord:
        self.get_project(project_id)
        record = ArtifactRecord(project_id=project_id, kind=kind, payload=payload)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO artifacts(project_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            )
            record.artifact_id = cursor.lastrowid
        return record

    def list_artifacts(self, project_id: str, kind: str | None = None) -> list[ArtifactRecord]:
        query = "SELECT * FROM artifacts WHERE project_id = ?"
        params: list[Any] = [project_id]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY artifact_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            ArtifactRecord(
                artifact_id=row["artifact_id"],
                project_id=row["project_id"],
                kind=row["kind"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_events(self, project_id: str) -> list[StateEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM state_events WHERE project_id = ? ORDER BY event_id",
                (project_id,),
            ).fetchall()
        return [
            StateEvent(
                event_id=row["event_id"],
                project_id=row["project_id"],
                from_stage=row["from_stage"],
                to_stage=row["to_stage"],
                actor=row["actor"],
                created_at=row["created_at"],
                artifact_hash=row["artifact_hash"],
                review_verdict=row["review_verdict"],
            )
            for row in rows
        ]
