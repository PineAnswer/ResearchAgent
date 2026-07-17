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
        "/api/projects/{project_id}",
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
                await client.get("/ui-assets/vendor/lucide.min.js"),
                await client.get("/ui-assets/vendor/marked.umd.js"),
                await client.get("/ui-assets/fonts/InterVariable.woff2"),
                await client.get("/ui-assets/fonts/NotoSerifSC-Regular.otf"),
                await client.get("/ui-assets/licenses/LUCIDE-LICENSE.txt"),
                await client.get("/ui-assets/licenses/INTER-LICENSE.txt"),
                await client.get("/ui-assets/licenses/NOTO-SERIF-CJK-LICENSE.txt"),
                await client.get("/ui-assets/licenses/MARKED-LICENSE.md"),
                await client.get("/api/projects?limit=10"),
                await client.get(f"/api/projects/{first.project_id}"),
                await client.get("/api/projects/RP-missing"),
            )

    (
        index,
        styles,
        script,
        lucide,
        marked,
        inter_font,
        noto_font,
        lucide_license,
        inter_license,
        noto_license,
        marked_license,
        projects,
        snapshot,
        missing,
    ) = asyncio.run(exercise_api())

    assert index.status_code == 200
    assert "文献研究工作台" in index.text
    assert "continueButtonLabel" in index.text
    assert "vendor/marked.umd.js" in index.text
    assert styles.status_code == 200
    assert "--accent" in styles.text
    assert script.status_code == 200
    assert "submitFeedback" in script.text
    assert "继续生成综述" in script.text
    assert "成果待补全" in script.text
    assert "写作待恢复" in script.text
    assert "本次停止来自主编输出格式故障" in script.text
    assert "renderMarkdown" in script.text
    assert lucide.status_code == 200
    assert "createIcons" in lucide.text
    assert marked.status_code == 200
    assert "parseMarkdown" in marked.text
    assert inter_font.status_code == 200
    assert len(inter_font.content) > 100_000
    assert noto_font.status_code == 200
    assert len(noto_font.content) > 1_000_000
    for license_response in (
        lucide_license,
        inter_license,
        noto_license,
        marked_license,
    ):
        assert license_response.status_code == 200
        assert len(license_response.content) > 1_000
    assert projects.status_code == 200
    assert [item["project_id"] for item in projects.json()["data"]] == [
        second.project_id,
        first.project_id,
    ]
    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["project"]["project_id"] == first.project_id
    assert missing.status_code == 404
    assert missing.json()["detail"] == "project_not_found"


def test_api_deletes_project_records_and_exported_files(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
        enable_fallback=True,
    )
    app = create_app(settings)
    project = app.state.supervisor.service.create_project("delete me", "question")
    output_dir = tmp_path / "outputs" / project.project_id
    assert output_dir.is_dir()

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            deleted = await client.delete(f"/api/projects/{project.project_id}")
            missing = await client.get(f"/api/projects/{project.project_id}")
            deleted_again = await client.delete(f"/api/projects/{project.project_id}")
            return deleted, missing, deleted_again

    deleted, missing, deleted_again = asyncio.run(exercise_api())

    assert deleted.status_code == 200
    assert deleted.json()["data"]["project_id"] == project.project_id
    assert missing.status_code == 404
    assert deleted_again.status_code == 404
    assert not output_dir.exists()


def test_library_api_saves_project_paper_and_imports_bibtex(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
        enable_fallback=True,
    )
    app = create_app(settings)
    project = app.state.supervisor.service.create_project("library", "question")

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            saved = await client.post(
                f"/api/projects/{project.project_id}/library",
                json={
                    "paper_id": "W10",
                    "title": "Saved candidate",
                    "doi": "10.1000/saved",
                    "source": "OpenAlex",
                },
            )
            imported = await client.post(
                "/api/library/import",
                json={
                    "format": "bibtex",
                    "content": "@article{two, title={Imported paper}, year={2023}}",
                    "tags": ["imported"],
                },
            )
            library = await client.get("/api/library")
            project_library = await client.get(
                f"/api/projects/{project.project_id}/library"
            )
            exported = await client.get("/api/library/export?format=ris")
            return saved, imported, library, project_library, exported

    saved, imported, library, project_library, exported = asyncio.run(exercise_api())

    assert saved.status_code == 200
    assert saved.json()["data"]["paper"]["saved"] is True
    assert imported.status_code == 200
    assert len(library.json()["data"]) == 2
    assert len(project_library.json()["data"]) == 1
    assert exported.status_code == 200
    assert "TI  - Saved candidate" in exported.text


def test_library_management_api_supports_organizing_notes_and_compare(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=tmp_path / "filesystem",
        enable_fallback=True,
    )
    app = create_app(settings)

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/api/library/papers",
                json={"title": "First study", "abstract": "Traceability improved."},
            )
            second = await client.post(
                "/api/library/papers",
                json={"title": "Second study", "abstract": "A baseline result."},
            )
            first_id = first.json()["data"]["library_id"]
            second_id = second.json()["data"]["library_id"]
            collection = await client.post(
                "/api/library/collections", json={"name": "Thesis"}
            )
            collection_id = collection.json()["data"]["collection_id"]
            bulk = await client.post(
                "/api/library/bulk",
                json={
                    "library_ids": [first_id, second_id],
                    "action": "add_collection",
                    "value": collection_id,
                },
            )
            note = await client.post(
                f"/api/library/papers/{first_id}/notes",
                json={"content": "Reusable note"},
            )
            attachment = await client.post(
                f"/api/library/papers/{first_id}/attachments",
                json={"name": "PDF", "url": "https://example.test/paper.pdf"},
            )
            uploaded = await client.post(
                f"/api/library/papers/{first_id}/attachments/upload",
                params={"filename": "local.pdf", "media_type": "application/pdf"},
                content=b"%PDF-1.4 test content",
                headers={"Content-Type": "application/pdf"},
            )
            uploaded_id = uploaded.json()["data"]["attachment_id"]
            downloaded = await client.get(
                f"/api/library/attachments/{uploaded_id}/content"
            )
            detail = await client.get(f"/api/library/papers/{first_id}")
            comparison = await client.post(
                "/api/library/compare",
                json={"library_ids": [first_id, second_id]},
            )
            answer = await client.post(
                "/api/library/assistant",
                json={
                    "library_ids": [first_id, second_id],
                    "question": "Which paper mentions traceability?",
                },
            )
            overview = await client.get("/api/library/overview")
            return (
                bulk,
                note,
                attachment,
                uploaded,
                downloaded,
                detail,
                comparison,
                answer,
                overview,
            )

    (
        bulk,
        note,
        attachment,
        uploaded,
        downloaded,
        detail,
        comparison,
        answer,
        overview,
    ) = asyncio.run(exercise_api())

    assert bulk.status_code == 200
    assert note.status_code == 200
    assert attachment.status_code == 200
    assert uploaded.status_code == 200
    assert downloaded.content == b"%PDF-1.4 test content"
    assert detail.json()["data"]["notes"][0]["content"] == "Reusable note"
    assert len(detail.json()["data"]["attachments"]) == 2
    assert len(comparison.json()["data"]["rows"]) == 2
    assert "Traceability improved" in answer.json()["data"]["answer"]
    assert overview.json()["data"]["collections"][0]["paper_count"] == 2


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
        inputs = None

        async def ainvoke(self, inputs, config):
            del config
            self.inputs = inputs
            return {"messages": []}

    fake_graph = FakeGraph()

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
            supervisor.graph = fake_graph
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
    prompt = fake_graph.inputs["messages"][0]["content"]
    assert "screened_context" in prompt
    assert '"included_paper_ids": [\n    "P1"\n  ]' in prompt
    assert "不要为了寻找 ScreeningDecision 去读取或 grep 完整大快照" in prompt
