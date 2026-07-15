from __future__ import annotations

import argparse
import json
import sys
from dotenv import load_dotenv

from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.demo import run_offline_demo
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository
from research_agent.application.research_service import ResearchService


def _configure_console() -> None:
    """Use UTF-8 output on Windows and replace any unsupported terminal glyphs."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _last_message_text(result: dict) -> str:
    messages = result.get("messages", [])
    if not messages:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    last = messages[-1]
    content = getattr(last, "content", None)
    return content if isinstance(content, str) else str(content or last)


def _project_status_text(result: dict) -> str:
    project = result.get("project_status") or {}
    stage = project.get("stage", "UNKNOWN")
    review = project.get("current_review") or {}
    verdict = review.get("verdict")
    if stage == "COMPLETED" and verdict == "PASS":
        return "科研项目已通过证据审查并完成。"
    if stage == "INCONCLUSIVE":
        return "本轮执行已结束：证据不足，当前没有可确认的科研结论。"
    if stage == "SEARCH_REVIEW_PENDING":
        return "初次检索已完成：当前正在等待用户审核候选论文。"
    if verdict == "REVISE":
        return "本轮执行已结束：报告需要修订，尚未通过证据审查。"
    return f"本轮执行已结束：当前项目阶段为{stage}。"


def main() -> None:
    _configure_console()
    load_dotenv()
    parser = argparse.ArgumentParser(description="Evidence-driven Deep Agents research demo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("demo", help="Run the offline deterministic workflow")

    run_parser = subparsers.add_parser("run", help="Run the Deep Agents coordinator")
    run_parser.add_argument("topic")
    run_parser.add_argument("--question", default="请检索文献、提取证据并形成研究空白报告")
    run_parser.add_argument("--thread-id", default=None)

    status_parser = subparsers.add_parser("status", help="Show a saved project")
    status_parser.add_argument("project_id")

    serve_parser = subparsers.add_parser("serve", help="Start the FastAPI service")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "demo":
        print(json.dumps(run_offline_demo(settings.database_path), ensure_ascii=False, indent=2))
        return

    if args.command == "status":
        service = ResearchService(SqliteResearchRepository(settings.database_path))
        result = service.get_snapshot(args.project_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "research_agent.api.app:app",
            host=args.host or settings.api_host,
            port=args.port or settings.api_port,
            reload=False,
        )
        return

    supervisor = ResearchSupervisor(settings)
    outcome = supervisor.invoke_with_fallback(
        args.topic,
        args.question,
        args.thread_id,
        show_progress=True,
    )
    if outcome.get("mode") == "agent":
        print("\n===== 确定性项目状态 =====")
        print(_project_status_text(outcome["result"]))
        print("\n===== Agent 报告草稿 =====")
        print(_last_message_text(outcome["result"]))
    else:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
    print(f"\n[运行日志] {outcome['run_log_dir']}")


if __name__ == "__main__":
    main()
