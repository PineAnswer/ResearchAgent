"""模型能力诊断：验证当前 .env 配置的模型能否支撑 Agent 工作流。

独立运行，不读写数据库。用法（在项目根目录、激活虚拟环境后）：

    python scripts/diagnose_model.py

依次测试四项能力：基础对话、工具调用、结构化输出（function_calling）、
create_agent + ToolStrategy 端到端——后两项分别是"项目/论文问答"和
"文献库助手 + 七个研究子 Agent"实际使用的机制。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from research_agent.agents.supervisor import ResearchSupervisor  # noqa: E402
from research_agent.infrastructure.config import Settings  # noqa: E402


class FinalAnswer(BaseModel):
    """诊断用最简结构化响应。"""

    status: str
    detail: str = ""


@tool
def ping(text: str) -> str:
    """回显收到的文本，用于验证工具调用链路。"""
    return f"pong:{text}"


def _brief(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:220]}"


def main() -> int:
    settings = Settings.from_env()
    provider, model_name = settings.resolved_model()
    print(f"配置解析：provider={provider} model={model_name}")

    shell = object.__new__(ResearchSupervisor)
    shell.settings = settings
    try:
        model = shell._build_model()
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需要展示任何失败
        print(f"[失败] 模型初始化：{_brief(exc)}")
        return 1
    print(f"[通过] 模型初始化：{type(model).__name__}")

    results: dict[str, bool] = {}

    # 1. 基础对话
    try:
        reply = model.invoke([HumanMessage(content="请只回复两个字符：OK")])
        text = str(getattr(reply, "content", reply))[:80]
        results["chat"] = True
        print(f"[通过] 基础对话：{text!r}")
    except Exception as exc:  # noqa: BLE001
        results["chat"] = False
        print(f"[失败] 基础对话：{_brief(exc)}")

    # 2. 工具调用
    try:
        bound = model.bind_tools([ping])
        reply = bound.invoke([HumanMessage(content="请调用 ping 工具，参数 text 为 hello。")])
        calls = list(getattr(reply, "tool_calls", None) or [])
        results["tools"] = bool(calls)
        if calls:
            print(f"[通过] 工具调用：模型请求了 {calls[0]['name']}({calls[0]['args']})")
        else:
            print("[失败] 工具调用：模型未发起 tool_call，直接返回了文本")
    except Exception as exc:  # noqa: BLE001
        results["tools"] = False
        print(f"[失败] 工具调用：{_brief(exc)}")

    # 3. 结构化输出——与 supervisor 的项目/论文问答完全相同的调用方式
    try:
        structured = model.with_structured_output(FinalAnswer, method="function_calling")
        obj = structured.invoke(
            [HumanMessage(content="请提交 status='done'、detail='结构化输出可用' 的结果。")]
        )
        results["structured"] = isinstance(obj, FinalAnswer)
        if results["structured"]:
            print(f"[通过] 结构化输出：{obj!r}")
        else:
            print(f"[失败] 结构化输出：返回 {obj!r}（应为 FinalAnswer 实例）")
    except Exception as exc:  # noqa: BLE001
        results["structured"] = False
        print(f"[失败] 结构化输出：{_brief(exc)}")

    # 4. Agent 端到端——文献库助手与研究子 Agent 使用的机制
    try:
        from langchain.agents import create_agent
        from langchain.agents.structured_output import ToolStrategy

        agent = create_agent(
            model=model,
            tools=[ping],
            system_prompt=(
                "你是诊断 Agent。先调用 ping 工具一次（text='hi'），"
                "然后必须通过结构化响应工具提交 FinalAnswer(status='done')。"
            ),
            response_format=ToolStrategy(FinalAnswer),
        )
        result = agent.invoke({"messages": [HumanMessage(content="开始诊断。")]})
        payload = result.get("structured_response") if isinstance(result, dict) else None
        results["agent"] = payload is not None
        if payload is not None:
            print(f"[通过] Agent 结构化提交：{payload!r}")
        else:
            messages = result.get("messages") if isinstance(result, dict) else []
            print(
                "[失败] Agent 结构化提交：structured_response 为空"
                f"（共 {len(messages or [])} 条消息）——这正是文献库助手降级的直接原因"
            )
    except Exception as exc:  # noqa: BLE001
        results["agent"] = False
        print(f"[失败] Agent 端到端：{_brief(exc)}")

    print("\n诊断结论：")
    if not results.get("chat"):
        print("- 模型基础调用不可用：先检查凭据、区域和网络连通性（与 Agent 机制无关）。")
    elif not results.get("tools"):
        print(
            "- 该模型（或其接入层）未实现 LangChain 工具调用协议。检索、精读、文献库助手等"
            "全部 Agent 功能都无法工作，建议改用对 tool calling 支持完善的模型"
            "（如 Claude、GPT 系列）。"
        )
    elif not results.get("agent"):
        print(
            "- 模型能调用业务工具，但不遵守『通过结构化响应工具提交结果』的约定。"
            "文献库助手会尝试从正文恢复带 [[source_id]] 的回答，正文也无标记时降级为本地检索。"
            "建议优先换用 Claude/GPT 系列模型；或继续当前模型并接受部分场景降级。"
        )
    else:
        print("- 四项能力全部可用。若文献库助手仍降级，请查看服务端日志中的 warning 详情。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
