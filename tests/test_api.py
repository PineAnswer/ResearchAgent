import asyncio

import httpx
import research_agent.agents.supervisor as supervisor_module

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


def test_completed_research_supports_scoped_questions_notes_and_annotations(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=tmp_path / "filesystem",
            enable_fallback=True,
        )
    )
    project = app.state.supervisor.service.create_project("研究工作台", "如何提高可追踪性？")
    app.state.supervisor.repository.save_artifact(
        project.project_id,
        "NarrativeReview",
        {
            "title": "可追踪研究综述",
            "abstract": "结构化引用能够提高研究结论的可追踪性。",
            "sections": [
                {
                    "section_id": "methods",
                    "heading": "方法",
                    "content": "证据标记将结论连接至原始论文。",
                    "subsections": [],
                }
            ],
            "references": [],
        },
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            selection_answer = await client.post(
                f"/api/projects/{project.project_id}/assistant",
                json={
                    "scope": "selection",
                    "question": "这句话说明什么？",
                    "selected_text": "结构化引用能够提高研究结论的可追踪性。",
                },
            )
            assert selection_answer.status_code == 200
            assert selection_answer.json()["data"]["scope"] == "selection"
            assert selection_answer.json()["data"]["citations"][0]["source_id"] == "REVIEW_SELECTION"

            missing_selection = await client.post(
                f"/api/projects/{project.project_id}/assistant",
                json={"scope": "selection", "question": "说明什么？"},
            )
            assert missing_selection.status_code == 400

            note = await client.post(
                f"/api/projects/{project.project_id}/notes",
                json={"kind": "note", "content": "后续检查引用覆盖率。"},
            )
            assert note.status_code == 200
            note_id = note.json()["data"]["note_id"]

            annotation = await client.post(
                f"/api/projects/{project.project_id}/notes",
                json={
                    "kind": "annotation",
                    "selected_text": "证据标记将结论连接至原始论文。",
                    "content": "这里需要补充量化指标。",
                },
            )
            assert annotation.status_code == 200

            invalid_annotation = await client.post(
                f"/api/projects/{project.project_id}/notes",
                json={"kind": "annotation", "content": "缺少选段"},
            )
            assert invalid_annotation.status_code == 400

            notes = await client.get(f"/api/projects/{project.project_id}/notes")
            assert notes.status_code == 200
            assert {item["kind"] for item in notes.json()["data"]} == {"note", "annotation"}

            deleted = await client.delete(
                f"/api/projects/{project.project_id}/notes/{note_id}"
            )
            assert deleted.status_code == 200
            remaining = await client.get(f"/api/projects/{project.project_id}/notes")
            assert len(remaining.json()["data"]) == 1

    asyncio.run(exercise())


def test_selection_question_preserves_personalized_llm_answer_without_inline_marker(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=tmp_path / "filesystem",
            enable_fallback=True,
        )
    )
    supervisor = app.state.supervisor
    project = supervisor.service.create_project("个性化解释", "五种范式是什么意思？")
    supervisor.repository.save_artifact(
        project.project_id,
        "NarrativeReview",
        {
            "title": "协同智能综述",
            "abstract": "研究总结了五种范式。",
            "sections": [],
            "references": [],
        },
    )
    captured: dict[str, str] = {}

    class FakeStructuredModel:
        async def ainvoke(self, messages):
            captured["system"] = messages[0].content
            captured["human"] = messages[1].content
            return {
                "answer": (
                    "这里的“范式”指解决问题的五类技术路径。提示工程调整输入，"
                    "检索增强补充外部材料，微调改变模型参数，工具增强扩展执行能力，"
                    "验证机制负责复核结果；这比单纯列出五个名称更能说明它们的分工。"
                ),
                "cited_source_ids": ["REVIEW_SELECTION"],
                "used_library_ids": [],
                "coverage_note": "解释仅覆盖用户选中的段落。",
            }

    class FakeModel:
        def with_structured_output(self, _schema, method=None):
            assert method == "function_calling"
            return FakeStructuredModel()

    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: FakeModel())
    result = asyncio.run(
        supervisor.answer_project_question(
            project.project_id,
            "五种范式是什么意思？原文是怎么描述的？",
            scope="selection",
            selected_text="研究者已经总结出五大范式。",
        )
    )

    assert result["mode"] == "agent"
    assert "五类技术路径" in result["answer"]
    assert result["answer"].endswith("依据：[1]")
    assert result["citations"][0]["source_id"] == "REVIEW_SELECTION"
    assert "禁止仅复述" in captured["system"]
    assert "直接根据领域通用知识给出清晰解释" in captured["system"]
    assert "用户问题：五种范式是什么意思？原文是怎么描述的？" in captured["human"]

    class GeneralKnowledgeStructuredModel:
        async def ainvoke(self, _messages):
            return {
                "answer": "从领域通用意义上说，提示工程通过设计输入来引导模型行为。",
                "cited_source_ids": [],
                "used_library_ids": [],
                "coverage_note": "",
            }

    class GeneralKnowledgeModel:
        def with_structured_output(self, _schema, method=None):
            assert method == "function_calling"
            return GeneralKnowledgeStructuredModel()

    monkeypatch.setattr(supervisor, "_build_model", lambda: GeneralKnowledgeModel())
    general_result = asyncio.run(
        supervisor.answer_project_question(
            project.project_id,
            "提示工程是什么意思？",
            scope="selection",
            selected_text="Prompting/In-Context Learning（提示工程/上下文学习）",
        )
    )

    assert general_result["mode"] == "agent"
    assert general_result["citations"] == []
    assert "领域通用意义" in general_result["answer"]


def test_project_chat_streams_plain_llm_answer_and_includes_followup_history(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=tmp_path / "filesystem",
            enable_fallback=True,
        )
    )
    supervisor = app.state.supervisor
    project = supervisor.service.create_project("多轮聊天", "什么是检索增强？")
    supervisor.repository.save_artifact(
        project.project_id,
        "NarrativeReview",
        {
            "title": "检索增强综述",
            "abstract": "综述讨论检索增强生成。",
            "sections": [],
            "references": [],
        },
    )
    captured: dict[str, object] = {}

    class Chunk:
        def __init__(self, content: str):
            self.content = content

    class StreamingModel:
        async def astream(self, messages):
            captured["messages"] = messages
            for text in ("它会先检索", "，再结合结果生成回答。"):
                yield Chunk(text)

    monkeypatch.setattr(supervisor, "_build_model", lambda: StreamingModel())

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/projects/{project.project_id}/assistant/stream",
                json={
                    "scope": "narrative",
                    "question": "那它具体怎么工作？",
                    "history": [
                        {"role": "user", "content": "什么是检索增强？"},
                        {"role": "assistant", "content": "它把检索与生成结合起来。"},
                    ],
                },
            )
            assert response.status_code == 200
            assert "event: delta" in response.text
            assert "它会先检索" in response.text
            assert "再结合结果生成回答" in response.text
            assert "event: done" in response.text

    asyncio.run(exercise())
    messages = captured["messages"]
    assert [type(message).__name__ for message in messages] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]
    assert "不构成回答范围限制" in messages[0].content
    assert messages[-1].content == "那它具体怎么工作？"


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
                await client.get("/ui-assets/favicon.svg"),
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
        favicon,
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
    assert "论文研读工作台" in index.text
    assert favicon.status_code == 200
    assert "#245b48" in favicon.text
    assert "continueButtonLabel" in index.text
    assert 'id="runVisualizer"' in index.text
    assert 'id="runPhaseTitle"' in index.text
    assert 'id="usageGuide"' in index.text
    assert 'id="usageGuideOpen"' in index.text
    assert "欢迎使用论文研读工作台" in index.text
    assert "今天想研究什么？" in index.text
    assert "继续上次研究" not in index.text
    assert "候选集已经确认" not in index.text
    assert 'id="undoDecision"' not in index.text
    assert 'id="projectSearch"' in index.text
    assert 'id="toggleProjectSelection"' in index.text
    assert 'id="projectBulkBar"' in index.text
    assert 'id="deleteSelectedProjects"' in index.text
    assert 'id="emptyResearchLibrary"' in index.text
    assert "管理研究库" in index.text
    assert 'class="start-task-card is-primary"' not in index.text
    assert "首轮计入；每轮可生成多条检索词" not in index.text
    assert 'id="brandHome"' in index.text
    assert 'id="homeToggle"' in index.text
    assert "vendor/marked.umd.js" in index.text
    assert styles.status_code == 200
    assert "--accent" in styles.text
    assert "@keyframes run-orbit" in styles.text
    assert ".stage-stepper.is-running" in styles.text
    assert ".usage-guide-backdrop" in styles.text
    assert script.status_code == 200
    assert "submitFeedback" in script.text
    assert "deriveStepperState" in script.text
    assert "startRunPolling" in script.text
    assert "syncRunningSnapshot" in script.text
    assert "继续生成综述" in script.text
    assert "证据审查要求修订" in script.text
    assert "continueRecentReading" in script.text
    assert 'return reviseCount >= 2 ? null : "pipeline"' in script.text
    assert 'id="supplementalQueries"' in index.text
    assert "成果待补全" in script.text
    assert "写作待恢复" in script.text
    assert "本次停止来自主编输出格式故障" in script.text
    assert "renderMarkdown" in script.text
    assert "maybeOpenUsageGuide" in script.text
    assert "deleteSelectedProjectRecords" in script.text
    assert "research-agent.usage-guide-dismissed.v1" in script.text
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
    monkeypatch.setattr(
        supervisor_module,
        "extract_pdf_pages",
        lambda _path, _limit: [
            {"page": 1, "text": "Traceability improved through linked evidence."}
        ],
    )
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
            attachment_id = attachment.json()["data"]["attachment_id"]
            progress = await client.put(
                f"/api/library/papers/{first_id}/reading-progress",
                json={"page": 4, "attachment_id": attachment_id},
            )
            pinned = await client.patch(
                f"/api/library/collections/{collection_id}/papers/{first_id}",
                json={"pinned": True},
            )
            recent = await client.get("/api/library", params={"view": "recent"})
            folder = await client.get(
                "/api/library", params={"collection_id": collection_id}
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
                progress,
                pinned,
                recent,
                folder,
                first_id,
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
        progress,
        pinned,
        recent,
        folder,
        first_id,
    ) = asyncio.run(exercise_api())

    assert bulk.status_code == 200
    assert note.status_code == 200
    assert attachment.status_code == 200
    assert uploaded.status_code == 200
    assert uploaded.json()["data"]["full_text_status"] == "indexed"
    assert downloaded.content == b"%PDF-1.4 test content"
    assert detail.json()["data"]["notes"][0]["content"] == "Reusable note"
    assert len(detail.json()["data"]["attachments"]) == 2
    assert detail.json()["data"]["indexed_chunk_count"] == 1
    assert detail.json()["data"]["analyses"][0]["kind"] == "PaperCard"
    assert len(comparison.json()["data"]["rows"]) == 2
    assert "Traceability improved" in answer.json()["data"]["answer"]
    assert overview.json()["data"]["collections"][0]["paper_count"] == 2
    assert progress.status_code == 200
    assert progress.json()["data"]["page"] == 4
    assert pinned.json()["data"]["pinned"] is True
    assert recent.json()["data"][0]["library_id"] == first_id
    assert folder.json()["data"][0]["collection_membership"]["pinned"] is True


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
                {"paper_id": "P1", "title": "Paper 1", "source": "OpenAlex"},
                {"paper_id": "P2", "title": "Paper 2", "source": "OpenAlex"},
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
                f"/api/projects/{project.project_id}/search-review?page=1&page_size=1"
            )
            deselected = await client.patch(
                f"/api/projects/{project.project_id}/search-review/selection",
                json={"paper_ids": ["P1"], "selected": False},
            )
            reselected = await client.patch(
                f"/api/projects/{project.project_id}/search-review/selection",
                json={"paper_ids": ["P1"], "selected": True},
            )
            cleared_all = await client.patch(
                f"/api/projects/{project.project_id}/search-review/selection",
                json={"selected": False, "all_candidates": True},
            )
            selected_all = await client.patch(
                f"/api/projects/{project.project_id}/search-review/selection",
                json={"selected": True, "all_candidates": True},
            )
            await client.patch(
                f"/api/projects/{project.project_id}/search-review/selection",
                json={"paper_ids": ["P2"], "selected": False},
            )
            snapshot = await client.get(f"/api/projects/{project.project_id}")
            accepted = await client.post(
                f"/api/projects/{project.project_id}/search-feedback",
                json={"action": "accept", "comment": "Keep P1."},
            )
            supervisor.graph = fake_graph
            continued = await client.post(
                f"/api/projects/{project.project_id}/continue",
                json={},
            )
            return (
                review,
                deselected,
                reselected,
                cleared_all,
                selected_all,
                snapshot,
                accepted,
                continued,
            )

    (
        review,
        deselected,
        reselected,
        cleared_all,
        selected_all,
        snapshot,
        accepted,
        continued,
    ) = asyncio.run(exercise_api())

    assert review.status_code == 200
    assert review.json()["data"]["awaiting_input"] is True
    assert review.json()["data"]["candidate_page"]["page_size"] == 1
    assert deselected.json()["data"]["selected_count"] == 1
    assert reselected.json()["data"]["selected_count"] == 2
    assert cleared_all.json()["data"]["selected_count"] == 0
    assert selected_all.json()["data"]["selected_count"] == 2
    search_artifact = next(
        artifact
        for artifact in snapshot.json()["data"]["artifacts"]
        if artifact["kind"] == "SearchReport"
    )
    assert search_artifact["payload"]["candidate_count"] == 2
    assert search_artifact["payload"]["candidates"] == []
    assert accepted.status_code == 200
    assert accepted.json()["data"]["ready_to_continue"] is True
    assert continued.status_code == 200
    assert continued.json()["data"]["project_status"]["stage"] == "SCREENED"
    prompt = fake_graph.inputs["messages"][0]["content"]
    assert "screened_context" in prompt
    assert '"included_paper_ids": [\n    "P1"\n  ]' in prompt
    assert "不要为了寻找 ScreeningDecision 去读取或 grep 完整大快照" in prompt


def test_accepting_conversation_candidates_starts_research_immediately(
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
    conversation, project = supervisor.service.create_conversation("topic", "question")
    supervisor.service.save_artifact_and_transition(
        project.project_id,
        "SearchReport",
        {
            "query": "question",
            "search_terms": ["query"],
            "candidates": [
                {"paper_id": "P1", "title": "Paper 1", "source": "OpenAlex"},
                {"paper_id": "P2", "title": "Paper 2", "source": "OpenAlex"},
            ],
        },
        target=ResearchStage.SEARCHED,
        actor="literature-scout",
    )
    supervisor.search_review.begin_review(project.project_id)
    started = {}

    class FakeRun:
        def model_dump(self, mode="json"):
            del mode
            return {
                "run_id": "RUN-followup",
                "conversation_id": conversation.conversation_id,
                "project_id": project.project_id,
                "thread_id": conversation.thread_id,
                "kind": "continue",
                "status": "queued",
                "phase": "reading",
                "message": "queued",
            }

    async def fake_start_continue(conversation_id, user_id):
        started.update(conversation_id=conversation_id, user_id=user_id)
        return FakeRun()

    app.state.run_manager.start_continue = fake_start_continue

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/projects/{project.project_id}/search-feedback",
                json={"action": "accept"},
            )

    response = asyncio.run(exercise_api())

    assert response.status_code == 200
    assert response.json()["data"]["research_started"] is True
    assert response.json()["data"]["run"]["run_id"] == "RUN-followup"
    assert started["conversation_id"] == conversation.conversation_id
