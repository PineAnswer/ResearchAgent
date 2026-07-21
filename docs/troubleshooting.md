# 故障诊断与当前限制

本文记录当前版本的排错顺序、常见失败类型和已确认限制。运行报告中的自然语言总结只用于辅助阅读；项目阶段、Artifact 和状态事件以 SQLite 及 `outputs/<project-id>/snapshot.json` 为准。

## 1. 推荐诊断顺序

1. 打开 `.research-agent/runs/<run-id>/summary.json`，确认 `status`、`project_stage`、`review_verdict` 和 `error`。
2. 在 `events.jsonl` 中找到最后一个成功事件和第一个 `artifact.commit_failed`、`tool.error` 或 `run.inconclusive`。
3. 需要核对模型响应时查看 `messages.jsonl`，重点比较 `finish_reason`、`content`、工具调用和 token 统计。
4. 打开 `.research-agent/outputs/<project-id>/snapshot.json`，统计已经正式保存的 `PaperCard`、Evidence、`SynthesisReport` 和 `ReviewResult`。
5. 根据首个失败事件确定故障层级，避免只依据最终报告中的概括性描述判断。

## 2. `INCONCLUSIVE` 的含义

当前状态机使用 `INCONCLUSIVE` 表示流程受控结束，主要用于记录用户在人工定稿阶段主动停止，或确实无法形成可靠科研结论的证据不足场景。常见触发原因包括：

- 用户在人工检索审核阶段主动停止；
- 已有候选和补充检索均不足以支持继续研究，且用户选择停止；
- 其他由业务规则明确认定为证据不足、同时不应生成科研结论的情况。

空候选、空 findings、模型超时和结构化输出失败不会自动把项目改成 `INCONCLUSIVE`；执行类问题会保存为 `RuntimeIssue` 并保持在最近的安全阶段，便于后续恢复。判断根因时应读取最新 `InsufficientEvidence.reason`、`RuntimeIssue.reason` 和前序 `artifact.commit_failed` 事件。`INCONCLUSIVE` 仍是终态。

最终报告偶尔会使用“项目已完成”描述本轮程序调用已经结束。正式科研完成仍要求 `status=completed`、`project_stage=COMPLETED` 且 `review_verdict=PASS`；`error=null` 也只表示没有未处理的顶层异常。

## 3. 数值声明校验

`ResearchService` 会检查研究空白的 `proposed_hypothesis`，防止生成 Evidence 原文没有支持的精确数字。当前实现会忽略 `2D`、`3DVG`、`FFL-3DOG`、`ResNet-50` 和 `GPT-4` 等技术标识，并规范化千位分隔符、空格、百分号、`x/×/倍` 后执行精确集合比较。

如果对应 Evidence 引文没有相同的规范化数值，提交会返回：

```text
Synthesis hypothesis contains unsupported numeric claims: 3, 3, 3
```

诊断时应确认被拒绝的数值是否确实出现在当前 gap 引用的 Evidence 原文中；年份 `2023` 不会再被错误地用于支持单独的 `3`。

## 4. `structured_response_missing`

子 Agent 通过结构化响应 schema 返回 `SearchReport`、`PaperCard`、`SynthesisReport`、`ReviewResult`、`ReviewOutline`、`SectionDraft` 或 `NarrativeReview`。如果模型调用结束后没有可解析的 `structured_response`，运行时会记录：

```json
{
  "_subagent_error": "structured_response_missing"
}
```

常见诊断信号：

- `finish_reason` 为 `tool_calls`，但消息中没有可执行的工具调用；
- `content` 为空；
- 模型产生了 completion token，但框架没有得到结构化对象；
- 随后的 Pydantic 错误显示所有顶层字段缺失。

这通常位于模型兼容接口、工具调用格式或 LangChain 结构化响应解析边界。当前日志记录解析后的 LangChain 对象，没有保存供应商的原始 HTTP 响应，因此仅凭现有日志不一定能把责任进一步定位到模型服务或适配层。

第一次无效结果会被释放并允许重新委派一次。第二次仍无效时，WorkflowGuard 要求调用 `record_research_issue`，项目保持当前阶段，以免把执行故障当作证据不足。

## 5. PDF 获取失败

`pdf.unavailable`、HTTP 403 或开放全文地址不可用不一定会终止论文精读：

- 摘要非空时，Reader 可以生成 `section="abstract"`、`page=null` 的摘要级 Evidence；
- 全文和摘要均为空时，该论文的 `findings` 为空；
- 只有全部入选论文都无法形成 Evidence 时，`EXTRACTED` 门禁才会拒绝继续综合。

因此，应先检查 `PaperCard` 数量和 `findings` 总数，再判断 PDF 错误是否为最终根因。

## 6. 人工检索审核限制

- `action` 只接受 `refine`、`accept` 和 `stop`。
- 每轮默认最多提交 3 条新检索词，默认最多补充 3 轮；重复查询不消耗轮次。
- `refine`、`accept` 和人工 `stop` 在后续运行开始前可以撤销；撤销会追加补偿事件和上一版 `CandidateSetSnapshot`，不会删除历史产物。
- 多个测试人员同时修改同一项目可能形成并发反馈；前端按钮锁只能降低单浏览器重复提交风险。

## 7. 当前恢复边界

- `SEARCH_REVIEW_PENDING`：继续提交人工反馈。
- `SCREENED`：可以调用 `/continue`，从逐篇精读开始。
- `EXTRACTED`、`SYNTHESIZED`、`REVIEW_PENDING`：可以调用 `/continue`，从已保存产物后的下一个安全步骤恢复。
- `REVIEWED + PASS`：可以调用 `/continue`，进入提纲和正文写作。
- `REVIEWED + REVISE`：首次修订会返回 `EXTRACTED` 并复审一次；连续两次 `REVISE` 会保存 `RuntimeIssue` 并等待人工处理。
- `OUTLINED`：提纲已保存，可能正在逐节生成 `SectionDraft` 或等待 `chief-editor`。
- `NARRATED`：旧版本遗留状态；完整 `NarrativeReview` 已保存，可以直接完成。
- `COMPLETED`：完整 NarrativeReview 已生成时为终态；旧版缺产物的错误完成可恢复。
- `INCONCLUSIVE`：真实证据不足仍为终态；结构化输出、模型超时等执行故障可从最近安全阶段恢复。
当前主流程在 `chief-editor` 成功提交完整 `NarrativeReview` 后直接进入 `COMPLETED`。

如果项目在综合、审查或写作阶段因格式、校验或进程中断故障停住，已保存的 `PaperCard`、Evidence 和后续产物仍保留在 SQLite。当前 `/continue` 会从 `EXTRACTED`、`SYNTHESIZED`、`REVIEW_PENDING`、`REVIEWED`、`OUTLINED`、旧版 `NARRATED` 或缺少完整综述的错误 `COMPLETED` 项目中受控恢复，并跳过已经保存的工作。

## 8. 2026-07-15 综合阶段故障样例

一次 3DVG 研究运行完成了人工审核和 4 篇论文精读，保存 4 份 `PaperCard` 与 27 条 Evidence，并成功进入 `EXTRACTED`。随后发生：

1. 第一份 `SynthesisReport` 结构完整，但假设中的 `3D/3DVG/FFL-3DOG` 被数值校验器识别为无证据数字 `3`，提交被拒绝。
2. 第二次 Synthesizer 响应显示 `finish_reason=tool_calls`，却没有形成可解析结构化对象，记录为 `structured_response_missing`。
3. 连续两次无效结果会保存 `RuntimeIssue` 并保持在当前安全阶段，等待后续恢复或人工处理。

该案例说明：首要故障来自数值校验误判，PDF 403 和 Evidence 数量均未阻断 `EXTRACTED`；执行故障应按 `RuntimeIssue` 排查，避免误判为证据不足。
