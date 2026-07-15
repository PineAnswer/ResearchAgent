from __future__ import annotations

from collections.abc import Mapping, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from research_agent.agents.prompts import (
    READER_PROMPT,
    REVIEWER_PROMPT,
    SCOUT_PROMPT,
    SYNTHESIZER_PROMPT,
)
from research_agent.agents.runtime_state import (
    ExecutedSearchTrackingMiddleware,
    PaperFetchGuardMiddleware,
    ResearchRuntimeState,
    recording_runnable,
)
from research_agent.agents.serial_tools import SerialToolExecutionMiddleware
from research_agent.domain.models import PaperCard, ReviewResult, SearchReport, SynthesisReport


def _bounded_middleware(tool_call_limit: int) -> list:
    return [
        SerialToolExecutionMiddleware(),
        ToolCallLimitMiddleware(run_limit=tool_call_limit, exit_behavior="end"),
    ]


def build_subagent_registry(
    tools_by_name: Mapping[str, BaseTool],
    skill_paths: Mapping[str, str],
    model: str | BaseChatModel,
    runtime_state: ResearchRuntimeState | None = None,
    max_openalex_searches: int = 3,
    max_crossref_searches: int = 1,
    max_paper_fetches_per_paper: int = 2,
) -> Sequence[dict]:
    del skill_paths  # Compiled narrow agents do not receive general filesystem capabilities.
    state = runtime_state or ResearchRuntimeState()

    scout_tools = [tools_by_name["search_openalex"]]
    if max_crossref_searches > 0:
        scout_tools.append(tools_by_name["search_crossref"])
    scout_middleware = [
        SerialToolExecutionMiddleware(),
        ToolCallLimitMiddleware(
            tool_name="search_openalex",
            run_limit=max_openalex_searches,
            exit_behavior="continue",
        ),
    ]
    if max_crossref_searches > 0:
        scout_middleware.append(
            ToolCallLimitMiddleware(
                tool_name="search_crossref",
                run_limit=max_crossref_searches,
                exit_behavior="continue",
            )
        )
    scout_middleware.append(ExecutedSearchTrackingMiddleware(state))

    configured = [
        (
            "literature-scout",
            "检索并筛选学术论文，返回结构化 SearchReport。",
            SCOUT_PROMPT,
            scout_tools,
            SearchReport,
            scout_middleware,
        ),
        (
            "paper-reader",
            "获取开放全文或使用摘要证据，并生成一篇 PaperCard。",
            READER_PROMPT,
            [tools_by_name["fetch_paper_text"], tools_by_name["extract_pdf_text"]],
            PaperCard,
            [
                SerialToolExecutionMiddleware(),
                ModelCallLimitMiddleware(run_limit=4, exit_behavior="end"),
                ToolCallLimitMiddleware(
                    tool_name="extract_pdf_text",
                    run_limit=1,
                    exit_behavior="end",
                ),
                PaperFetchGuardMiddleware(state, max_paper_fetches_per_paper),
            ],
        ),
        (
            "research-synthesizer",
            "只基于可定位 Evidence 比较论文并生成 SynthesisReport。",
            SYNTHESIZER_PROMPT,
            [tools_by_name["get_active_research_project"]],
            SynthesisReport,
            _bounded_middleware(2),
        ),
        (
            "evidence-reviewer",
            "只读审查引用、证据和结论的对应关系。",
            REVIEWER_PROMPT,
            [tools_by_name["get_active_research_project"]],
            ReviewResult,
            [
                SerialToolExecutionMiddleware(),
                ModelCallLimitMiddleware(run_limit=3, exit_behavior="end"),
                ToolCallLimitMiddleware(
                    tool_name="get_active_research_project",
                    run_limit=1,
                    exit_behavior="end",
                ),
            ],
        ),
    ]

    registry = []
    for name, description, prompt, tools, schema, middleware in configured:
        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=prompt,
            response_format=schema.model_json_schema(),
            middleware=middleware,
            name=name,
        )
        registry.append(
            {
                "name": name,
                "description": description,
                "runnable": recording_runnable(agent, name, state),
            }
        )
    return registry
