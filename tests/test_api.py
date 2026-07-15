import asyncio

import httpx

from research_agent.api.app import create_app
from research_agent.domain.models import ResearchStage
from research_agent.infrastructure.config import Settings


def test_health_reports_agent_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    workspace = tmp_path / "filesystem"
    workspace.mkdir()
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=workspace,
    )
    app = create_app(settings)

    async def request_health():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health")

    response = asyncio.run(request_health())
    paths = {route.path for route in app.routes}

    assert app.state.supervisor.graph is not None
    assert response.status_code == 200
    assert response.json()["data"]["agent_available"] is True
    assert {
        "/health",
        "/api/research/invoke",
        "/api/research/stream",
        "/api/projects/{project_id}/search-review",
        "/api/projects/{project_id}/search-feedback",
        "/api/projects/{project_id}/continue",
    } <= paths


def test_api_can_start_in_degraded_mode_without_model_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace = tmp_path / "filesystem"
    workspace.mkdir()
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=workspace,
        enable_fallback=True,
    )

    app = create_app(settings)

    assert app.state.supervisor.graph is None
    assert app.state.supervisor.initialization_error


def test_visual_console_and_project_read_endpoints(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
        enable_fallback=True,
    )
    app = create_app(settings)
    first = app.state.supervisor.service.create_project("first", "question one")
    second = app.state.supervisor.service.create_project("second", "question two")

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return (
                await client.get("/"),
                await client.get("/ui-assets/styles.css"),
                await client.get("/ui-assets/app.js"),
                await client.get("/api/projects?limit=10"),
                await client.get(f"/api/projects/{first.project_id}"),
                await client.get("/api/projects/RP-missing"),
            )

    index, styles, script, projects, snapshot, missing = asyncio.run(exercise_api())

    assert index.status_code == 200
    assert "文献研究测试台" in index.text
    assert styles.status_code == 200
    assert "--accent" in styles.text
    assert script.status_code == 200
    assert "submitFeedback" in script.text
    assert projects.status_code == 200
    assert [item["project_id"] for item in projects.json()["data"]] == [
        second.project_id,
        first.project_id,
    ]
    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["project"]["project_id"] == first.project_id
    assert missing.status_code == 404
    assert missing.json()["detail"] == "project_not_found"


def test_search_review_api_can_show_accept_and_continue_project(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
    )
    app = create_app(settings)
    supervisor = app.state.supervisor
    project = supervisor.service.create_project("topic", "question")
    supervisor.service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["query"],
            "candidates": [
                {"paper_id": "P1", "title": "Paper", "source": "OpenAlex"}
            ],
            "selection_notes": [],
        },
        target=ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    supervisor.search_review.begin_review(project.project_id)

    class FakeGraph:
        async def ainvoke(self, inputs, config):
            del inputs, config
            return {"messages": []}

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            review = await client.get(
                f"/api/projects/{project.project_id}/search-review"
            )
            accepted = await client.post(
                f"/api/projects/{project.project_id}/search-feedback",
                json={"action": "accept", "comment": "Keep P1."},
            )
            supervisor.graph = FakeGraph()
            continued = await client.post(
                f"/api/projects/{project.project_id}/continue",
                json={},
            )
            return review, accepted, continued

    review, accepted, continued = asyncio.run(exercise_api())

    assert review.status_code == 200
    assert review.json()["data"]["awaiting_input"] is True
    assert accepted.status_code == 200
    assert accepted.json()["data"]["ready_to_continue"] is True
    assert continued.status_code == 200
    assert continued.json()["data"]["project_status"]["stage"] == "SCREENED"
