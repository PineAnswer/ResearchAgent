import asyncio

import httpx
import pytest

from research_agent.api.app import create_app
from research_agent.application.library_service import LibraryService
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import (
    ConversationNotFound,
    SqliteResearchRepository,
)


def test_repository_isolates_conversations_and_editable_library_records(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "agent.db")
    first_user, first_token = repository.resolve_user_session(None)

    with repository.user_scope(first_user.user_id):
        conversation, first_project = repository.create_conversation(
            "Geo landmarks",
            "Can street-level landmarks support image geolocation?",
        )
        first_paper = LibraryService(repository).upsert_paper(
            {
                "title": "Owner copy",
                "doi": "10.1000/shared",
            }
        )

    second_user, _ = repository.resolve_user_session(None)
    assert second_user.user_id != first_user.user_id

    with repository.user_scope(second_user.user_id):
        assert repository.list_projects() == []
        with pytest.raises(ConversationNotFound):
            repository.get_conversation(conversation.conversation_id)
        second_paper = LibraryService(repository).upsert_paper(
            {
                "title": "Independent copy",
                "doi": "10.1000/shared",
            }
        )

    assert first_token
    assert first_paper.library_id != second_paper.library_id
    with repository.user_scope(first_user.user_id):
        assert repository.get_project(first_project.project_id).user_id == first_user.user_id
        assert repository.get_library_paper(first_paper.library_id).title == "Owner copy"


def test_local_shared_mode_rebinds_existing_browser_sessions_to_primary_user(
    tmp_path,
) -> None:
    repository = SqliteResearchRepository(tmp_path / "agent.db")
    primary_user, _ = repository.resolve_user_session(None)
    with repository.user_scope(primary_user.user_id):
        conversation, project = repository.create_conversation(
            "Existing history",
            "Should every local browser see this project?",
        )

    isolated_user, isolated_token = repository.resolve_user_session(None)
    assert isolated_user.user_id != primary_user.user_id
    assert isolated_token

    shared_user, replacement_token = repository.resolve_user_session(
        isolated_token,
        create_isolated_user=False,
    )
    assert replacement_token is None
    assert shared_user.user_id == primary_user.user_id
    with repository.user_scope(shared_user.user_id):
        assert repository.get_conversation(conversation.conversation_id).project_id == (
            project.project_id
        )


def test_default_api_mode_shares_history_across_local_browsers(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    filesystem_root = tmp_path / "filesystem"
    filesystem_root.mkdir()
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=filesystem_root,
            enable_fallback=True,
        )
    )
    project = app.state.supervisor.service.create_project(
        "Shared local history",
        "Can two browsers read the same local project?",
    )

    async def exercise_api() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as codex_browser:
            first = await codex_browser.get("/api/projects")
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as regular_browser:
            second = await regular_browser.get("/api/projects")

        assert first.json()["data"][0]["project_id"] == project.project_id
        assert second.json()["data"][0]["project_id"] == project.project_id

    asyncio.run(exercise_api())


def test_api_runs_two_conversations_concurrently_and_blocks_cross_user_access(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    filesystem_root = tmp_path / "filesystem"
    filesystem_root.mkdir()
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=filesystem_root,
            enable_fallback=True,
            multi_user_mode=True,
        )
    )

    async def exercise_api() -> None:
        started: list[str] = []
        release = asyncio.Event()

        async def fake_start(project_id, thread_id, **_options):
            started.append(f"{project_id}:{thread_id}")
            await release.wait()
            return {"messages": [{"content": f"finished {project_id}"}]}

        app.state.supervisor.astart_project = fake_start
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as owner:
            first = await owner.post(
                "/api/conversations",
                json={"topic": "Geo A", "research_question": "Question A"},
            )
            second = await owner.post(
                "/api/conversations",
                json={"topic": "Geo B", "research_question": "Question B"},
            )
            for _ in range(100):
                if len(started) == 2:
                    break
                await asyncio.sleep(0.01)

            assert first.status_code == 202
            assert second.status_code == 202
            assert len(started) == 2

            first_data = first.json()["data"]
            second_data = second.json()["data"]
            listing = await owner.get("/api/projects")
            assert [bool(item["active_run"]) for item in listing.json()["data"]] == [
                True,
                True,
            ]
            for data in (first_data, second_data):
                switched = await owner.get(
                    f"/api/conversations/{data['conversation']['conversation_id']}"
                )
                assert switched.status_code == 200

            duplicate = await owner.post(
                "/api/conversations/"
                f"{first_data['conversation']['conversation_id']}/continue",
                json={},
            )
            assert duplicate.status_code == 409

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as outsider:
                outsider_list = await outsider.get("/api/projects")
                outsider_read = await outsider.get(
                    "/api/conversations/"
                    f"{first_data['conversation']['conversation_id']}"
                )
                assert outsider_list.json()["data"] == []
                assert outsider_read.status_code == 404

            release.set()
            await asyncio.sleep(0.05)
        await app.state.run_manager.shutdown()

    asyncio.run(exercise_api())
