import asyncio

import httpx

from research_agent.api.app import create_app
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
    assert {"/health", "/api/research/invoke", "/api/research/stream"} <= paths


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
