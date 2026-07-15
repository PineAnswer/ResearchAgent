---
name: research-protocol
description: 证据驱动科研项目的总流程与状态推进规范
---

# Research Protocol

所有工具和子 Agent 必须串行调用：一条 AI 消息只调用一个工具，等待结果后再继续。

## 第一步：创建项目

- 调用 `create_research_project` 并记录 `project_id`。
- 成功创建项目之前禁止委派任何子 Agent，也禁止保存产物。

## 第二步：检索文献 → SEARCHED

- 委派一次 literature-scout。
- literature-scout 每个任务只能委派一次；禁止使用 general-purpose 或第二次委派绕过检索限制。
- 调用 `commit_subagent_result(project_id, "literature-scout")`，由系统原样提交结构化结果并进入 SEARCHED。
- 如果 SearchReport 的 `candidates` 为空，立即调用 `finish_inconclusive` 保存检索词、失败原因和建议，项目进入 `INCONCLUSIVE` 并正常结束。禁止创建空 ScreeningDecision 或继续到 EXTRACTED。

## 第三步：筛选论文 → SCREENED

- 根据 SearchReport 生成 ScreeningDecision。
- ScreeningDecision 的固定格式如下；`reasons` 只能包含字符串，并按入选论文顺序描述理由：

```json
{
  "included_paper_ids": ["P001"],
  "excluded_paper_ids": ["P002"],
  "reasons": ["P001：与研究问题直接相关；P002：仅讨论相邻问题"]
}
```

- 调用 `save_screening_decision`，原子保存并进入 SCREENED。

## 第四步：精读论文 → EXTRACTED

按入选论文逐篇完成，禁止一次发出多个 `task`：

1. 从 SearchReport 复制该论文完整元数据，包括 paper_id、title、authors、year、abstract、doi、url、source。
2. 委派一个 paper-reader。它会使用 `fetch_paper_text` 自动尝试 OpenAlex/arXiv 开放全文。
3. 收到 PaperCard 后立即调用 `commit_subagent_result(project_id, "paper-reader")` 原样保存。
5. 保存成功后再处理下一篇。
6. 全部入选论文保存完成后，调用 `advance_project_stage(project_id, "EXTRACTED", "paper-reader")`。

PaperCard 官方字段固定为：

```json
{
  "paper_id": "P001",
  "title": "论文标题",
  "research_question": "研究问题",
  "methods": [],
  "datasets": [],
  "findings": [
    {
      "evidence_id": "P001-E1",
      "paper_id": "P001",
      "claim": "有证据支持的结论",
      "quote": "PDF原文",
      "page": 1,
      "section": "章节"
    }
  ],
  "limitations": []
}
```

全文不可用但摘要非空时，可保存明确标记为 abstract 的摘要级 Evidence。全文和摘要均不可用时 findings 为空。
全部卡片保存后若 EXTRACTED 返回 `insufficient_evidence`，立即调用 `finish_inconclusive`，禁止进入综合。

## 第五步：综合比较 → SYNTHESIZED

- 委派 research-synthesizer，任务中必须复制创建项目工具返回的原始 `project_id`，并提供研究主题和研究问题。
- 禁止向任务中复制论文列表、猜测项目ID或自行定义 SynthesisReport JSON；综合 Agent 通过当前运行绑定的只读工具获取已保存产物。
- 调用 `commit_subagent_result(project_id, "research-synthesizer")` 原样提交并进入 SYNTHESIZED。
- 如果提交返回 `retry_allowed=true`，旧结果已被系统丢弃；根据错误原因重新委派 synthesizer 一次。再次失败或 `retry_allowed=false` 时调用 `finish_inconclusive`，禁止重复提交旧结果。

## 第六步：同行审查 → REVIEWED

- 推进到 REVIEW_PENDING。
- 委派 evidence-reviewer。
- 调用 `commit_subagent_result(project_id, "evidence-reviewer")` 原样提交并进入 REVIEWED。

## 第七步：完成

- PASS：推进到 COMPLETED。
- REVISE：明确标记“报告需要修订”；可返回 EXTRACTED 修订一次，证据无法补充时进入 INCONCLUSIVE。

## 停止规则

- 子 Agent 返回 `_subagent_error` 时仍调用 `commit_subagent_result`，由系统记录拒绝并释放结果；根据 `retry_allowed` 决定重新委派或进入 `INCONCLUSIVE`。
- 同一工具错误连续出现两次时停止重试，保留局限并进入可继续的下一步。
- 子 Agent 达到工具调用上限时，Supervisor不得以相同指令重复委派。
- 所有状态变化必须经过 Python 状态机。
