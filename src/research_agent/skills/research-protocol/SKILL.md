---
name: research-protocol
description: 证据驱动科研项目的总流程与状态推进规范
---

# Research Protocol

所有工具和子 Agent 必须串行调用：一条 AI 消息只调用一个工具，等待结果后再继续。

## 第一步：创建项目

- 调用 `create_research_project` 并记录 `project_id`。
- 成功创建项目之前禁止委派任何子 Agent，也禁止保存产物。

## 第二步：检索文献 → SEARCH_REVIEW_PENDING

- 委派一次 literature-scout。
- literature-scout 正常情况下每个任务只委派一次；只有首次结构校验失败且提交工具明确返回 `retry_allowed=true` 时，才允许修正后重试一次。
- literature-scout 负责检索策略设计和标题摘要初筛，只输出 candidate_ids、screening_decisions、screening_reasons、coverage_gaps、search_iteration_log、selection_notes。candidate_ids 必须使用搜索工具返回的真实 paper_id 或 DOI，禁止使用 P001/P002 等临时编号。论文完整元数据由系统从搜索工具返回中自动捕获并重建 candidates 列表。
- 调用 `commit_subagent_result(project_id, "literature-scout")`，由系统重建 candidates 并提交结构化结果，进入 SEARCHED。
- 如果 SearchReport 的 `candidates` 为空，仍保存 SearchReport 和空 CandidateSetSnapshot，进入 `SEARCH_REVIEW_PENDING` 等待用户补充查询或手动加入论文；不得自动进入 `INCONCLUSIVE`。
- 如果存在候选论文，系统会创建候选集快照并进入 `SEARCH_REVIEW_PENDING`。立即停止本轮 Agent 执行，等待用户通过检索审核 API 补充查询、加入或排除论文。

## 第三步：用户确认候选集 → SCREENED

- 只有用户提交 `action=accept` 后，检索审核服务才能根据当前候选集生成 ScreeningDecision。
- Supervisor在 `SEARCH_REVIEW_PENDING` 阶段禁止自行调用 `save_screening_decision`。
- ScreeningDecision 的固定格式如下；`reasons` 只能包含字符串，并按入选论文顺序描述理由：

```json
{
  "included_paper_ids": ["W4409797280"],
  "excluded_paper_ids": ["10.1109/example"],
  "reasons": ["W4409797280：与研究问题直接相关；10.1109/example：仅讨论相邻问题"]
}
```

- 检索审核服务原子保存 `ScreeningDecision` 并进入 SCREENED。
- 新一轮继续执行收到已绑定的 `project_id` 和 `SCREENED` 状态后，禁止重新创建项目或重新检索，从逐篇 `paper-reader` 开始。

## 第四步：精读论文 → EXTRACTED

按入选论文逐篇完成，禁止一次发出多个 `task`：

1. 从 SearchReport 复制该论文完整元数据，包括 paper_id、library_id、title、authors、year、abstract、doi、url、source。
2. 委派一个 paper-reader。`library_id` 非空时它只检索一次本地索引；为空时才尝试 OpenAlex/arXiv 开放全文。任务描述只传元数据和研究问题，禁止复制工具调用签名或定义 PaperCard JSON。
3. 收到 PaperCard 后立即调用 `commit_subagent_result(project_id, "paper-reader")` 原样保存。
4. 保存成功后再处理下一篇。
5. 全部入选论文保存完成后，调用 `advance_project_stage(project_id, "EXTRACTED", "paper-reader")`。

全文不可用但摘要非空时，可保存明确标记为 abstract 的摘要级 Evidence。全文和摘要均不可用时 findings 为空。
全部卡片的 findings 都为空时仍推进到 EXTRACTED。后续 SynthesisReport 的四个结论列表保持为空，并明确说明只有元数据、没有可定位证据；不得虚构综合结论。

## 第五步：综合比较 → SYNTHESIZED

- 委派 research-synthesizer，任务中必须复制创建项目工具返回的原始 `project_id`，并提供研究主题和研究问题。
- 禁止向任务中复制论文列表、猜测项目ID或自行定义 SynthesisReport JSON；综合 Agent 通过当前运行绑定的只读工具获取已保存产物。
- 调用 `commit_subagent_result(project_id, "research-synthesizer")` 原样提交并进入 SYNTHESIZED。
- 如果提交返回 `retry_allowed=true`，旧结果已被系统丢弃；根据错误原因重新委派 synthesizer 一次。再次失败或 `retry_allowed=false` 时调用 `record_research_issue`，保留当前阶段并停止本轮，禁止重复提交旧结果。

## 第六步：同行审查 → REVIEWED

- 推进到 REVIEW_PENDING。
- 委派 evidence-reviewer。
- 调用 `commit_subagent_result(project_id, "evidence-reviewer")` 原样提交并进入 REVIEWED。

## 第七步：审查分流

- PASS：在同一运行中继续提纲和正文写作。
- 首次 REVISE：立即返回 EXTRACTED，复用现有 PaperCard 和 Evidence 修订一份新的 SynthesisReport，再次进入 REVIEW_PENDING 独立审查。禁止重新检索、重新筛选或重读论文。
- 第二次仍为 REVISE：调用 `record_research_issue` 保存审查问题并停在 REVIEWED，禁止无限修订或进入 INCONCLUSIVE。

## 第八步：综述提纲 → OUTLINED

- 综述采用中文撰写。ReviewOutline 的标题、叙事线、章节标题和核心论点均使用中文；论文原始标题、模型名、数据集名、缩写及必要专业术语可以保留英文。
- 在 REVIEWED 且审查为 PASS 时委派 `research-outliner`。
- 调用 `commit_subagent_result(project_id, "research-outliner")` 原样提交 ReviewOutline 并进入 OUTLINED。
- ReviewOutline 的每个 `section_id` 必须唯一，并明确分配论文、Evidence、核心论点和目标字数。

## 第九步：分节写作与总编整合 → COMPLETED

- SectionDraft 与最终 NarrativeReview 的摘要、引言、章节正文、过渡语和结论必须使用中文；参考文献原始题名和 BibTeX 保持原始语言。
- 按 ReviewOutline 顺序逐节委派 `narrative-writer`，每次任务只指定一个 `section_id`。
- 每节完成后立即调用 `commit_subagent_result(project_id, "narrative-writer")` 保存 SectionDraft，再处理下一节。
- 已保存的 SectionDraft 不得重复生成；恢复执行时只补写缺失章节。
- 全部提纲章节都有 SectionDraft 后，委派 `chief-editor` 整合完整 NarrativeReview。
- 调用 `commit_subagent_result(project_id, "chief-editor")` 原样提交完整 NarrativeReview 并直接进入 COMPLETED。
- NarrativeReview 保存成功后立即结束执行，不再委派任何后续 Agent，也不再执行事实核查或正文修订流程。

## 停止规则

- 子 Agent 返回 `_subagent_error` 时仍调用 `commit_subagent_result`，由系统记录拒绝并释放结果；根据 `retry_allowed` 决定重新委派或调用 `record_research_issue` 保持当前阶段。
- 证据不足、空候选、来源限流和结构化输出失败都不得由 Supervisor 自动调用 `finish_inconclusive`。
- 工具返回结构化错误时严格遵循其中的 `instruction` 和 `retry_allowed`；禁止为了继续流程而跳过前置产物或非法推进状态。
- 子 Agent 达到工具调用上限时，Supervisor不得以相同指令重复委派。
- 所有状态变化必须经过 Python 状态机。
