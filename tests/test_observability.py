import json
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from research_agent.application.research_service import ResearchService
from research_agent.infrastructure.artifact_exporter import JsonArtifactExporter
from research_agent.infrastructure.run_logger import ResearchRunLogger
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def test_service_exports_artifacts_and_snapshot(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    exporter = JsonArtifactExporter(tmp_path / "outputs")
    service = ResearchService(repository, exporter)

    project = service.create_project("topic", "question")
    artifact = service.save_artifact(
        project.project_id,
        "SearchReport",
        {
            "query": "few-shot remote sensing",
            "search_terms": ["few-shot", "remote sensing"],
            "candidates": [],
            "selection_notes": [],
        },
    )

    project_root = tmp_path / "outputs" / project.project_id
    artifact_path = project_root / "artifacts" / f"{artifact.artifact_id:06d}-SearchReport.json"
    snapshot = json.loads((project_root / "snapshot.json").read_text(encoding="utf-8"))

    assert artifact_path.exists()
    assert (project_root / "project.json").exists()
    assert (project_root / "state-events.json").exists()
    assert snapshot["artifacts"][0]["kind"] == "SearchReport"


def test_run_logger_records_llm_tools_search_results_and_paper_progress(tmp_path) -> None:
    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
        console=False,
    )

    llm_run_id = uuid4()
    logger.on_chat_model_start(
        {"name": "test-model"},
        [[HumanMessage(content="research this topic")]],
        run_id=llm_run_id,
    )
    logger.on_llm_end(
        LLMResult(
            generations=[
                [ChatGeneration(message=AIMessage(content="I will search the literature."))]
            ]
        ),
        run_id=llm_run_id,
    )

    search_run_id = uuid4()
    logger.on_tool_start(
        {"name": "search_openalex"},
        "",
        run_id=search_run_id,
        inputs={"query": "few-shot remote sensing", "limit": 2},
    )
    logger.on_tool_end(
        json.dumps(
            [
                {"paper_id": "P1", "title": "Paper One"},
                {"paper_id": "P2", "title": "Paper Two"},
            ]
        ),
        run_id=search_run_id,
    )

    screening_run_id = uuid4()
    logger.on_tool_start(
        {"name": "save_project_artifact"},
        "",
        run_id=screening_run_id,
        inputs={
            "kind": "ScreeningDecision",
            "payload_json": json.dumps({"included_paper_ids": ["P1", "P2"]}),
        },
    )
    logger.on_tool_end("saved", run_id=screening_run_id)

    paper_run_id = uuid4()
    logger.on_tool_start(
        {"name": "save_project_artifact"},
        "",
        run_id=paper_run_id,
        inputs={
            "kind": "PaperCard",
            "payload_json": json.dumps({"paper_id": "P1", "title": "Paper One"}),
        },
    )
    logger.on_tool_end("saved", run_id=paper_run_id)
    logger.finish("completed", result={"messages": []})

    events = logger.events_path.read_text(encoding="utf-8")
    transcript = logger.messages_path.read_text(encoding="utf-8")

    assert "OpenAlex返回2篇：Paper One；Paper Two" in events
    assert "筛选结果已保存，入选2篇论文" in events
    assert "第1/2篇处理完成：《Paper One》" in events
    assert "I will search the literature." in transcript
    assert logger.result_path.exists()


def test_run_logger_labels_model_batches_as_serial_execution(tmp_path) -> None:
    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
    )
    logger.on_llm_end(
        LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "verify_doi", "args": {"doi": "1"}, "id": "a"},
                                {"name": "verify_doi", "args": {"doi": "2"}, "id": "b"},
                            ],
                        )
                    )
                ]
            ]
        )
    )

    events = logger.events_path.read_text(encoding="utf-8")
    assert "LLM提出2个工具调用；系统串行执行第一个：verify_doi" in events


def test_run_logger_marks_nonterminal_return_incomplete_and_updates_run_file(
    tmp_path,
) -> None:
    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
    )
    result = {
        "messages": [AIMessage(content="draft report")],
        "project_status": {
            "project_id": "RP-test",
            "stage": "EXTRACTED",
            "current_review": None,
        },
    }

    summary = logger.finish("completed", result=result)
    run = json.loads(logger.run_path.read_text(encoding="utf-8"))

    assert summary["status"] == "incomplete"
    assert result["run_status"] == "incomplete"
    assert run["status"] == "incomplete"
    assert run["project_stage"] == "EXTRACTED"
    assert logger.report_path.exists()
    assert "性质：运行草稿" in logger.report_path.read_text(encoding="utf-8")
