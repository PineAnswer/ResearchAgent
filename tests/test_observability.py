import json
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from research_agent.application.research_service import ResearchService
from research_agent.infrastructure.artifact_exporter import JsonArtifactExporter
from research_agent.infrastructure.observable_chat_model import ObservableChatOpenAI
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


def test_run_logger_streams_structured_search_rounds_and_summary(tmp_path) -> None:
    streamed = []
    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
        event_sink=streamed.append,
    )

    search_run_id = uuid4()
    logger.on_tool_start(
        {"name": "search_multi_source"},
        "",
        run_id=search_run_id,
        inputs={
            "queries": ["large model AIOps", "small model anomaly detection"],
            "limit_per_source": 5,
        },
    )
    logger.on_tool_end(
        json.dumps(
            {
                "queries": ["large model AIOps", "small model anomaly detection"],
                "candidates": [{"paper_id": "P1", "title": "Paper One"}],
                "source_status": [
                    {"source": "OpenAlex", "query": "large model AIOps", "ok": True, "count": 1},
                    {"source": "arXiv", "query": "large model AIOps", "ok": False},
                ],
            }
        ),
        run_id=search_run_id,
    )

    commit_run_id = uuid4()
    logger.on_tool_start(
        {"name": "commit_subagent_result"},
        "",
        run_id=commit_run_id,
        inputs={"subagent_type": "literature-scout"},
    )
    logger.on_tool_end(
        json.dumps(
            {
                "artifact": {
                    "kind": "SearchReport",
                    "payload": {
                        "search_terms": ["large model AIOps", "small model anomaly detection"],
                        "candidates": [{"paper_id": "P1"}],
                        "search_iteration_log": [{"query": "large model AIOps"}],
                        "coverage_gaps": ["缺少在线推理研究"],
                    },
                },
                "project": {"stage": "SEARCH_REVIEW_PENDING"},
            }
        ),
        run_id=commit_run_id,
    )

    portfolio = [
        event
        for event in streamed
        if (event.get("data") or {}).get("scope") == "portfolio"
    ]
    assert [event["type"] for event in portfolio] == [
        "search.started",
        "search.results",
        "search.synthesizing",
        "search.summary",
    ]
    assert portfolio[0]["data"]["round"] == 1
    assert portfolio[0]["data"]["queries"] == [
        "large model AIOps",
        "small model anomaly detection",
    ]
    assert portfolio[1]["data"]["count"] == 1
    assert portfolio[-1]["data"]["coverage_gaps"] == ["缺少在线推理研究"]


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
    assert "LLM拟调用2个工具；系统将尝试串行执行第一个：verify_doi" in events


def test_run_logger_records_invalid_tool_call_diagnostics(tmp_path) -> None:
    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
    )
    message = AIMessage(
        content="",
        invalid_tool_calls=[
            {
                "name": "SynthesisReport",
                "args": '{"topic": "broken"',
                "id": "bad-call",
                "error": "invalid JSON",
            }
        ],
        response_metadata={"finish_reason": "tool_calls"},
    )

    logger.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=message)]])
    )

    events = logger.events_path.read_text(encoding="utf-8")
    transcript = logger.messages_path.read_text(encoding="utf-8")
    assert "llm.invalid_tool_calls" in events
    assert "invalid JSON" in events
    assert '"message_diagnostics"' in transcript
    assert '"raw_provider_response_captured": false' in transcript


def test_run_logger_marks_tool_finish_without_parsed_calls(tmp_path) -> None:
    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
    )
    message = AIMessage(
        content="",
        response_metadata={"finish_reason": "tool_calls"},
    )

    logger.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=message)]])
    )

    events = logger.events_path.read_text(encoding="utf-8")
    assert "llm.tool_call_parse_gap" in events
    assert "tool_call_finish_without_parsed_calls" in events


def test_observable_chat_model_preserves_raw_provider_tool_arguments(tmp_path) -> None:
    model = ObservableChatOpenAI(
        model="deepseek-chat",
        api_key="test-key",
        base_url="https://example.invalid",
    )
    response = {
        "id": "response-1",
        "api_key": "sk-provider-secret",
        "model": "deepseek-chat",
        "object": "chat.completion",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "bad-call",
                            "type": "function",
                            "function": {
                                "name": "SynthesisReport",
                                "arguments": '{"topic": "broken"',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }

    result = model._create_chat_result(response)

    raw = result.generations[0].generation_info["raw_provider_response"]
    arguments = raw["choices"][0]["message"]["tool_calls"][0]["function"][
        "arguments"
    ]
    assert arguments == '{"topic": "broken"'

    logger = ResearchRunLogger(
        tmp_path / "runs",
        topic="topic",
        research_question="question",
        thread_id="thread-1",
    )
    logger.on_llm_end(LLMResult(generations=[[result.generations[0]]]))
    transcript = logger.messages_path.read_text(encoding="utf-8")
    assert '"raw_provider_response_captured": true' in transcript
    record = json.loads(transcript)
    logged_raw = record["data"]["message_diagnostics"]["raw_provider_response"]
    logged_arguments = logged_raw["choices"][0]["message"]["tool_calls"][0][
        "function"
    ]["arguments"]
    assert logged_arguments == '{"topic": "broken"'
    assert logged_raw["api_key"] == "[REDACTED]"


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
