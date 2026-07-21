from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from research_agent.domain.models import (
    ArtifactRecord,
    ConversationMessage,
    ConversationRun,
    DEFAULT_USER_ID,
    LibraryAttachment,
    LibraryArtifact,
    LibraryChunk,
    LibraryCollection,
    LibraryNote,
    LibraryPaper,
    PaperAnnotation,
    ProjectPaper,
    ResearchConversation,
    ResearchProject,
    ResearchStage,
    ReviewResult,
    StateEvent,
    UserAccount,
)
from research_agent.domain.workflow import InvalidTransition, validate_transition


class ProjectNotFound(KeyError):
    pass


class LibraryPaperNotFound(KeyError):
    pass


class LibraryCollectionNotFound(KeyError):
    pass


class ConversationNotFound(KeyError):
    pass


class ConversationRunNotFound(KeyError):
    pass


class ActiveConversationRunError(RuntimeError):
    pass


_current_user_id: ContextVar[str] = ContextVar(
    "research_agent_current_user_id",
    default=DEFAULT_USER_ID,
)


class SqliteResearchRepository:
    """Persist research projects, artifacts, and append-only workflow events."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def current_user_id(self) -> str:
        return _current_user_id.get()

    @contextmanager
    def user_scope(self, user_id: str) -> Iterator[None]:
        token = _current_user_id.set(user_id)
        try:
            yield
        finally:
            _current_user_id.reset(token)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @classmethod
    def _ensure_column(
        cls,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        if column not in cls._table_columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'local-user',
                    conversation_id TEXT NOT NULL DEFAULT ''
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

                CREATE TABLE IF NOT EXISTS paper_annotations (
                    annotation_id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    attachment_id TEXT,
                    kind TEXT NOT NULL,
                    page INTEGER,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'local-user',
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(attachment_id) REFERENCES library_attachments(attachment_id)
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

                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    research_question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS conversation_runs (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS user_library_papers (
                    user_id TEXT NOT NULL,
                    library_id TEXT NOT NULL,
                    saved INTEGER NOT NULL DEFAULT 1,
                    starred INTEGER NOT NULL DEFAULT 0,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, library_id),
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(library_id) REFERENCES library_papers(library_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_library_papers_saved_updated
                    ON library_papers(saved, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_project_papers_library
                    ON project_papers(library_id);
                CREATE INDEX IF NOT EXISTS idx_collection_papers_library
                    ON library_collection_papers(library_id);
                CREATE INDEX IF NOT EXISTS idx_library_notes_paper
                    ON library_notes(library_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_paper_annotations_paper
                    ON paper_annotations(user_id, library_id, page, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_library_attachments_paper
                    ON library_attachments(library_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_library_chunks_paper
                    ON library_chunks(library_id, attachment_id, page, chunk_index);
                CREATE INDEX IF NOT EXISTS idx_library_artifacts_paper
                    ON library_artifacts(library_id, kind, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
                    ON conversations(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversation_runs_conversation
                    ON conversation_runs(conversation_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_runs_one_active
                    ON conversation_runs(conversation_id)
                    WHERE status IN ('queued', 'running');
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation
                    ON conversation_messages(conversation_id, message_id);
                CREATE INDEX IF NOT EXISTS idx_user_library_updated
                    ON user_library_papers(user_id, saved, updated_at DESC);
                """
            )
            self._ensure_column(
                connection,
                "projects",
                "user_id",
                "TEXT NOT NULL DEFAULT 'local-user'",
            )
            self._ensure_column(
                connection,
                "projects",
                "conversation_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "conversations",
                "pinned_at",
                "TEXT",
            )
            for table in (
                "library_collections",
                "library_notes",
                "paper_annotations",
                "library_attachments",
                "library_chunks",
                "library_artifacts",
            ):
                self._ensure_column(
                    connection,
                    table,
                    "user_id",
                    "TEXT NOT NULL DEFAULT 'local-user'",
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_projects_user_updated
                ON projects(user_id, updated_at DESC)
                """
            )
            self._migrate_identity_and_conversations(connection)

    def _migrate_identity_and_conversations(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT OR IGNORE INTO users(user_id, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (DEFAULT_USER_ID, "本地用户", now, now),
        )
        connection.execute(
            "UPDATE state_events SET from_stage = 'NARRATED' "
            "WHERE from_stage = 'REVISION_PENDING'"
        )
        connection.execute(
            "UPDATE state_events SET to_stage = 'NARRATED' "
            "WHERE to_stage = 'REVISION_PENDING'"
        )
        rows = connection.execute(
            """
            SELECT project_id, payload_json, updated_at, user_id, conversation_id
            FROM projects
            """
        ).fetchall()
        for row in rows:
            project_payload = json.loads(row["payload_json"])
            if project_payload.get("stage") == "REVISION_PENDING":
                has_narrative = connection.execute(
                    """
                    SELECT 1 FROM artifacts
                    WHERE project_id = ? AND kind = 'NarrativeReview'
                    LIMIT 1
                    """,
                    (row["project_id"],),
                ).fetchone()
                target_stage = (
                    ResearchStage.COMPLETED
                    if has_narrative is not None
                    else ResearchStage.OUTLINED
                )
                project_payload["stage"] = target_stage.value
                migrated_payload = json.dumps(
                    project_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
                connection.execute(
                    "UPDATE projects SET payload_json = ? WHERE project_id = ?",
                    (migrated_payload, row["project_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO state_events(
                        project_id, from_stage, to_stage, actor, created_at,
                        artifact_hash, review_verdict
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["project_id"],
                        ResearchStage.NARRATED.value,
                        target_stage.value,
                        "workflow-migration",
                        now,
                        hashlib.sha256(migrated_payload.encode("utf-8")).hexdigest(),
                        None,
                    ),
                )
            project = ResearchProject.model_validate(project_payload)
            user_id = str(row["user_id"] or project.user_id or DEFAULT_USER_ID)
            conversation_id = str(
                row["conversation_id"]
                or project.conversation_id
                or f"CV-{uuid.uuid5(uuid.NAMESPACE_URL, project.project_id).hex[:16]}"
            )
            project.user_id = user_id
            project.conversation_id = conversation_id
            connection.execute(
                """
                UPDATE projects
                SET payload_json = ?, user_id = ?, conversation_id = ?
                WHERE project_id = ?
                """,
                (project.model_dump_json(), user_id, conversation_id, project.project_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations(
                    conversation_id, user_id, project_id, thread_id, title,
                    research_question, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    user_id,
                    project.project_id,
                    conversation_id,
                    project.topic,
                    project.research_question,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )
        library_rows = connection.execute(
            "SELECT library_id, payload_json, updated_at FROM library_papers"
        ).fetchall()
        for row in library_rows:
            paper = LibraryPaper.model_validate_json(row["payload_json"])
            connection.execute(
                """
                INSERT OR IGNORE INTO user_library_papers(
                    user_id, library_id, saved, starred, tags_json, archived_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_USER_ID,
                    row["library_id"],
                    int(paper.saved),
                    int(paper.starred),
                    json.dumps(paper.tags, ensure_ascii=False),
                    paper.archived_at.isoformat() if paper.archived_at else None,
                    paper.created_at.isoformat(),
                    row["updated_at"],
                ),
            )
        connection.execute(
            """
            UPDATE conversation_runs
            SET status = 'interrupted',
                message = '服务重启前运行未正常结束，可安全恢复',
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            (now, now),
        )

    def _ensure_user(
        self,
        user_id: str,
        display_name: str = "本地用户",
    ) -> UserAccount:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO users(
                    user_id, display_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (user_id, display_name, now.isoformat(), now.isoformat()),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return UserAccount(
            user_id=row["user_id"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_current_user(self) -> UserAccount:
        return self._ensure_user(self.current_user_id)

    def resolve_user_session(
        self,
        raw_token: str | None,
        *,
        create_isolated_user: bool = True,
    ) -> tuple[UserAccount, str | None]:
        """Resolve a browser session for shared-local or isolated-user operation."""
        now = datetime.now(UTC)
        token_hash = (
            hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            if raw_token
            else ""
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            shared_user = None
            if not create_isolated_user:
                shared_user = connection.execute(
                    """
                    SELECT u.*
                    FROM users AS u
                    LEFT JOIN projects AS p ON p.user_id = u.user_id
                    GROUP BY u.user_id
                    ORDER BY COUNT(p.project_id) DESC,
                             CASE WHEN u.user_id = ? THEN 0 ELSE 1 END,
                             u.created_at
                    LIMIT 1
                    """,
                    (DEFAULT_USER_ID,),
                ).fetchone()
                if shared_user is None:
                    raise RuntimeError("Local shared user is unavailable")
            if token_hash:
                row = connection.execute(
                    """
                    SELECT u.*
                    FROM user_sessions AS s
                    JOIN users AS u ON u.user_id = s.user_id
                    WHERE s.token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
                if row is not None:
                    if shared_user is not None:
                        connection.execute(
                            """
                            UPDATE user_sessions
                            SET user_id = ?, last_seen_at = ?
                            WHERE token_hash = ?
                            """,
                            (
                                shared_user["user_id"],
                                now.isoformat(),
                                token_hash,
                            ),
                        )
                        row = shared_user
                    else:
                        connection.execute(
                            """
                            UPDATE user_sessions
                            SET last_seen_at = ?
                            WHERE token_hash = ?
                            """,
                            (now.isoformat(), token_hash),
                        )
                    return (
                        UserAccount(
                            user_id=row["user_id"],
                            display_name=row["display_name"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                        ),
                        None,
                    )

            if shared_user is not None:
                user_id = str(shared_user["user_id"])
                display_name = str(shared_user["display_name"])
            elif (
                connection.execute("SELECT 1 FROM user_sessions LIMIT 1").fetchone()
                is None
            ):
                user_id = DEFAULT_USER_ID
                display_name = "本地用户"
            else:
                user_id = f"USR-{uuid.uuid4().hex[:16]}"
                display_name = "新用户"
            connection.execute(
                """
                INSERT OR IGNORE INTO users(
                    user_id, display_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (user_id, display_name, now.isoformat(), now.isoformat()),
            )
            new_token = secrets.token_urlsafe(32)
            connection.execute(
                """
                INSERT INTO user_sessions(
                    session_id, user_id, token_hash, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"SES-{uuid.uuid4().hex}",
                    user_id,
                    hashlib.sha256(new_token.encode("utf-8")).hexdigest(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return (
            UserAccount(
                user_id=row["user_id"],
                display_name=row["display_name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            ),
            new_token,
        )

    def create_conversation(
        self,
        topic: str,
        research_question: str,
    ) -> tuple[ResearchConversation, ResearchProject]:
        user_id = self.current_user_id
        self._ensure_user(user_id)
        now = datetime.now(UTC)
        conversation_id = f"CV-{uuid.uuid4().hex[:16]}"
        thread_id = f"thread-{uuid.uuid4().hex}"
        prefix = now.strftime("RP-%Y%m%d")
        project = ResearchProject(
            project_id=f"{prefix}-{uuid.uuid4().hex[:8]}",
            topic=topic.strip(),
            research_question=research_question.strip(),
            user_id=user_id,
            conversation_id=conversation_id,
            created_at=now,
            updated_at=now,
        )
        conversation = ResearchConversation(
            conversation_id=conversation_id,
            user_id=user_id,
            project_id=project.project_id,
            thread_id=thread_id,
            title=project.topic,
            research_question=project.research_question,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO projects(
                    project_id, payload_json, updated_at, user_id, conversation_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    project.model_dump_json(),
                    now.isoformat(),
                    user_id,
                    conversation_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO conversations(
                    conversation_id, user_id, project_id, thread_id, title,
                    research_question, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    user_id,
                    project.project_id,
                    thread_id,
                    conversation.title,
                    conversation.research_question,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO conversation_messages(
                    conversation_id, user_id, role, content, run_id, created_at
                ) VALUES (?, ?, 'user', ?, NULL, ?)
                """,
                (conversation_id, user_id, research_question.strip(), now.isoformat()),
            )
        return conversation, project

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> ResearchConversation:
        pinned_at = row["pinned_at"]
        return ResearchConversation(
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            project_id=row["project_id"],
            thread_id=row["thread_id"],
            title=row["title"],
            research_question=row["research_question"],
            pinned=bool(pinned_at),
            pinned_at=pinned_at,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_conversation(self, conversation_id: str) -> ResearchConversation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFound(conversation_id)
        return self._conversation_from_row(row)

    def get_project_conversation(self, project_id: str) -> ResearchConversation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM conversations
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFound(project_id)
        return self._conversation_from_row(row)

    def list_conversations(self, limit: int = 50) -> list[ResearchConversation]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversations
                WHERE user_id = ?
                ORDER BY (pinned_at IS NOT NULL) DESC, pinned_at DESC, updated_at DESC
                LIMIT ?
                """,
                (self.current_user_id, safe_limit),
            ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
    ) -> ResearchConversation:
        conversation = self.get_conversation(conversation_id)
        clean_title = title.strip() if title is not None else None
        if clean_title == "":
            raise ValueError("Conversation title cannot be empty")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE conversations
                SET title = COALESCE(?, title),
                    pinned_at = CASE
                        WHEN ? IS NULL THEN pinned_at
                        WHEN ? = 1 THEN COALESCE(pinned_at, ?)
                        ELSE NULL
                    END,
                    updated_at = ?
                WHERE conversation_id = ? AND user_id = ?
                """,
                (
                    clean_title,
                    None if pinned is None else int(pinned),
                    None if pinned is None else int(pinned),
                    now,
                    now,
                    conversation_id,
                    conversation.user_id,
                ),
            )
        return self.get_conversation(conversation_id)

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> ConversationRun:
        return ConversationRun(
            run_id=row["run_id"],
            user_id=row["user_id"],
            conversation_id=row["conversation_id"],
            project_id=row["project_id"],
            thread_id=row["thread_id"],
            kind=row["kind"],
            status=row["status"],
            phase=row["phase"],
            message=row["message"],
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
        )

    def create_conversation_run(
        self,
        conversation_id: str,
        kind: str,
    ) -> ConversationRun:
        conversation = self.get_conversation(conversation_id)
        now = datetime.now(UTC)
        run = ConversationRun(
            run_id=f"RUN-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
            user_id=conversation.user_id,
            conversation_id=conversation.conversation_id,
            project_id=conversation.project_id,
            thread_id=conversation.thread_id,
            kind=kind,
            created_at=now,
            updated_at=now,
            message="正在准备文献检索",
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO conversation_runs(
                        run_id, user_id, conversation_id, project_id, thread_id,
                        kind, status, phase, message, error, created_at,
                        started_at, finished_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.user_id,
                        run.conversation_id,
                        run.project_id,
                        run.thread_id,
                        run.kind,
                        run.status,
                        run.phase,
                        run.message,
                        run.error,
                        run.created_at.isoformat(),
                        None,
                        None,
                        run.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            active = self.get_active_conversation_run(conversation_id)
            raise ActiveConversationRunError(
                f"Conversation already has an active run: {active.run_id if active else 'unknown'}"
            ) from exc
        return run

    def get_conversation_run(self, run_id: str) -> ConversationRun:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM conversation_runs
                WHERE run_id = ? AND user_id = ?
                """,
                (run_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise ConversationRunNotFound(run_id)
        return self._run_from_row(row)

    def list_conversation_runs(self, conversation_id: str) -> list[ConversationRun]:
        self.get_conversation(conversation_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_runs
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY created_at DESC
                """,
                (conversation_id, self.current_user_id),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def get_active_conversation_run(
        self,
        conversation_id: str,
    ) -> ConversationRun | None:
        self.get_conversation(conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM conversation_runs
                WHERE conversation_id = ? AND user_id = ?
                  AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id, self.current_user_id),
            ).fetchone()
        return self._run_from_row(row) if row else None

    def update_conversation_run(
        self,
        run_id: str,
        **changes: Any,
    ) -> ConversationRun:
        run = self.get_conversation_run(run_id)
        allowed = {
            "status",
            "phase",
            "message",
            "error",
            "started_at",
            "finished_at",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return run
        updates["updated_at"] = datetime.now(UTC)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = [
            value.isoformat() if isinstance(value, datetime) else value
            for value in updates.values()
        ]
        params.extend([run_id, self.current_user_id])
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE conversation_runs
                SET {assignments}
                WHERE run_id = ? AND user_id = ?
                """,
                params,
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE conversation_id = ? AND user_id = ?
                """,
                (
                    updates["updated_at"].isoformat(),
                    run.conversation_id,
                    self.current_user_id,
                ),
            )
        return self.get_conversation_run(run_id)

    def append_conversation_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        run_id: str | None = None,
    ) -> ConversationMessage:
        conversation = self.get_conversation(conversation_id)
        message = ConversationMessage(
            conversation_id=conversation_id,
            user_id=conversation.user_id,
            role=role,
            content=content.strip(),
            run_id=run_id,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversation_messages(
                    conversation_id, user_id, role, content, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message.conversation_id,
                    message.user_id,
                    message.role,
                    message.content,
                    message.run_id,
                    message.created_at.isoformat(),
                ),
            )
            message.message_id = cursor.lastrowid
        return message

    def list_conversation_messages(
        self,
        conversation_id: str,
    ) -> list[ConversationMessage]:
        self.get_conversation(conversation_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY message_id
                """,
                (conversation_id, self.current_user_id),
            ).fetchall()
        return [
            ConversationMessage(
                message_id=row["message_id"],
                conversation_id=row["conversation_id"],
                user_id=row["user_id"],
                role=row["role"],
                content=row["content"],
                run_id=row["run_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def create_project(
        self,
        topic: str,
        research_question: str,
        *,
        user_id: str | None = None,
        conversation_id: str = "",
    ) -> ResearchProject:
        owner_id = user_id or self.current_user_id
        self._ensure_user(owner_id)
        prefix = datetime.now(UTC).strftime("RP-%Y%m%d")
        project = ResearchProject(
            project_id=f"{prefix}-{uuid.uuid4().hex[:8]}",
            topic=topic.strip(),
            research_question=research_question.strip(),
            user_id=owner_id,
            conversation_id=conversation_id,
        )
        payload = project.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    project_id, payload_json, updated_at, user_id, conversation_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    payload,
                    project.updated_at.isoformat(),
                    owner_id,
                    conversation_id,
                ),
            )
        return project

    def get_project(self, project_id: str) -> ResearchProject:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM projects
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return ResearchProject.model_validate_json(row["payload_json"])

    def list_projects(self, limit: int = 20) -> list[ResearchProject]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT projects.payload_json
                FROM projects
                LEFT JOIN conversations
                    ON conversations.project_id = projects.project_id
                    AND conversations.user_id = projects.user_id
                WHERE projects.user_id = ?
                ORDER BY
                    (conversations.pinned_at IS NOT NULL) DESC,
                    conversations.pinned_at DESC,
                    projects.updated_at DESC
                LIMIT ?
                """,
                (self.current_user_id, safe_limit),
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
                """
                SELECT 1 FROM projects
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, self.current_user_id),
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
                """
                SELECT lp.payload_json, ulp.saved, ulp.starred, ulp.tags_json,
                       ulp.archived_at, ulp.created_at AS user_created_at,
                       ulp.updated_at AS user_updated_at
                FROM library_papers AS lp
                JOIN user_library_papers AS ulp ON ulp.library_id = lp.library_id
                WHERE lp.library_id = ? AND ulp.user_id = ?
                """,
                (library_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise LibraryPaperNotFound(library_id)
        return self._user_library_paper(row)

    def _scoped_canonical_key(self, canonical_key: str) -> str:
        """Keep each user's editable bibliographic record physically isolated."""
        if self.current_user_id == DEFAULT_USER_ID:
            return canonical_key
        prefix = f"user:{self.current_user_id}:"
        return (
            canonical_key
            if canonical_key.startswith(prefix)
            else f"{prefix}{canonical_key}"
        )

    def get_library_paper_by_key(self, canonical_key: str) -> LibraryPaper | None:
        scoped_key = self._scoped_canonical_key(canonical_key)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT lp.payload_json, ulp.saved, ulp.starred, ulp.tags_json,
                       ulp.archived_at, ulp.created_at AS user_created_at,
                       ulp.updated_at AS user_updated_at
                FROM library_papers AS lp
                JOIN user_library_papers AS ulp ON ulp.library_id = lp.library_id
                WHERE lp.canonical_key = ? AND ulp.user_id = ?
                """,
                (scoped_key, self.current_user_id),
            ).fetchone()
        return self._user_library_paper(row) if row else None

    @staticmethod
    def _user_library_paper(row: sqlite3.Row) -> LibraryPaper:
        paper = LibraryPaper.model_validate_json(row["payload_json"])
        return LibraryPaper.model_validate(
            {
                **paper.model_dump(),
                "saved": bool(row["saved"]),
                "starred": bool(row["starred"]),
                "tags": json.loads(row["tags_json"] or "[]"),
                "archived_at": row["archived_at"],
                "created_at": row["user_created_at"],
                "updated_at": row["user_updated_at"],
            }
        )

    def save_library_paper(
        self,
        paper: LibraryPaper,
        canonical_key: str,
    ) -> LibraryPaper:
        canonical_key = self._scoped_canonical_key(canonical_key)
        payload = paper.model_dump_json()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT library_id FROM library_papers WHERE canonical_key = ?",
                (canonical_key,),
            ).fetchone()
            if existing is not None and existing["library_id"] != paper.library_id:
                paper.library_id = str(existing["library_id"])
                payload = paper.model_dump_json()
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
            connection.execute(
                """
                INSERT INTO user_library_papers(
                    user_id, library_id, saved, starred, tags_json, archived_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, library_id) DO UPDATE SET
                    saved = excluded.saved,
                    starred = excluded.starred,
                    tags_json = excluded.tags_json,
                    archived_at = excluded.archived_at,
                    updated_at = excluded.updated_at
                """,
                (
                    self.current_user_id,
                    paper.library_id,
                    int(paper.saved),
                    int(paper.starred),
                    json.dumps(paper.tags, ensure_ascii=False),
                    paper.archived_at.isoformat() if paper.archived_at else None,
                    paper.created_at.isoformat(),
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
        clauses: list[str] = ["ulp.user_id = ?"]
        params: list[Any] = [self.current_user_id]
        if saved_only:
            clauses.append("ulp.saved = 1")
        if not include_archived:
            clauses.append("ulp.archived_at IS NULL")
        if query.strip():
            clauses.append("LOWER(lp.payload_json) LIKE ?")
            params.append(f"%{query.strip().casefold()}%")
        sql = """
            SELECT lp.payload_json, ulp.saved, ulp.starred, ulp.tags_json,
                   ulp.archived_at, ulp.created_at AS user_created_at,
                   ulp.updated_at AS user_updated_at
            FROM library_papers AS lp
            JOIN user_library_papers AS ulp ON ulp.library_id = lp.library_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ulp.updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._user_library_paper(row) for row in rows]

    def archive_library_paper(self, library_id: str) -> LibraryPaper:
        paper = self.get_library_paper(library_id)
        now = datetime.now(UTC)
        paper.archived_at = now
        paper.updated_at = now
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE user_library_papers
                SET archived_at = ?, updated_at = ?
                WHERE user_id = ? AND library_id = ?
                """,
                (
                    now.isoformat(),
                    now.isoformat(),
                    self.current_user_id,
                    library_id,
                ),
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
            connection.execute(
                """
                DELETE FROM project_papers
                WHERE library_id = ? AND project_id IN (
                    SELECT project_id FROM projects WHERE user_id = ?
                )
                """,
                (library_id, self.current_user_id),
            )
            connection.execute(
                """
                DELETE FROM library_collection_papers
                WHERE library_id = ? AND collection_id IN (
                    SELECT collection_id FROM library_collections WHERE user_id = ?
                )
                """,
                (library_id, self.current_user_id),
            )
            for table in (
                "library_notes",
                "paper_annotations",
                "library_chunks",
                "library_artifacts",
                "library_attachments",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE library_id = ? AND user_id = ?",
                    (library_id, self.current_user_id),
                )
            connection.execute(
                """
                DELETE FROM user_library_papers
                WHERE library_id = ? AND user_id = ?
                """,
                (library_id, self.current_user_id),
            )
            remaining = connection.execute(
                """
                SELECT 1 FROM user_library_papers WHERE library_id = ?
                UNION ALL
                SELECT 1 FROM project_papers WHERE library_id = ?
                LIMIT 1
                """,
                (library_id, library_id),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    "DELETE FROM library_papers WHERE library_id = ?",
                    (library_id,),
                )

    def create_library_collection(
        self, collection: LibraryCollection
    ) -> LibraryCollection:
        if collection.parent_id:
            self._get_library_collection(collection.parent_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_collections(
                    collection_id, name, parent_id, payload_json, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    collection.collection_id,
                    collection.name,
                    collection.parent_id,
                    collection.model_dump_json(),
                    collection.updated_at.isoformat(),
                    self.current_user_id,
                ),
            )
        return collection

    def _get_library_collection(self, collection_id: str) -> LibraryCollection:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM library_collections
                WHERE collection_id = ? AND user_id = ?
                """,
                (collection_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise LibraryCollectionNotFound(collection_id)
        return LibraryCollection.model_validate_json(row["payload_json"])

    def list_library_collections(self) -> list[LibraryCollection]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json, parent_id FROM library_collections
                WHERE user_id = ? ORDER BY name COLLATE NOCASE
                """,
                (self.current_user_id,),
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
                WHERE collection_id = ? AND user_id = ?
                """,
                (
                    collection.name,
                    collection.parent_id,
                    collection.model_dump_json(),
                    collection.updated_at.isoformat(),
                    collection.collection_id,
                    self.current_user_id,
                ),
            )
        return collection

    def delete_library_collection(self, collection_id: str) -> None:
        self._get_library_collection(collection_id)
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM library_collections
                WHERE collection_id = ? AND user_id = ?
                """,
                (collection_id, self.current_user_id),
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
        self._get_library_collection(collection_id)
        self.get_library_paper(library_id)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM library_collection_papers WHERE collection_id = ? AND library_id = ?",
                (collection_id, library_id),
            )

    def list_paper_collection_ids(self, library_id: str) -> list[str]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT lcp.collection_id
                FROM library_collection_papers AS lcp
                JOIN library_collections AS lc ON lc.collection_id = lcp.collection_id
                WHERE lcp.library_id = ? AND lc.user_id = ?
                """,
                (library_id, self.current_user_id),
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
                INSERT INTO library_notes(
                    note_id, library_id, payload_json, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    user_id = excluded.user_id
                """,
                (
                    note.note_id,
                    note.library_id,
                    note.model_dump_json(),
                    note.updated_at.isoformat(),
                    self.current_user_id,
                ),
            )
        return note

    def list_library_notes(self, library_id: str) -> list[LibraryNote]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM library_notes
                WHERE library_id = ? AND user_id = ?
                ORDER BY updated_at DESC
                """,
                (library_id, self.current_user_id),
            ).fetchall()
        return [LibraryNote.model_validate_json(row["payload_json"]) for row in rows]

    def delete_library_note(self, note_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM library_notes WHERE note_id = ? AND user_id = ?",
                (note_id, self.current_user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(note_id)

    def save_paper_annotation(self, annotation: PaperAnnotation) -> PaperAnnotation:
        self.get_library_paper(annotation.library_id)
        if annotation.attachment_id:
            attachment = self.get_library_attachment(annotation.attachment_id)
            if attachment.library_id != annotation.library_id:
                raise ValueError("Annotation attachment belongs to another paper")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_annotations(
                    annotation_id, library_id, attachment_id, kind, page,
                    payload_json, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(annotation_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    attachment_id = excluded.attachment_id,
                    kind = excluded.kind,
                    page = excluded.page,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    user_id = excluded.user_id
                """,
                (
                    annotation.annotation_id,
                    annotation.library_id,
                    annotation.attachment_id,
                    annotation.kind,
                    annotation.page,
                    annotation.model_dump_json(),
                    annotation.updated_at.isoformat(),
                    self.current_user_id,
                ),
            )
        return annotation

    def list_paper_annotations(self, library_id: str) -> list[PaperAnnotation]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM paper_annotations
                WHERE library_id = ? AND user_id = ?
                ORDER BY COALESCE(page, 2147483647), updated_at DESC
                """,
                (library_id, self.current_user_id),
            ).fetchall()
        return [PaperAnnotation.model_validate_json(row["payload_json"]) for row in rows]

    def get_paper_annotation(self, annotation_id: str) -> PaperAnnotation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM paper_annotations
                WHERE annotation_id = ? AND user_id = ?
                """,
                (annotation_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(annotation_id)
        return PaperAnnotation.model_validate_json(row["payload_json"])

    def delete_paper_annotation(self, annotation_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM paper_annotations
                WHERE annotation_id = ? AND user_id = ?
                """,
                (annotation_id, self.current_user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(annotation_id)

    def save_library_attachment(
        self, attachment: LibraryAttachment
    ) -> LibraryAttachment:
        self.get_library_paper(attachment.library_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_attachments(
                    attachment_id, library_id, payload_json, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(attachment_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    user_id = excluded.user_id
                """,
                (
                    attachment.attachment_id,
                    attachment.library_id,
                    attachment.model_dump_json(),
                    attachment.updated_at.isoformat(),
                    self.current_user_id,
                ),
            )
        return attachment

    def list_library_attachments(self, library_id: str) -> list[LibraryAttachment]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM library_attachments
                WHERE library_id = ? AND user_id = ?
                ORDER BY updated_at DESC
                """,
                (library_id, self.current_user_id),
            ).fetchall()
        return [LibraryAttachment.model_validate_json(row["payload_json"]) for row in rows]

    def get_library_attachment(self, attachment_id: str) -> LibraryAttachment:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM library_attachments
                WHERE attachment_id = ? AND user_id = ?
                """,
                (attachment_id, self.current_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(attachment_id)
        return LibraryAttachment.model_validate_json(row["payload_json"])

    def delete_library_attachment(self, attachment_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM library_attachments
                WHERE attachment_id = ? AND user_id = ?
                """,
                (attachment_id, self.current_user_id),
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
                """
                DELETE FROM library_chunks
                WHERE attachment_id = ? AND user_id = ?
                """,
                (attachment_id, self.current_user_id),
            )
            connection.executemany(
                """
                INSERT INTO library_chunks(
                    chunk_id, library_id, attachment_id, page, chunk_index,
                    text, content_hash, payload_json, created_at
                    , user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        self.current_user_id,
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
        clauses: list[str] = ["user_id = ?"]
        params: list[Any] = [self.current_user_id]
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
                    payload_json, created_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    library_id = excluded.library_id,
                    attachment_id = excluded.attachment_id,
                    kind = excluded.kind,
                    mode = excluded.mode,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at,
                    user_id = excluded.user_id
                """,
                (
                    artifact.artifact_id,
                    artifact.library_id,
                    artifact.attachment_id,
                    artifact.kind,
                    artifact.mode,
                    artifact.model_dump_json(),
                    artifact.created_at.isoformat(),
                    self.current_user_id,
                ),
            )
        return artifact

    def list_library_artifacts(
        self,
        library_id: str,
        kind: str | None = None,
    ) -> list[LibraryArtifact]:
        self.get_library_paper(library_id)
        params: list[Any] = [library_id, self.current_user_id]
        sql = """
            SELECT payload_json FROM library_artifacts
            WHERE library_id = ? AND user_id = ?
        """
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
        canonical_key = self._scoped_canonical_key(canonical_key)
        if primary.library_id == duplicate_id:
            raise ValueError("Primary and duplicate paper must differ")
        self.get_library_paper(primary.library_id)
        self.get_library_paper(duplicate_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO library_collection_papers(collection_id, library_id, created_at)
                SELECT lcp.collection_id, ?, lcp.created_at
                FROM library_collection_papers AS lcp
                JOIN library_collections AS lc ON lc.collection_id = lcp.collection_id
                WHERE lcp.library_id = ? AND lc.user_id = ?
                """,
                (primary.library_id, duplicate_id, self.current_user_id),
            )
            duplicate_relations = connection.execute(
                """
                SELECT pp.* FROM project_papers AS pp
                JOIN projects AS p ON p.project_id = pp.project_id
                WHERE pp.library_id = ? AND p.user_id = ?
                """,
                (duplicate_id, self.current_user_id),
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
                """
                SELECT note_id, payload_json FROM library_notes
                WHERE library_id = ? AND user_id = ?
                """,
                (duplicate_id, self.current_user_id),
            ).fetchall()
            for row in note_rows:
                note = LibraryNote.model_validate_json(row["payload_json"])
                note.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_notes SET library_id = ?, payload_json = ? WHERE note_id = ?",
                    (primary.library_id, note.model_dump_json(), row["note_id"]),
                )
            annotation_rows = connection.execute(
                """
                SELECT annotation_id, payload_json FROM paper_annotations
                WHERE library_id = ? AND user_id = ?
                """,
                (duplicate_id, self.current_user_id),
            ).fetchall()
            for row in annotation_rows:
                annotation = PaperAnnotation.model_validate_json(row["payload_json"])
                annotation.library_id = primary.library_id
                connection.execute(
                    """
                    UPDATE paper_annotations
                    SET library_id = ?, payload_json = ?
                    WHERE annotation_id = ?
                    """,
                    (
                        primary.library_id,
                        annotation.model_dump_json(),
                        row["annotation_id"],
                    ),
                )
            attachment_rows = connection.execute(
                """
                SELECT attachment_id, payload_json FROM library_attachments
                WHERE library_id = ? AND user_id = ?
                """,
                (duplicate_id, self.current_user_id),
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
                """
                SELECT chunk_id, payload_json FROM library_chunks
                WHERE library_id = ? AND user_id = ?
                """,
                (duplicate_id, self.current_user_id),
            ).fetchall()
            for row in chunk_rows:
                chunk = LibraryChunk.model_validate_json(row["payload_json"])
                chunk.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_chunks SET library_id = ?, payload_json = ? WHERE chunk_id = ?",
                    (primary.library_id, chunk.model_dump_json(), row["chunk_id"]),
                )
            artifact_rows = connection.execute(
                """
                SELECT artifact_id, payload_json FROM library_artifacts
                WHERE library_id = ? AND user_id = ?
                """,
                (duplicate_id, self.current_user_id),
            ).fetchall()
            for row in artifact_rows:
                artifact = LibraryArtifact.model_validate_json(row["payload_json"])
                artifact.library_id = primary.library_id
                connection.execute(
                    "UPDATE library_artifacts SET library_id = ?, payload_json = ? WHERE artifact_id = ?",
                    (primary.library_id, artifact.model_dump_json(), row["artifact_id"]),
                )
            connection.execute(
                """
                DELETE FROM project_papers
                WHERE library_id = ? AND project_id IN (
                    SELECT project_id FROM projects WHERE user_id = ?
                )
                """,
                (duplicate_id, self.current_user_id),
            )
            connection.execute(
                """
                DELETE FROM library_collection_papers
                WHERE library_id = ? AND collection_id IN (
                    SELECT collection_id FROM library_collections WHERE user_id = ?
                )
                """,
                (duplicate_id, self.current_user_id),
            )
            connection.execute(
                """
                DELETE FROM user_library_papers
                WHERE library_id = ? AND user_id = ?
                """,
                (duplicate_id, self.current_user_id),
            )
            remaining = connection.execute(
                """
                SELECT 1 FROM user_library_papers WHERE library_id = ?
                UNION ALL SELECT 1 FROM project_papers WHERE library_id = ?
                LIMIT 1
                """,
                (duplicate_id, duplicate_id),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    "DELETE FROM library_papers WHERE library_id = ?",
                    (duplicate_id,),
                )
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
            connection.execute(
                """
                UPDATE user_library_papers
                SET saved = ?, starred = ?, tags_json = ?, archived_at = ?,
                    updated_at = ?
                WHERE user_id = ? AND library_id = ?
                """,
                (
                    int(primary.saved),
                    int(primary.starred),
                    json.dumps(primary.tags, ensure_ascii=False),
                    primary.archived_at.isoformat() if primary.archived_at else None,
                    primary.updated_at.isoformat(),
                    self.current_user_id,
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
                self.get_library_paper(row["library_id"]),
            )
            for row in rows
        ]

    def list_library_paper_projects(self, library_id: str) -> list[ProjectPaper]:
        self.get_library_paper(library_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT pp.* FROM project_papers AS pp
                JOIN projects AS p ON p.project_id = pp.project_id
                WHERE pp.library_id = ? AND p.user_id = ?
                ORDER BY pp.updated_at DESC
                """,
                (library_id, self.current_user_id),
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
                """
                SELECT payload_json FROM projects
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, self.current_user_id),
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
                """
                UPDATE projects
                SET payload_json = ?, updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (
                    payload,
                    project.updated_at.isoformat(),
                    project_id,
                    self.current_user_id,
                ),
            )
            connection.execute(
                """
                UPDATE conversations SET updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (project.updated_at.isoformat(), project_id, self.current_user_id),
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
        review: ReviewResult | None = None,
    ) -> ResearchProject:
        """Reopen a false completion or recoverable operational interruption."""
        allowed_targets = {
            ResearchStage.SEARCH_REVIEW_PENDING,
            ResearchStage.SCREENED,
            ResearchStage.EXTRACTED,
            ResearchStage.SYNTHESIZED,
            ResearchStage.REVIEW_PENDING,
            ResearchStage.REVIEWED,
            ResearchStage.OUTLINED,
            ResearchStage.NARRATED,
        }
        if target not in allowed_targets:
            raise InvalidTransition(
                f"Recovery cannot target {target.value}; allowed: "
                + ", ".join(sorted(stage.value for stage in allowed_targets))
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_json FROM projects
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, self.current_user_id),
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)

            project = ResearchProject.model_validate_json(row["payload_json"])
            allowed_sources = (
                {ResearchStage.SCREENED, ResearchStage.INCONCLUSIVE}
                if target is ResearchStage.SEARCH_REVIEW_PENDING
                else {ResearchStage.COMPLETED, ResearchStage.INCONCLUSIVE}
            )
            if project.stage not in allowed_sources:
                raise InvalidTransition(
                    "Workflow recovery is not allowed from the current stage: "
                    + project.stage.value
                )

            from_stage = project.stage
            project.stage = target
            if review is not None:
                project.current_review = review
            project.updated_at = datetime.now(UTC)
            payload = project.model_dump_json()
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            connection.execute(
                """
                UPDATE projects
                SET payload_json = ?, updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (
                    payload,
                    project.updated_at.isoformat(),
                    project_id,
                    self.current_user_id,
                ),
            )
            connection.execute(
                """
                UPDATE conversations SET updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (project.updated_at.isoformat(), project_id, self.current_user_id),
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
                """
                SELECT payload_json FROM projects
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, self.current_user_id),
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
                """
                UPDATE projects
                SET payload_json = ?, updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (
                    project_payload,
                    project.updated_at.isoformat(),
                    project_id,
                    self.current_user_id,
                ),
            )
            connection.execute(
                """
                UPDATE conversations SET updated_at = ?
                WHERE project_id = ? AND user_id = ?
                """,
                (project.updated_at.isoformat(), project_id, self.current_user_id),
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
