import asyncio
import json

import research_agent.agents.supervisor as supervisor_module
from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.domain.models import (
    LibraryAttachment,
    LibraryFinding,
    LibraryPaperAnalysis,
)
from research_agent.infrastructure.config import Settings


def _supervisor(tmp_path) -> ResearchSupervisor:
    return ResearchSupervisor(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=tmp_path / "filesystem",
            enable_fallback=True,
        )
    )


def test_uploaded_pdf_ingestion_indexes_pages_and_persists_paper_card(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor.graph = None
    paper = supervisor.service.library.upsert_paper(
        {"title": "Local PDF study", "abstract": "A routing study."}
    )
    attachment = LibraryAttachment(
        attachment_id="LA-test-ingest",
        library_id=paper.library_id,
        name="paper.pdf",
        url="/api/library/attachments/LA-test-ingest/content",
        full_text_status="uploaded",
    )
    supervisor.repository.save_library_attachment(attachment)
    path = tmp_path / "library-attachments" / paper.library_id / attachment.attachment_id
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        supervisor_module,
        "extract_pdf_pages",
        lambda _path, _limit: [
            {"page": 1, "text": "We use a sparse routing method."},
            {"page": 2, "text": "The method improves routing stability."},
        ],
    )

    result = asyncio.run(supervisor.ingest_library_attachment(attachment.attachment_id))
    detail = supervisor.service.library.get_paper(paper.library_id)

    assert result["attachment"]["full_text_status"] == "indexed"
    assert result["attachment"]["page_count"] == 2
    assert result["attachment"]["chunk_count"] == 2
    assert detail["indexed_chunk_count"] == 2
    assert detail["analyses"][0]["kind"] == "PaperCard"


def test_ai_library_findings_keep_only_exact_page_quotes() -> None:
    analysis = LibraryPaperAnalysis(
        findings=[
            LibraryFinding(
                claim="Supported.",
                quote="The method improves routing stability.",
                page=2,
            ),
            LibraryFinding(claim="Invented.", quote="A fabricated quote.", page=2),
            LibraryFinding(claim="No page.", quote="The method", page=None),
        ]
    )

    grounded = ResearchSupervisor._ground_library_analysis(
        analysis,
        [{"page": 2, "text": "The method improves routing stability."}],
    )

    assert [item.claim for item in grounded.findings] == ["Supported."]


def test_ask_library_agent_searches_full_library_and_validates_citations(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor = _supervisor(tmp_path)
    paper = supervisor.service.library.upsert_paper(
        {
            "title": "Traceable evidence",
            "abstract": "Every answer is grounded in traceable evidence.",
        }
    )
    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: object())
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        tools = {tool.name: tool for tool in kwargs["tools"]}

        class FakeAgent:
            async def ainvoke(self, _inputs):
                tools["search_library"].invoke({"query": "traceable evidence"})
                passages = json.loads(
                    tools["retrieve_library_passages"].invoke(
                        {"query": "traceable evidence"}
                    )
                )
                source_id = passages[0]["source_id"]
                return {
                    "structured_response": {
                        "answer": f"该文献强调回答应可追溯。 [[{source_id}]]",
                        "cited_source_ids": [source_id],
                        "used_library_ids": [paper.library_id],
                        "coverage_note": "已检索整个文献库。",
                    }
                }

        return FakeAgent()

    monkeypatch.setattr(supervisor_module, "create_agent", fake_create_agent)

    result = asyncio.run(
        supervisor.answer_library_question([], "文献库对可追溯回答有什么证据？")
    )

    assert result["mode"] == "agent"
    assert result["citations"][0]["library_id"] == paper.library_id
    assert result["citations"][0]["quote"].startswith("Every answer")
    assert result["answer"].endswith("[1]")
    assert {tool.name for tool in captured["tools"]} == {
        "search_library",
        "retrieve_library_passages",
        "get_library_paper_context",
    }
    assert len(captured["middleware"]) == 5
