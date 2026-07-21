import asyncio

import httpx

import research_agent.agents.supervisor as supervisor_module
from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.api.app import create_app
from research_agent.application.library_service import LibraryService
from research_agent.domain.models import LibraryAttachment
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def _test_supervisor(tmp_path) -> ResearchSupervisor:
    supervisor = ResearchSupervisor(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=tmp_path / "filesystem",
            enable_fallback=True,
        )
    )
    supervisor.graph = None
    return supervisor


def test_library_can_acquire_open_pdf_once_and_reuse_indexed_attachment(
    tmp_path, monkeypatch
) -> None:
    supervisor = _test_supervisor(tmp_path)
    paper = supervisor.service.library.upsert_paper(
        {
            "paper_id": "W123456",
            "title": "Open full text study",
            "doi": "10.1000/open-study",
        }
    )
    cached_pdf = tmp_path / "filesystem" / "papers" / "open-study.pdf"
    cached_pdf.parent.mkdir(parents=True, exist_ok=True)
    cached_pdf.write_bytes(b"%PDF-1.4 online test")

    class FakeFetchTool:
        calls = 0

        def invoke(self, payload):
            self.calls += 1
            assert payload["paper_id"] == "W123456"
            return """{
                "available": true,
                "source_url": "https://example.test/open-study.pdf",
                "local_pdf_path": "/papers/open-study.pdf",
                "cached": false
            }"""

    fetch_tool = FakeFetchTool()
    supervisor.literature_tools_by_name["fetch_paper_text"] = fetch_tool
    monkeypatch.setattr(
        supervisor_module,
        "extract_pdf_pages",
        lambda _path, _limit: [
            {"page": 1, "text": "Online full text with a real page number."}
        ],
    )

    async def exercise():
        acquired = await supervisor.acquire_library_full_text(paper.library_id)
        reused = await supervisor.acquire_library_full_text(paper.library_id)
        return acquired, reused

    acquired, reused = asyncio.run(exercise())

    assert acquired["status"] == "acquired"
    assert acquired["attachment"]["full_text_status"] == "indexed"
    assert acquired["attachment"]["name"].startswith("Online - ")
    assert reused["status"] == "existing"
    assert fetch_tool.calls == 1
    assert supervisor._library_attachment_path(
        acquired["attachment"]["attachment_id"]
    ).read_bytes().startswith(b"%PDF")


def test_library_online_acquisition_failure_keeps_paper_usable(tmp_path) -> None:
    supervisor = _test_supervisor(tmp_path)
    paper = supervisor.service.library.upsert_paper(
        {"title": "Paywalled study", "doi": "10.1000/paywalled"}
    )

    class UnavailableFetchTool:
        def invoke(self, _payload):
            return """{
                "available": false,
                "error_code": "open_full_text_unavailable",
                "attempted_urls": []
            }"""

    supervisor.literature_tools_by_name["fetch_paper_text"] = UnavailableFetchTool()

    result = asyncio.run(supervisor.acquire_library_full_text(paper.library_id))

    assert result["status"] == "unavailable"
    assert supervisor.repository.get_library_paper(paper.library_id).title == "Paywalled study"
    assert supervisor.repository.list_library_attachments(paper.library_id) == []


def test_reading_card_can_fall_back_to_abstract_without_pdf(tmp_path) -> None:
    supervisor = _test_supervisor(tmp_path)
    paper = supervisor.service.library.upsert_paper(
        {
            "title": "Abstract-only study",
            "abstract": "A controller improves closed-loop planning in unseen cities.",
        }
    )

    result = asyncio.run(
        supervisor.generate_library_reading_card(paper.library_id)
    )

    assert result["evidence_level"] == "abstract"
    assert result["attachment"] is None
    assert result["analysis"]["summary"].startswith("A controller improves")
    assert result["artifact"]["attachment_id"] is None


def test_abstract_reading_card_uses_tool_calling_and_grounds_quotes(
    tmp_path, monkeypatch
) -> None:
    supervisor = _test_supervisor(tmp_path)
    supervisor.graph = object()
    paper = supervisor.service.library.upsert_paper(
        {
            "title": "Structured abstract study",
            "abstract": "BehaviorNet predicts parameters of an agent motion controller.",
        }
    )

    class FakeStructuredModel:
        async def ainvoke(self, _messages):
            return {
                "summary": "BehaviorNet models reactive behavior.",
                "methods": ["BehaviorNet"],
                "findings": [
                    {
                        "claim": "The controller parameters are predicted.",
                        "quote": "BehaviorNet predicts parameters of an agent motion controller.",
                        "page": 99,
                    }
                ],
            }

    class FakeModel:
        def with_structured_output(self, _schema, *, method):
            assert method == "function_calling"
            return FakeStructuredModel()

    monkeypatch.setattr(supervisor, "_build_model", lambda: FakeModel())

    result = asyncio.run(
        supervisor.generate_library_reading_card(paper.library_id)
    )

    assert result["mode"] == "agent"
    assert result["analysis"]["evidence_level"] == "abstract"
    assert result["analysis"]["findings"][0]["page"] is None
    assert result["analysis"]["findings"][0]["source_scope"] == "abstract"


def test_paper_question_sends_complete_pdf_and_selection_to_llm(
    tmp_path, monkeypatch
) -> None:
    supervisor = _test_supervisor(tmp_path)
    supervisor.graph = object()
    paper = supervisor.service.library.upsert_paper(
        {"title": "Complete context study"}
    )
    attachment = LibraryAttachment(
        attachment_id="LA-complete",
        library_id=paper.library_id,
        name="complete.pdf",
        url="/api/library/attachments/LA-complete/content",
        media_type="application/pdf",
        full_text_status="indexed",
    )
    supervisor.repository.save_library_attachment(attachment)
    pdf_path = (
        tmp_path
        / "library-attachments"
        / paper.library_id
        / attachment.attachment_id
    )
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(
        supervisor_module,
        "extract_pdf_pages",
        lambda _path, _limit: [
            {"page": 1, "text": "The first page defines the research problem."},
            {"page": 2, "text": "The second page reports a validated improvement."},
        ],
    )
    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, messages):
            captured["prompt"] = messages[-1].content
            return {
                "answer": "The selected result is supported by the evaluation.",
                "citations": [
                    {
                        "page": 2,
                        "quote": "The second page reports a validated improvement.",
                    },
                    {"page": 1, "quote": "This quote was invented."},
                ],
                "coverage_note": "Read both pages.",
            }

    class FakeModel:
        def with_structured_output(self, _schema, *, method):
            assert method == "function_calling"
            return FakeStructuredModel()

    monkeypatch.setattr(supervisor, "_build_model", lambda: FakeModel())

    result = asyncio.run(
        supervisor.answer_paper_question(
            paper.library_id,
            "What does the selected result mean?",
            scope="selection",
            attachment_id=attachment.attachment_id,
            page=2,
            selected_text="validated improvement",
        )
    )

    assert result["mode"] == "agent"
    assert result["context_scope"] == "full_text"
    assert result["pages_sent"] == 2
    assert "--- PAGE 1 ---" in captured["prompt"]
    assert "--- PAGE 2 ---" in captured["prompt"]
    assert "validated improvement" in captured["prompt"]
    assert len(result["citations"]) == 1
    assert result["citations"][0]["page"] == 2


def test_paper_annotations_are_persisted_and_exported(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    paper = service.upsert_paper(
        {"title": "Page-aware research", "authors": ["Ada"], "year": 2026}
    )
    annotation = service.save_annotation(
        paper.library_id,
        {
            "kind": "qa",
            "page": 3,
            "selected_text": "A grounded sentence.",
            "question": "Why does this matter?",
            "answer": "It improves traceability [1].",
            "citations": [
                {
                    "citation": "[1]",
                    "title": paper.title,
                    "page": 3,
                    "quote": "A grounded sentence.",
                }
            ],
        },
    )

    workspace = service.paper_workspace(paper.library_id)
    report = service.export_reading_report(paper.library_id)

    assert workspace["annotations"][0]["annotation_id"] == annotation.annotation_id
    assert "## 高亮与批注" in report
    assert "Why does this matter?" in report
    assert "第 3 页" in report


def test_paper_workspace_api_supports_selection_question_and_markdown_export(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        supervisor_module,
        "extract_pdf_pages",
        lambda _path, _limit: [
            {"page": 1, "text": "Linked evidence improves scientific traceability."},
            {"page": 2, "text": "The evaluation reports an important limitation."},
        ],
    )
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=tmp_path / "filesystem",
            enable_fallback=True,
        )
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pdfjs_module = await client.get("/ui-assets/vendor/pdfjs/pdf.mjs")
            assert pdfjs_module.status_code == 200
            assert "text/javascript" in pdfjs_module.headers["content-type"]
            frontend = await client.get("/ui-assets/app.js")
            assert "paperPdfLoadingTask.destroy()" in frontend.text
            assert "state.paperPdf.destroy()" not in frontend.text
            created = await client.post(
                "/api/library/papers",
                json={"title": "Traceable paper", "abstract": "Evidence study"},
            )
            library_id = created.json()["data"]["library_id"]
            uploaded = await client.post(
                f"/api/library/papers/{library_id}/attachments/upload",
                params={"filename": "paper.pdf", "media_type": "application/pdf"},
                content=b"%PDF-1.4 test content",
                headers={"Content-Type": "application/pdf"},
            )
            assert uploaded.status_code == 200
            attachment_id = uploaded.json()["data"]["attachment_id"]

            workspace = await client.get(f"/api/library/papers/{library_id}/workspace")
            assert workspace.status_code == 200
            assert workspace.json()["data"]["workspace_attachment"]["attachment_id"] == attachment_id

            answer = await client.post(
                f"/api/library/papers/{library_id}/workspace/question",
                json={
                    "scope": "selection",
                    "attachment_id": attachment_id,
                    "page": 1,
                    "selected_text": "Linked evidence improves scientific traceability.",
                    "question": "What is improved?",
                },
            )
            assert answer.status_code == 200
            answer_data = answer.json()["data"]
            assert answer_data["scope"] == "selection"
            assert answer_data["citations"][0]["page"] == 1

            whole_answer = await client.post(
                f"/api/library/papers/{library_id}/workspace/question",
                json={
                    "scope": "paper",
                    "attachment_id": attachment_id,
                    "question": "What should I know?",
                },
            )
            assert whole_answer.status_code == 200
            assert all(
                citation["page"] is not None
                for citation in whole_answer.json()["data"]["citations"]
            )

            annotation = await client.post(
                f"/api/library/papers/{library_id}/annotations",
                json={
                    "kind": "qa",
                    "attachment_id": attachment_id,
                    "page": 1,
                    "selected_text": "Linked evidence improves scientific traceability.",
                    "question": "What is improved?",
                    "answer": answer_data["answer"],
                    "citations": answer_data["citations"],
                },
            )
            assert annotation.status_code == 200

            report = await client.get(
                f"/api/library/papers/{library_id}/workspace/report.md"
            )
            assert report.status_code == 200
            assert "text/markdown" in report.headers["content-type"]
            assert "What is improved?" in report.text
            assert "第 1 页" in report.text

    asyncio.run(exercise())
