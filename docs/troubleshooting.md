# 故障诊断与当前限制

本文记录当前版本的排错顺序、常见失败类型和已确认限制。运行报告中的自然语言总结只用于辅助阅读；项目阶段、Artifact 和状态事件以 SQLite 及 `outputs/<project-id>/snapshot.json` 为准。

## 1. 推荐诊断顺序

1. 打开 `.research-agent/runs/<run-id>/summary.json`，确认 `status`、`project_stage`、`review_verdict` 和 `error`。
2. 在 `events.jsonl` 中找到最后一个成功事件和第一个 `artifact.commit_failed`、`tool.error` 或 `run.inconclusive`。
3. 需要核对模型响应时查看 `messages.jsonl`，重点比较 `finish_reason`、`content`、工具调用和 token 统计。
4. 打开 `.research-agent/outputs/<project-id>/snapshot.json`，统计已经正式保存的 `PaperCard`、Evidence、`SynthesisReport` 和 `ReviewResult`。
5. 根据首个失败事件确定故障层级，避免只依据最终报告中的概括性描述判断。

## 2. `INCONCLUSIVE` 的含义

当前状态机使用 `INCONCLUSIVE` 表示流程受控结束。触发原因包括：

- 检索后没有候选论文；
- 入选论文无法提供可追踪 Evidence；
- 用户在人工检索审核阶段主动停止；
- 同一子 Agent 连续两次生成无法提交的结构化结果；
- 其他无法安全继续、同时又不应生成科研结论的情况。

因此，`INCONCLUSIVE` 不能直接解释为“论文数量不足”或“证据全部无效”。必须读取最新 `InsufficientEvidence.reason` 和前序 `artifact.commit_failed` 事件。该状态在当前版本中是终态，`POST /api/projects/{project_id}/continue` 只接受 `SCREENED` 项目。

最终报告偶尔会使用“项目已完成”描述本轮程序调用已经结束。正式科研完成仍要求 `status=completed`、`project_stage=COMPLETED` 且 `review_verdict=PASS`；`error=null` 也只表示没有未处理的顶层异常。

## 3. 已确认问题：技术术语中的数字被误判

`ResearchService` 会检查研究空白的 `proposed_hypothesis`，防止生成 Evidence 原文没有支持的精确数字。当前实现使用：

```python
re.findall(r"\d+(?:\.\d+)?%?", proposed_hypothesis)
```

该表达式也会从以下技术术语中提取数字：

- `2D` → `2`
- `3D`、`3DVG` → `3`
- `FFL-3DOG` → `3`

如果对应 Evidence 引文没有出现相同字符，提交会返回：

```text
Synthesis hypothesis contains unsupported numeric claims: 3, 3, 3
```

这类错误可能是校验误判。诊断时应检查被拒绝假设中的数字是否属于百分比、指标、样本量等真正的定量声明，还是 `2D/3D` 等术语的一部分。

当前比较方式还使用 `token in quote_text` 字符串包含判断。例如引文中的 `2023` 可能让单独的 `3` 被视为已支持。后续实现应对假设和 Evidence 分别提取规范化数值，忽略维度缩写，并执行精确集合比较。

## 4. `structured_response_missing`

子 Agent 通过结构化响应 schema 返回 `SearchReport`、`PaperCard`、`SynthesisReport` 或 `ReviewResult`。如果模型调用结束后没有可解析的 `structured_response`，运行时会记录：

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

第一次无效结果会被释放并允许重新委派一次。第二次仍无效时，WorkflowGuard 要求调用 `finish_inconclusive`，项目进入终态。

## 5. PDF 获取失败

`pdf.unavailable`、HTTP 403 或开放全文地址不可用不一定会终止论文精读：

- 摘要非空时，Reader 可以生成 `section="abstract"`、`page=null` 的摘要级 Evidence；
- 全文和摘要均为空时，该论文的 `findings` 为空；
- 只有全部入选论文都无法形成 Evidence 时，`EXTRACTED` 门禁才会拒绝继续综合。

因此，应先检查 `PaperCard` 数量和 `findings` 总数，再判断 PDF 错误是否为最终根因。

## 6. 人工检索审核限制

- `action` 只接受 `refine`、`accept` 和 `stop`。
- 每轮默认最多提交 3 条新检索词，默认最多补充 3 轮；重复查询不消耗轮次。
- 排除操作提交后，论文会从新版 `CandidateSetSnapshot` 移除，当前没有一键恢复接口。
- `accept` 和 `stop` 没有撤销接口；可视化测试台会在提交前要求确认。
- 多个测试人员同时修改同一项目可能形成并发反馈；前端按钮锁只能降低单浏览器重复提交风险。

## 7. 当前恢复边界

- `SEARCH_REVIEW_PENDING`：继续提交人工反馈。
- `SCREENED`：可以调用 `/continue`，从逐篇精读开始。
- `REVIEWED + REVISE`：状态机允许返回 `EXTRACTED` 修订。
- `COMPLETED`、`INCONCLUSIVE`：终态，当前 API 不支持重新打开。

如果项目在综合阶段因格式或校验故障进入 `INCONCLUSIVE`，已保存的 `PaperCard` 和 Evidence 仍保留在 SQLite，从数据和设计上可以复用。当前 `/continue` 只接受 `SCREENED`，系统也没有从 `EXTRACTED` 重新执行综合或重新打开 `INCONCLUSIVE` 的恢复用例，因此现有产品路径无法自动复用这些产物；直接调用 `/continue` 会返回阶段冲突。

## 8. 2026-07-15 综合阶段故障样例

一次 3DVG 研究运行完成了人工审核和 4 篇论文精读，保存 4 份 `PaperCard` 与 27 条 Evidence，并成功进入 `EXTRACTED`。随后发生：

1. 第一份 `SynthesisReport` 结构完整，但假设中的 `3D/3DVG/FFL-3DOG` 被数值校验器识别为无证据数字 `3`，提交被拒绝。
2. 第二次 Synthesizer 响应显示 `finish_reason=tool_calls`，却没有形成可解析结构化对象，记录为 `structured_response_missing`。
3. 连续两次无效结果触发受控终止，项目执行 `EXTRACTED → INCONCLUSIVE`。

该案例说明：最终 `INCONCLUSIVE` 是状态机的安全收口结果，首要故障来自数值校验误判，PDF 403 和 Evidence 数量均未阻断 `EXTRACTED`。
