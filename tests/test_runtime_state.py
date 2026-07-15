import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, ToolMessage

from research_agent.agents.runtime_state import (
    PaperFetchGuardMiddleware,
    ResearchRuntimeState,
    recording_runnable,
)


class FakeStructuredAgent:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, inputs, config=None):
        return {"messages": inputs["messages"], "structured_response": self.payload}

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


def test_rejected_result_is_consumed_and_retry_count_resets_on_success() -> None:
    state = ResearchRuntimeState()
    state.record_result("thread-a", "research-synthesizer", {"topic": "t"})

    assert state.reject_result("thread-a", "research-synthesizer") == 1
    assert state.pending_result("thread-a", "research-synthesizer") is None
    assert state.rejection_count("thread-a", "research-synthesizer") == 1

    state.record_result("thread-a", "research-synthesizer", {"topic": "fixed"})
    state.mark_consumed("thread-a", "research-synthesizer")
    assert state.rejection_count("thread-a", "research-synthesizer") == 0


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
    assert result["structured_response"] == pending


def test_paper_fetch_attempts_can_reset_for_a_fresh_subagent() -> None:
    state = ResearchRuntimeState()
    args = ("thread-a", "https://openalex.org/W9", "", "https://x.test/p.pdf", 2)

    assert state.reserve_paper_fetch(*args) is None
    assert state.reserve_paper_fetch(*args) == "duplicate_paper_fetch"
    state.reset_paper_fetch("thread-a", "https://openalex.org/W9")
    assert state.reserve_paper_fetch(*args) is None
