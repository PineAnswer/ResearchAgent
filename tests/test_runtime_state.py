import json
from types import SimpleNamespace

from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from research_agent.agents.runtime_state import (
    ExecutedSearchTrackingMiddleware,
    PaperFetchGuardMiddleware,
    ResearchRuntimeState,
    recording_runnable,
)


class FakeStructuredAgent:
    def __init__(self, payload, messages=None):
        self.payload = payload
        self.messages = messages

    def invoke(self, inputs, config=None):
        return {
            "messages": self.messages or inputs["messages"],
            "structured_response": self.payload,
        }

    async def ainvoke(self, inputs, config=None):
        return self.invoke(inputs, config)


def test_scout_result_uses_only_executed_search_terms() -> None:
    state = ResearchRuntimeState()
    state.record_search("thread-a", "actually executed")
    agent = FakeStructuredAgent(
        {
            "query": "question",
            "search_terms": ["planned but blocked"],
            "candidates": [],
            "selection_notes": [],
        }
    )
    runnable = recording_runnable(agent, "literature-scout", state)

    runnable.invoke(
        {"messages": [HumanMessage(content="search")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    result = state.pending_result("thread-a", "literature-scout")
    assert result["search_terms"] == ["actually executed"]


def test_scout_result_fills_missing_query_from_executed_search_terms() -> None:
    state = ResearchRuntimeState()
    state.record_search("thread-a", "large language model anomaly detection")
    agent = FakeStructuredAgent(
        {
            "candidate_ids": [],
            "screening_decisions": {},
            "screening_reasons": {},
            "coverage_gaps": [],
            "search_iteration_log": [],
            "selection_notes": [],
        }
    )
    runnable = recording_runnable(agent, "literature-scout", state)

    runnable.invoke(
        {"messages": [HumanMessage(content="search")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    result = state.pending_result("thread-a", "literature-scout")
    assert result["query"] == "large language model anomaly detection"


def test_scout_candidate_ids_match_openalex_urls_and_bare_ids() -> None:
    state = ResearchRuntimeState()
    state.record_search("thread-a", "query")
    state.store_search_results(
        "thread-a",
        json.dumps(
            [
                {
                    "paper_id": "https://openalex.org/W4409797280",
                    "title": "Included",
                    "source": "OpenAlex",
                },
                {
                    "paper_id": "https://openalex.org/W4409797281",
                    "title": "Excluded",
                    "source": "OpenAlex",
                },
            ]
        ),
    )
    agent = FakeStructuredAgent(
        {
            "query": "query",
            "candidate_ids": ["W4409797280"],
            "screening_decisions": {"W4409797280": "include"},
            "screening_reasons": {},
            "coverage_gaps": [],
            "search_iteration_log": [],
            "selection_notes": [],
        }
    )
    runnable = recording_runnable(agent, "literature-scout", state)

    runnable.invoke(
        {"messages": [HumanMessage(content="search")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    result = state.pending_result("thread-a", "literature-scout")
    assert [item["paper_id"] for item in result["candidates"]] == [
        "https://openalex.org/W4409797280"
    ]


def test_scout_temporary_candidate_ids_are_mapped_to_real_search_results() -> None:
    state = ResearchRuntimeState()
    state.record_search("thread-a", "query")
    state.store_search_results(
        "thread-a",
        json.dumps(
            [
                {
                    "paper_id": "https://openalex.org/W4409797280",
                    "title": "First",
                    "doi": "https://doi.org/10.1109/first",
                    "source": "OpenAlex",
                },
                {
                    "paper_id": "10.1000/second",
                    "title": "Second",
                    "doi": "10.1000/second",
                    "source": "Crossref",
                },
            ]
        ),
    )
    agent = FakeStructuredAgent(
        {
            "query": "query",
            "candidate_ids": ["P001", "P002"],
            "screening_decisions": {"P001": "include", "P002": "exclude"},
            "screening_reasons": {"P001": "relevant", "P002": "off topic"},
            "coverage_gaps": [],
            "search_iteration_log": [],
            "selection_notes": [],
        }
    )
    runnable = recording_runnable(agent, "literature-scout", state)

    runnable.invoke(
        {"messages": [HumanMessage(content="search")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    result = state.pending_result("thread-a", "literature-scout")
    assert result["candidate_ids"] == ["W4409797280", "10.1000/second"]
    assert [item["title"] for item in result["candidates"]] == ["First", "Second"]
    assert result["screening_decisions"] == {
        "W4409797280": "include",
        "10.1000/second": "exclude",
    }
    assert result["screening_reasons"] == {
        "W4409797280": "relevant",
        "10.1000/second": "off topic",
    }


def test_reader_result_keeps_the_supervisor_supplied_paper_id() -> None:
    state = ResearchRuntimeState()
    agent = FakeStructuredAgent(
        {
            "paper_id": "invented-id",
            "title": "Paper",
            "research_question": "question",
            "methods": [],
            "datasets": [],
            "findings": [],
            "limitations": [],
        }
    )
    runnable = recording_runnable(agent, "paper-reader", state)

    runnable.invoke(
        {
            "messages": [
                HumanMessage(content='paper_id（必须使用此值）: "https://openalex.org/W1"')
            ]
        },
        config={"configurable": {"thread_id": "thread-a"}},
    )

    result = state.pending_result("thread-a", "paper-reader")
    assert result["paper_id"] == "https://openalex.org/W1"


def test_search_terms_deduplicate_same_token_intent() -> None:
    state = ResearchRuntimeState()

    assert state.record_search(
        "thread-a", "large small model collaboration AIOps limitations"
    )
    assert not state.record_search(
        "thread-a", "AIOps large model small model collaboration limitation"
    )
    assert state.search_terms("thread-a") == [
        "large small model collaboration AIOps limitations"
    ]


def test_runtime_state_preserves_local_library_identity_and_search_order() -> None:
    state = ResearchRuntimeState()
    state.store_search_results(
        "thread-a",
        json.dumps(
            [
                {
                    "paper_id": "W-local",
                    "library_id": "LP-local",
                    "title": "Local evidence",
                    "source": "library",
                    "sources": [
                        {
                            "source_id": "ABSTRACT-LP-local",
                            "snippet": "local evidence",
                        }
                    ],
                }
            ]
        ),
    )

    assert state.get_search_results("thread-a")[0]["library_id"] == "LP-local"
    assert state.get_search_results("thread-a")[0]["sources"] == ["library"]
    assert state.has_search_source("thread-a", "search_library") is False
    state.mark_search_source("thread-a", "search_library")
    assert state.has_search_source("thread-a", "search_library") is True


def test_runtime_state_normalizes_search_source_types_for_public_schema() -> None:
    state = ResearchRuntimeState()
    state.store_search_results(
        "thread-a",
        json.dumps(
            [
                {
                    "paper_id": "W-repository",
                    "title": "Repository paper",
                    "source": "OpenAlex",
                    "venue_type": "repository",
                },
                {
                    "paper_id": "W-proceedings",
                    "title": "Conference paper",
                    "source": "OpenAlex",
                    "venue_type": "proceedings",
                },
            ]
        ),
    )

    results = state.get_search_results("thread-a")
    assert results[0]["venue_type"] is None
    assert results[1]["venue_type"] == "conference"


def test_search_middleware_blocks_external_search_until_library_was_queried() -> None:
    state = ResearchRuntimeState()
    state.set_search_constraints(
        "thread-a",
        year_from=2024,
        year_to=2026,
        quality_venues_only=False,
        prefer_library_search=True,
    )
    middleware = ExecutedSearchTrackingMiddleware(state)
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-a"}})

    def request(name: str, query: str, call_id: str) -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={"name": name, "args": {"query": query}, "id": call_id},
            tool=None,
            state={},
            runtime=runtime,
        )

    blocked = middleware.wrap_tool_call(
        request("search_openalex", "external gap", "call-1"),
        lambda _request: "should not execute",
    )
    local = middleware.wrap_tool_call(
        request("search_library", "local topic", "call-2"),
        lambda _request: ToolMessage(
            content="[]",
            tool_call_id="call-2",
            name="search_library",
        ),
    )
    external = middleware.wrap_tool_call(
        request("search_openalex", "specific uncovered direction", "call-3"),
        lambda _request: ToolMessage(
            content="[]",
            tool_call_id="call-3",
            name="search_openalex",
        ),
    )

    assert blocked.status == "error"
    assert "local_library_search_required" in blocked.content
    assert local.status == "success"
    assert external.status == "success"


def test_search_middleware_skips_library_when_user_disables_priority() -> None:
    state = ResearchRuntimeState()
    state.set_search_constraints(
        "thread-a",
        year_from=2024,
        year_to=2026,
        quality_venues_only=False,
        prefer_library_search=False,
    )
    middleware = ExecutedSearchTrackingMiddleware(state)
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-a"}})

    def request(name: str, args: dict, call_id: str) -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={"name": name, "args": args, "id": call_id},
            tool=None,
            state={},
            runtime=runtime,
        )

    local = middleware.wrap_tool_call(
        request("search_library", {"query": "local topic"}, "call-local"),
        lambda _request: "should not execute",
    )
    external_request = request(
        "search_multi_source",
        {"queries": ["visual geolocation benchmark"]},
        "call-external",
    )
    external = middleware.wrap_tool_call(
        external_request,
        lambda _request: ToolMessage(
            content='{"candidates": []}',
            tool_call_id="call-external",
            name="search_multi_source",
        ),
    )

    assert local.status == "error"
    assert "local_library_search_disabled" in local.content
    assert external.status == "success"
    assert external_request.tool_call["args"] == {
        "queries": ["visual geolocation benchmark"],
        "year_from": 2024,
        "year_to": 2026,
        "quality_venues_only": False,
    }


def test_multi_source_search_tracks_each_query_and_captures_merged_candidates() -> None:
    state = ResearchRuntimeState()
    state.mark_search_source("thread-a", "search_library")
    middleware = ExecutedSearchTrackingMiddleware(state)
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-a"}})
    request = ToolCallRequest(
        tool_call={
            "name": "search_multi_source",
            "args": {
                "queries": ["image geolocation", "geo benchmark evaluation"],
            },
            "id": "call-multi",
        },
        tool=None,
        state={},
        runtime=runtime,
    )

    result = middleware.wrap_tool_call(
        request,
        lambda _request: ToolMessage(
            content=json.dumps(
                {
                    "candidates": [
                        {
                            "paper_id": "10.1000/geo",
                            "title": "Geo Paper",
                            "source": "OpenAlex + Crossref",
                            "sources": ["OpenAlex", "Crossref"],
                            "matched_queries": ["image geolocation"],
                            "relevance_score": 4.5,
                        }
                    ]
                }
            ),
            tool_call_id="call-multi",
            name="search_multi_source",
        ),
    )

    assert result.status == "success"
    assert state.search_terms("thread-a") == [
        "image geolocation",
        "geo benchmark evaluation",
    ]
    stored = state.get_search_results("thread-a")[0]
    assert stored["sources"] == ["OpenAlex", "Crossref"]
    assert stored["relevance_score"] == 4.5


def test_recording_runnable_injects_shared_memory_before_current_task() -> None:
    class CapturingAgent(FakeStructuredAgent):
        captured_inputs = None

        def invoke(self, inputs, config=None):
            self.captured_inputs = inputs
            return super().invoke(inputs, config)

    state = ResearchRuntimeState()
    state.register_project("thread-a", "RP-memory")
    agent = CapturingAgent(
        {
            "topic": "geo",
            "consensus": [],
            "conflicts": [],
            "method_comparison": [],
            "gaps": [],
        }
    )
    runnable = recording_runnable(
        agent,
        "research-synthesizer",
        state,
        memory_provider=lambda project_id, role, task: {
            "project_id": project_id,
            "role": role,
            "current_task": task,
            "artifact_id": 17,
            "evidence_id": "P1:E1",
        },
    )

    runnable.invoke(
        {"messages": [HumanMessage(content="整合前序证据")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    assert isinstance(agent.captured_inputs["messages"][0], SystemMessage)
    memory_text = agent.captured_inputs["messages"][0].content
    assert "RP-memory" in memory_text
    assert "P1:E1" in memory_text
    assert agent.captured_inputs["messages"][-1].content == "整合前序证据"


def test_rejected_result_is_consumed_and_retry_count_resets_on_success() -> None:
    state = ResearchRuntimeState()
    state.record_result("thread-a", "research-synthesizer", {"topic": "t"})

    assert state.reject_result("thread-a", "research-synthesizer") == 1
    assert state.pending_result("thread-a", "research-synthesizer") is None
    assert state.rejection_count("thread-a", "research-synthesizer") == 1

    state.record_result("thread-a", "research-synthesizer", {"topic": "fixed"})
    state.mark_consumed("thread-a", "research-synthesizer")
    assert state.rejection_count("thread-a", "research-synthesizer") == 0


def test_paper_reader_rejections_are_isolated_by_paper_id() -> None:
    state = ResearchRuntimeState()

    assert state.reject_result("thread-a", "paper-reader", "W1") == 1
    assert state.reject_result("thread-a", "paper-reader", "W1") == 2
    assert state.rejection_count("thread-a", "paper-reader", "W1") == 2
    assert state.rejection_count("thread-a", "paper-reader", "W2") == 0

    assert state.reject_result("thread-a", "paper-reader", "W2") == 1
    state.mark_consumed("thread-a", "paper-reader", "W2")
    assert state.rejection_count("thread-a", "paper-reader", "W2") == 0
    assert state.rejection_count("thread-a", "paper-reader", "W1") == 2


def test_paper_fetch_guard_blocks_duplicates_and_per_paper_overflow() -> None:
    state = ResearchRuntimeState()
    guard = PaperFetchGuardMiddleware(state, max_attempts_per_paper=2)

    def request(url: str):
        return SimpleNamespace(
            tool_call={
                "name": "fetch_paper_text",
                "args": {
                    "paper_id": "https://openalex.org/W123",
                    "doi": "10.1/example",
                    "url": url,
                },
                "id": "call-fetch",
            },
            runtime=SimpleNamespace(
                config={"configurable": {"thread_id": "thread-a"}}
            ),
        )

    assert guard.wrap_tool_call(request("https://one.test/p.pdf"), lambda _: "ok") == "ok"
    duplicate = guard.wrap_tool_call(
        request("https://one.test/p.pdf"), lambda _: "unexpected"
    )
    assert isinstance(duplicate, ToolMessage)
    assert json.loads(duplicate.content)["error_code"] == "duplicate_paper_fetch"
    assert guard.wrap_tool_call(request("https://two.test/p.pdf"), lambda _: "ok2") == "ok2"
    overflow = guard.wrap_tool_call(
        request("https://three.test/p.pdf"), lambda _: "unexpected"
    )
    assert isinstance(overflow, ToolMessage)
    assert json.loads(overflow.content)["error_code"] == "paper_fetch_limit_reached"


def test_missing_structured_response_becomes_recoverable_result() -> None:
    state = ResearchRuntimeState()
    runnable = recording_runnable(FakeStructuredAgent(None), "paper-reader", state)

    result = runnable.invoke(
        {
            "messages": [
                HumanMessage(content='paper_id（必须使用此值）: "https://openalex.org/W9"')
            ]
        },
        config={"configurable": {"thread_id": "thread-a"}},
    )

    pending = state.pending_result("thread-a", "paper-reader")
    assert pending["_subagent_error"] == "structured_response_missing"
    assert pending["_paper_id"] == "https://openalex.org/W9"
    assert pending["_diagnostics"]["raw_provider_response_captured"] is False
    assert result["structured_response"] == pending


def test_missing_structured_response_preserves_invalid_tool_call_details() -> None:
    state = ResearchRuntimeState()
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
    runnable = recording_runnable(
        FakeStructuredAgent(None, messages=[message]),
        "research-synthesizer",
        state,
    )

    runnable.invoke(
        {"messages": [HumanMessage(content="synthesize")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    pending = state.pending_result("thread-a", "research-synthesizer")
    diagnostics = pending["_diagnostics"]
    assert diagnostics["expected_schema_tool"] == "SynthesisReport"
    assert diagnostics["finish_reason"] == "tool_calls"
    assert diagnostics["parse_status"] == "invalid_tool_calls"
    assert diagnostics["invalid_tool_calls"][0]["args"] == '{"topic": "broken"'
    assert diagnostics["invalid_tool_calls"][0]["error"] == "invalid JSON"


def test_missing_structured_response_redacts_sensitive_diagnostics() -> None:
    state = ResearchRuntimeState()
    message = AIMessage(
        content="Bearer private-token",
        response_metadata={
            "finish_reason": "stop",
            "api_key": "sk-private",
        },
    )
    runnable = recording_runnable(
        FakeStructuredAgent(None, messages=[message]),
        "research-synthesizer",
        state,
    )

    runnable.invoke(
        {"messages": [HumanMessage(content="synthesize")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    diagnostics = state.pending_result("thread-a", "research-synthesizer")["_diagnostics"]
    assert diagnostics["parse_status"] == "unparsed_content"
    assert diagnostics["content"] == "Bearer [REDACTED]"
    assert diagnostics["response_metadata"]["api_key"] == "[REDACTED]"


def test_missing_structured_response_recovers_schema_tool_call_payload() -> None:
    state = ResearchRuntimeState()
    payload = {
        "topic": "topic",
        "consensus": [{"statement": "finding", "evidence_ids": ["P1:E1"]}],
        "conflicts": [],
        "method_comparison": [],
        "gaps": [],
    }
    agent = FakeStructuredAgent(
        None,
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "SynthesisReport",
                        "args": payload,
                        "id": "call-schema",
                    }
                ],
            )
        ],
    )
    runnable = recording_runnable(agent, "research-synthesizer", state)

    result = runnable.invoke(
        {"messages": [HumanMessage(content="synthesize")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    pending = state.pending_result("thread-a", "research-synthesizer")
    assert pending == payload
    assert result["structured_response"] == payload


def test_missing_structured_response_rejects_project_snapshot_payload() -> None:
    state = ResearchRuntimeState()
    snapshot = {
        "project": {"project_id": "RP-test", "stage": "OUTLINED"},
        "artifacts": [],
        "events": [],
    }
    agent = FakeStructuredAgent(
        None,
        messages=[
            ToolMessage(
                content=json.dumps(snapshot),
                tool_call_id="call-project",
                name="get_active_research_project",
            )
        ],
    )
    runnable = recording_runnable(agent, "chief-editor", state)

    result = runnable.invoke(
        {"messages": [HumanMessage(content="edit")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    pending = state.pending_result("thread-a", "chief-editor")
    assert pending["_subagent_error"] == "structured_response_missing"
    assert result["structured_response"] == pending


def test_missing_structured_response_recovers_narrative_review_payload() -> None:
    state = ResearchRuntimeState()
    payload = {
        "title": "Review",
        "abstract": "Summary.",
        "sections": [
            {
                "section_id": "sec-1",
                "heading": "Intro",
                "content": "Body",
                "cited_evidence": ["P1:E1"],
            }
        ],
        "references": [{"paper_id": "P1", "text": "Author. Title."}],
        "writing_style": "academic-survey",
        "word_count": 10,
        "evidence_chain": {"P1:E1": ["sec-1"]},
    }
    agent = FakeStructuredAgent(None, messages=[AIMessage(content=json.dumps(payload))])
    runnable = recording_runnable(agent, "chief-editor", state)

    result = runnable.invoke(
        {"messages": [HumanMessage(content="edit")]},
        config={"configurable": {"thread_id": "thread-a"}},
    )

    pending = state.pending_result("thread-a", "chief-editor")
    assert pending == payload
    assert result["structured_response"] == payload


def test_paper_fetch_attempts_can_reset_for_a_fresh_subagent() -> None:
    state = ResearchRuntimeState()
    args = ("thread-a", "https://openalex.org/W9", "", "https://x.test/p.pdf", 2)

    assert state.reserve_paper_fetch(*args) is None
    assert state.reserve_paper_fetch(*args) == "duplicate_paper_fetch"
    state.reset_paper_fetch("thread-a", "https://openalex.org/W9")
    assert state.reserve_paper_fetch(*args) is None
