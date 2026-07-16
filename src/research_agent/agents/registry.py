from __future__ import annotations

from collections.abc import Mapping, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from research_agent.agents.prompts import (
    CHIEF_EDITOR_PROMPT,
    FACT_CHECKER_PROMPT,
    NARRATIVE_WRITER_PROMPT,
    OUTLINER_PROMPT,
    READER_PROMPT,
    REVIEWER_PROMPT,
    SCOUT_PROMPT,
    SYNTHESIZER_PROMPT,
    inject_skill,
)
from research_agent.agents.runtime_state import (
    ExecutedSearchTrackingMiddleware,
    PaperFetchGuardMiddleware,
    ResearchRuntimeState,
    recording_runnable,
)
from research_agent.agents.serial_tools import SerialToolExecutionMiddleware
from research_agent.domain.models import (
    FactCheckReport,
    NarrativeReview,
    PaperCard,
    ReviewOutline,
    ReviewResult,
    SearchReport,
    SectionDraft,
    SynthesisReport,
)


def _bounded_middleware(tool_call_limit: int) -> list:
    return [
        SerialToolExecutionMiddleware(),
        ToolCallLimitMiddleware(run_limit=tool_call_limit, exit_behavior="end"),
    ]


def build_subagent_registry(
    tools_by_name: Mapping[str, BaseTool],
    skill_contents: Mapping[str, str],
    model: str | BaseChatModel,
    runtime_state: ResearchRuntimeState | None = None,
    max_openalex_searches: int = 3,
    max_crossref_searches: int = 1,
    max_paper_fetches_per_paper: int = 2,
) -> Sequence[dict]:
    state = runtime_state or ResearchRuntimeState()
    required_skills = {
        "literature-search",
        "paper-reading",
        "research-synthesis",
        "evidence-review",
    }
    missing_skills = sorted(required_skills - set(skill_contents))
    if missing_skills:
        raise ValueError("Missing subagent Skills: " + ", ".join(missing_skills))

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
            inject_skill(
                SCOUT_PROMPT,
                "literature-search",
                skill_contents["literature-search"],
            ),
            scout_tools,
            SearchReport,
            scout_middleware,
        ),
        (
            "paper-reader",
            "获取开放全文或使用摘要证据，并生成一篇 PaperCard。",
            inject_skill(READER_PROMPT, "paper-reading", skill_contents["paper-reading"]),
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
            inject_skill(
                SYNTHESIZER_PROMPT,
                "research-synthesis",
                skill_contents["research-synthesis"],
            ),
            [tools_by_name["get_active_research_project"]],
            SynthesisReport,
            _bounded_middleware(2),
        ),
        (
            "evidence-reviewer",
            "只读审查引用、证据和结论的对应关系。",
            inject_skill(
                REVIEWER_PROMPT,
                "evidence-review",
                skill_contents["evidence-review"],
            ),
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
        # ── DeepSynthesis narration subagents ─────────────────────
        (
            "research-outliner",
            "读取全部论文卡片和证据，设计文献综述的章节大纲。",
            OUTLINER_PROMPT,
            [tools_by_name["get_active_research_project"]],
            ReviewOutline,
            _bounded_middleware(2),
        ),
        (
            "narrative-writer",
            "根据提纲和指定的 section_id 撰写一节连贯的文献综述正文。",
            NARRATIVE_WRITER_PROMPT,
            [tools_by_name["get_active_research_project"]],
            SectionDraft,
            _bounded_middleware(2),
        ),
        (
            "chief-editor",
            "将所有 SectionDraft 整合为完整的 NarrativeReview，含引言、总结和参考文献。",
            CHIEF_EDITOR_PROMPT,
            [tools_by_name["get_active_research_project"]],
            NarrativeReview,
            _bounded_middleware(2),
        ),
        (
            "fact-checker",
            "核查综述中每条证据引用是否被原始证据支持，输出 FactCheckReport。",
            FACT_CHECKER_PROMPT,
            [tools_by_name["get_active_research_project"]],
            FactCheckReport,
            _bounded_middleware(2),
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
