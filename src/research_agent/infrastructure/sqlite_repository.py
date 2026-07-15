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
    ResearchProject,
    ResearchStage,
    ReviewResult,
    StateEvent,
)
from research_agent.domain.workflow import validate_transition


class ProjectNotFound(KeyError):
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
