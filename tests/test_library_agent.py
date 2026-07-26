import asyncio
import json

from langchain_core.messages import AIMessage, ToolMessage

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


def test_ask_library_agent_empty_library_skips_model_and_explains(
    tmp_path,
    monkeypatch,
) -> None:
    """Questions against an empty library are a data situation, not an agent
    failure: the model must not be invoked and the note must say the library
    is empty."""
    supervisor = _supervisor(tmp_path)
    supervisor.graph = object()

    def fail_build_model():
        raise AssertionError("model must not be built for an empty library")

    monkeypatch.setattr(supervisor, "_build_model", fail_build_model)

    result = asyncio.run(supervisor.answer_library_question([], "有几个文献"))

    assert result["mode"] == "extractive"
    assert "文献库当前没有论文" in result["coverage_note"]


def test_ask_library_agent_salvages_cited_plain_text_answer(
    tmp_path,
    monkeypatch,
) -> None:
    """Relays that ignore structured output still return the cited answer as
    plain text; a traceable [[source_id]] answer must be recovered instead of
    degrading to extractive mode."""
    supervisor = _supervisor(tmp_path)
    paper = supervisor.service.library.upsert_paper(
        {
            "title": "Traceable evidence",
            "abstract": "Every answer is grounded in traceable evidence.",
        }
    )
    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: object())

    def fake_create_agent(**kwargs):
        tools = {tool.name: tool for tool in kwargs["tools"]}

        class FakeAgent:
            async def ainvoke(self, _inputs):
                passages = json.loads(
                    tools["retrieve_library_passages"].invoke(
                        {"query": "traceable evidence"}
                    )
                )
                source_id = passages[0]["source_id"]
                answer = f"该文献强调回答应可追溯。 [[{source_id}]]"
                return {
                    "messages": [AIMessage(content=answer)],
                }

        return FakeAgent()

    monkeypatch.setattr(supervisor_module, "create_agent", fake_create_agent)

    result = asyncio.run(
        supervisor.answer_library_question([], "文献库对可追溯回答有什么证据？")
    )

    assert result["mode"] == "agent"
    assert result["answer"].endswith("[1]")
    assert result["citations"][0]["library_id"] == paper.library_id


def test_ask_library_agent_accepts_uncited_overview_answer_after_tool_use(
    tmp_path,
    monkeypatch,
) -> None:
    """Overview questions (e.g. how many papers) have no passage to quote; the
    answer must survive when the agent actually consulted the library tools."""
    supervisor = _supervisor(tmp_path)
    supervisor.service.library.upsert_paper(
        {"title": "Counted paper", "abstract": "An abstract."}
    )
    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: object())

    def fake_create_agent(**kwargs):
        class FakeAgent:
            async def ainvoke(self, _inputs):
                return {
                    "messages": [ToolMessage(content="[]", tool_call_id="t1")],
                    "structured_response": {
                        "answer": "文献库目前共有 1 篇论文。",
                        "cited_source_ids": [],
                        "used_library_ids": [],
                        "coverage_note": "已检索整个文献库。",
                    },
                }

        return FakeAgent()

    monkeypatch.setattr(supervisor_module, "create_agent", fake_create_agent)

    result = asyncio.run(supervisor.answer_library_question([], "有几个文献"))

    assert result["mode"] == "agent"
    assert result["answer"] == "文献库目前共有 1 篇论文。"
    assert result["citations"] == []
    assert "概览性质" in result["coverage_note"]


def test_ask_library_agent_rejects_uncited_answer_without_tool_use(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor.service.library.upsert_paper(
        {"title": "Counted paper", "abstract": "An abstract."}
    )
    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: object())

    def fake_create_agent(**kwargs):
        class FakeAgent:
            async def ainvoke(self, _inputs):
                return {
                    "messages": [],
                    "structured_response": {
                        "answer": "凭空断言的内容。",
                        "cited_source_ids": [],
                        "used_library_ids": [],
                        "coverage_note": "",
                    },
                }

        return FakeAgent()

    monkeypatch.setattr(supervisor_module, "create_agent", fake_create_agent)

    result = asyncio.run(supervisor.answer_library_question([], "有几个文献"))

    assert result["mode"] == "extractive"
    assert "缺少可追溯的文献引用" in result["coverage_note"]


def test_ask_library_agent_missing_structured_response_falls_back_cleanly(
    tmp_path,
    monkeypatch,
) -> None:
    """A run that ends without structured_response (e.g. model-call limit hit)
    must degrade to extractive results with a readable note, not surface a raw
    pydantic validation error to the user."""
    supervisor = _supervisor(tmp_path)
    supervisor.service.library.upsert_paper(
        {"title": "Fallback paper", "abstract": "Grounded fallback abstract."}
    )
    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: object())

    def fake_create_agent(**kwargs):
        class FakeAgent:
            async def ainvoke(self, _inputs):
                return {"messages": []}

        return FakeAgent()

    monkeypatch.setattr(supervisor_module, "create_agent", fake_create_agent)

    result = asyncio.run(supervisor.answer_library_question([], "文献库有内容吗"))

    assert result["mode"] == "extractive"
    assert "validation error" not in result["coverage_note"].lower()
    assert "已返回本地检索结果" in result["coverage_note"]


def test_ask_library_agent_invalid_structured_response_note_is_sanitized(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor.service.library.upsert_paper(
        {"title": "Fallback paper", "abstract": "Grounded fallback abstract."}
    )
    supervisor.graph = object()
    monkeypatch.setattr(supervisor, "_build_model", lambda: object())

    def fake_create_agent(**kwargs):
        class FakeAgent:
            async def ainvoke(self, _inputs):
                return {"structured_response": {"answer": 123}}

        return FakeAgent()

    monkeypatch.setattr(supervisor_module, "create_agent", fake_create_agent)

    result = asyncio.run(supervisor.answer_library_question([], "文献库有内容吗"))

    assert result["mode"] == "extractive"
    assert "validation error" not in result["coverage_note"].lower()
    assert "errors.pydantic.dev" not in result["coverage_note"]
    assert "模型未返回符合要求的结构化回答" in result["coverage_note"]
