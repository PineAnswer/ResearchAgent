# 故障诊断、恢复路径与当前限制

本文面向当前代码版本，说明研究流程出现停滞、失败、降级或数据不一致时应如何定位。重点是区分领域状态、后台任务状态和技术运行状态，并在不破坏已提交成果的前提下恢复。

项目的基本恢复原则是：已成功提交到 SQLite 的 Artifact 和状态事件继续有效；模型回复、前端活动文字和导出镜像只作为解释材料。

---

## 1. 先确认正在观察哪一层

同一次研究可能出现三套状态：

| 状态层 | 示例 | 回答的问题 |
| --- | --- | --- |
| 项目阶段 | `SCREENED`、`EXTRACTED`、`COMPLETED` | 领域工作推进到哪里 |
| Web 任务状态 | `running`、`awaiting_input`、`failed` | 本次后台协程怎样结束 |
| 技术运行状态 | `completed`、`incomplete`、`error` | 一次 Supervisor 调用怎样收尾 |

这三套状态可以合理地同时出现不同值。例如：

- 项目已经保存多个 `PaperCard` 并停在 `SCREENED`；
- 后台任务因后续模型请求失败而显示 `failed`；
- 技术日志显示 `error`。

此时论文卡仍是已提交成果，恢复应从 `SCREENED` 继续读取尚未完成的论文。

数据可信度建议按以下顺序判断：

1. SQLite 中的项目、Artifact 和状态事件；
2. SQLite 中的 `ConversationRun` 与会话消息；
3. `.research-agent/runs/<technical-run-id>/` 技术日志；
4. `.research-agent/outputs/<project-id>/` 可读镜像；
5. 最终报告和前端活动文字。

outputs 镜像由服务层操作触发刷新，会话任务、消息、笔记和候选选择的直接 Repository 更新可能暂时没有同步到镜像。

---

## 2. 通用诊断流程

### 第一步：读取 API 项目快照

优先查看 GET `/api/projects/{project_id}`：

- `project.stage`；
- 最后几个 `events`；
- Artifact 的类型、ID和创建时间；
- `active_run` 与最新一条 `runs`；
- `runtime_events` 是否仍在更新。

项目快照里的大型候选产物会被 API 压缩；需要候选明细时使用 GET `/api/projects/{project_id}/search-review`。

### 第二步：确认已提交产物

按照当前阶段检查最低前置产物：

| 阶段 | 应存在的关键产物 |
| --- | --- |
| `SEARCHED` | `SearchReport` |
| `SEARCH_REVIEW_PENDING` | `CandidateSetSnapshot` |
| `SCREENED` | `ScreeningDecision` |
| `EXTRACTED` | 每篇入选论文对应一份 `PaperCard` |
| `SYNTHESIZED` | `SynthesisReport` |
| `REVIEWED` | `ReviewResult` |
| `OUTLINED` | `ReviewOutline` |
| `COMPLETED` | 至少含一个章节的 `NarrativeReview` |
| `INCONCLUSIVE` | `InsufficientEvidence` |

`REVIEW_PENDING` 是从 `SYNTHESIZED` 到审查的门禁阶段，所需综合产物已经在前一阶段提交。

### 第三步：定位技术日志

Web 会话任务 ID与技术日志目录 ID目前没有直接互相引用。用 `thread_id`、`project_id`、主题和时间戳找到对应目录，然后按以下顺序阅读：

1. `run.json`：确认入口、主题、项目 ID、阶段和技术状态；
2. `summary.json`：确认本次调用的收尾结论；
3. `events.jsonl`：寻找最后一个成功事件和第一个失败事件；
4. `messages.jsonl`：核对模型响应、工具调用和结构化解析诊断；
5. `final-result.json`：还原图最终返回的状态。

重点事件包括：

- `llm.error`
- `llm.invalid_tool_calls`
- `llm.tool_call_parse_gap`
- `search.failed`
- `search.rate_limited`
- `pdf.unavailable`
- `artifact.commit_failed`
- `stage.rejected`
- `tool.error`
- `run.finished`

日志只有选择性脱敏，分享前仍需人工检查用户输入、论文内容、工具参数和凭据。详细日志结构见 [runtime-observability.md](runtime-observability.md)。

### 第四步：按首个失败点确定恢复动作

后续错误经常由首个错误引发。例如 `subagent_stage_not_ready` 可能只是前面的 Artifact 提交失败导致阶段没有推进。应先解决时间线上最早的失败点。

不要直接编辑 SQLite 阶段字段或删除历史 Artifact。正常的服务方法会同时执行 schema 校验、阶段校验、事务提交、状态事件追加和导出刷新。

---

## 3. 服务启动和模型配置

### 3.1 `/health` 显示 `degraded`

GET `/health` 返回：

- `status`
- `model`
- `provider`
- `agent_available`
- `initialization_error`
- `user_mode`
- 场地排名数据统计

当 Agent 图构建失败且 `RESEARCH_AGENT_ENABLE_FALLBACK=true` 时，API 仍能启动，`status` 为 `degraded`，`agent_available` 为 `false`。文献库的部分提取式功能仍可工作。

当前 Web 后台研究调用 `astart_project()` / `acontinue_project()`，图不可用时任务会进入 `failed`。CLI `invoke_with_fallback()` 和两个兼容研究 API拥有离线降级路径。离线降级只保存 `RuntimeFallback`，不会生成科研结论。

### 3.2 Provider 与模型冲突

Provider 解析顺序：

1. 显式 `RESEARCH_AGENT_PROVIDER`；
2. `RESEARCH_AGENT_MODEL` 中的 `openai:`、`anthropic:` 或 `bedrock:` 前缀；
3. 无前缀且模型名以 `claude` 开头时选择 Anthropic；
4. 其他无前缀模型默认选择 OpenAI。

典型启动错误：

| 错误 | 原因 | 修复 |
| --- | --- | --- |
| `Unsupported RESEARCH_AGENT_PROVIDER` | Provider 不在 `auto/openai/anthropic/bedrock` | 修正环境变量 |
| Provider prefix conflict | 显式 Provider 与模型前缀冲突 | 让两处使用同一 Provider，或把 Provider 设为 `auto` |
| `OPENAI_API_KEY is required` | OpenAI 路径缺少密钥 | 设置 `OPENAI_API_KEY` |
| `ANTHROPIC_API_KEY is required` | Anthropic 路径缺少密钥 | 设置 `ANTHROPIC_API_KEY` |
| AWS credentials CSV is empty/incomplete | Bedrock CSV为空或缺少 access/secret key | 修复 CSV，或使用标准 AWS Profile/凭据链 |

`RESEARCH_AGENT_BASE_URL` 按 OpenAI SDK 约定通常包含 `/v1`。原生 Anthropic 路径会从该值推导根地址并去掉结尾 `/v1`；可用 `RESEARCH_AGENT_ANTHROPIC_BASE_URL` 显式覆盖。

### 3.3 数据目录无法初始化

`Settings.from_env()` 会创建：

- `RESEARCH_AGENT_DATA_DIR`；
- `filesystem/`；
- SQLite 数据库父目录；
- 后续运行中的 `runs/` 和 `outputs/`。

出现权限错误时，先确认数据目录可写、磁盘未满、路径没有被只读挂载。环境变量中的整数或浮点数格式非法会在启动阶段直接抛出 `ValueError`。

---

## 4. Web 后台任务故障

### 4.1 长时间停在 `queued` 或 `running`

检查顺序：

1. `ConversationRun.updated_at` 是否继续变化；
2. `runtime_events` 最后一条事件时间；
3. 项目状态事件或 Artifact 是否增长；
4. API 进程是否重启；
5. 技术日志最后一条回调；
6. Provider 请求是否仍处于网络超时或限流退避。

后台执行协程只存在于 API 进程内。API 关闭时，管理器会尽力把本进程任务写成 `interrupted`；进程被强制终止时可能来不及收尾，数据库记录可能暂时保留旧的 `queued/running` 状态。当前没有独立 worker 或启动时自动接管机制。

实时事件同样只保存在进程内，每个任务最多 120 条、最多 50 个任务列表。事件消失不代表领域成果被删除。

### 4.2 HTTP 409

研究相关的 409 通常表示业务前置条件冲突：

| `detail` 或错误片段 | 含义 |
| --- | --- |
| `conversation_already_running` | 同一会话已有 `queued/running` 任务 |
| `Cannot accept an empty candidate set` | 候选集为空，需 refine、手动加入或 stop |
| `System deep-reading capacity is ...` | 最终候选数超过系统精读上限 |
| `Search review round limit reached` | 人工补充查询轮次用尽 |
| `Cannot undo while a continuation run is active` | 后续研究已经启动，当前不能撤销审核 |
| `Project continuation requires ...` | 当前阶段不支持 `/continue` |
| `Project is already complete` | 已存在合法 `NarrativeReview`，项目已终态完成 |

发生 `conversation_already_running` 时不要循环重发。轮询原任务，待其进入终态后再启动下一次继续。

### 4.3 HTTP 404

404 可能表示资源确实不存在，也可能表示当前用户作用域看不到该资源。默认 `local_shared` 模式下本地浏览器共享主用户历史；`RESEARCH_AGENT_MULTI_USER_MODE=true` 时，不同 session cookie 会隔离项目、会话和任务。

排查时确认：

- 请求是否携带原浏览器的 `research_agent_session` cookie；
- API 是否切换过 `RESEARCH_AGENT_DATA_DIR`；
- 是否从另一个用户会话复制了项目 ID；
- 对象是否已删除或归档。

### 4.4 HTTP 422 与 500

- 422 通常由 Pydantic 请求 schema 拒绝，例如非法 action、缺少必填字段、越界页码；
- 500 表示路由没有将异常转成业务状态，查看服务终端和对应技术日志；
- Web 后台研究中的 Agent 异常通常被管理器写成任务 `failed`，页面轮询本身仍可返回 200。

---

## 5. 检索与人工审核故障

### 5.1 单个学术源失败

OpenAlex、Crossref、Semantic Scholar 和 arXiv 返回可恢复错误 JSON，常见 `error_code`：

- `rate_limited`
- `http_error`
- `network_error`
- `timeout`
- `invalid_response`
- `source_exception`

错误载荷还可能包含 `attempts`、`retryable`、HTTP 状态、`Retry-After` 和限流头。429 与 5xx 按配置退避重试；等待时间超过 `RESEARCH_AGENT_SEARCH_MAX_RETRY_WAIT_SECONDS` 时停止自动等待。超时最多进行一次额外重试，其他网络错误使用通用重试次数。

`search_multi_source` 让四个来源并行工作，并在每个来源内串行执行查询。单源失败会写入 `source_status`，其余来源继续返回候选。因此单个 403、429 或超时通常不会让整轮检索失败。

### 5.2 本地库为空后没有外部检索

若 `literature-scout` 只调用 `search_library` 且结果为空，提交空 `SearchReport` 会返回：

```text
error_code: external_search_required
```

系统允许纠正性地重新委派 Scout 一次，要求首次工具调用为 `search_multi_source`。第二次仍未执行外部检索时应保存 `RuntimeIssue` 并结束本轮。

### 5.3 初始候选为空

空候选不会自动进入 `INCONCLUSIVE`：

- 正常空结果会保存 `SearchReport` 与 `CandidateSetSnapshot`，进入人工审核；
- 全部候选被硬过滤时，项目可能暂留 `SEARCHED`，快照含 `blocked_reason` 和 `filtered_candidates`；
- 用户可以从过滤列表手动加入、提供 DOI、补充检索词，或选择 stop。

Accept 空候选会被拒绝。用户 stop 会保存 `InsufficientEvidence` 并进入 `INCONCLUSIVE`。

### 5.4 人工审核限制

`SearchFeedback.action` 接受：

- `refine`
- `accept`
- `stop`
- `undo`

补充查询只允许随 `refine` 提交。默认每轮最多 3 条，默认最多 3 轮，实际值由以下配置控制：

- `RESEARCH_AGENT_MAX_SUGGESTED_QUERIES_PER_ROUND`
- `RESEARCH_AGENT_MAX_SEARCH_REVIEW_ROUNDS`

查询会经过大小写、词序、复数后缀和重复词归一化；重复查询不产生新搜索轮次。请求中的 `min_papers`、`max_papers` 和 `max_search_rounds` 不能突破服务端真实限制，当前 `_resolve_limits()` 固定使用最少 1 篇、`max_deep_read_papers` 和服务端轮次上限。

### 5.5 撤销边界与并发编辑

最近一次 `refine`、`accept` 或 `stop` 可以撤销，但必须满足：

- 当前阶段仍与该动作的结果阶段一致；
- 至少存在前一版 `CandidateSetSnapshot`；
- 没有活动中的继续任务。

撤销会追加 `SearchFeedback(action="undo")` 和补偿快照；历史 Artifact 保留。`accept` 或 `stop` 撤销后，Repository 以补偿事件将项目重新打开到 `SEARCH_REVIEW_PENDING`。

GET 审核结果会返回 `snapshot_version`，当前选择和反馈写接口没有把它作为乐观锁前置条件。多个浏览器同时修改同一项目时，后提交者可能基于旧视图计算新快照。前端按钮锁只覆盖单页面重复点击。

---

## 6. 子 Agent 结构化输出故障

### 6.1 结构化结果如何被恢复

子 Agent 首先通过绑定 schema 生成 `structured_response`。如果该字段缺失，运行时还会尝试：

1. 从预期 schema 工具调用的 `args` 读取字典或 JSON；
2. 从最后几条消息的纯 JSON 正文读取；
3. 检查结果是否含该 Agent 所需的顶层字段。

所有回收路径都失败后，记录：

```json
{
  "_subagent_error": "structured_response_missing",
  "_diagnostics": {
    "parse_status": "tool_call_finish_without_parsed_calls"
  }
}
```

可能的 `parse_status`：

| 值 | 解释 |
| --- | --- |
| `invalid_tool_calls` | 工具参数无法解析 |
| `tool_call_finish_without_parsed_calls` | Provider 声明工具结束，框架未得到调用 |
| `schema_tool_call_not_promoted` | 有工具调用，但未提升为结构化响应 |
| `unparsed_content` | 有正文，JSON 回收仍失败 |
| `empty_model_response` | 没有有效正文或调用 |

子 Agent 结果中的 `_diagnostics.raw_provider_response_captured` 固定为 false；对应的 `messages.jsonl` 中，OpenAI 和原生 Anthropic 回调可另外保存经脱敏的原始 Provider 响应。Bedrock Converse 当前没有同级捕获。

### 6.2 提交失败与重试上限

`commit_subagent_result` 会执行 Pydantic、领域前置条件和阶段迁移校验。失败返回：

```text
error_code: subagent_commit_rejected
```

第一次失败会消费并释放旧结果，允许根据错误信息重新委派一次。第二次仍失败时：

- Scout、Synthesizer、Reviewer、Outliner、Writer、Chief Editor：调用 `record_research_issue` 保存 `RuntimeIssue`，保持当前阶段；
- Paper Reader：停止处理当前论文，继续下一篇入选论文；每篇论文独立计算两次上限。

成功提交后，该 Agent 的拒绝计数会清除。Supervisor 不应手工拼接子 Agent JSON 绕过 schema。

### 6.3 Workflow Guard 常见错误

| `error_code` | 触发条件 | 正确动作 |
| --- | --- | --- |
| `project_must_be_created_first` | 创建项目前调用项目工具或委派 | 先创建或正确绑定项目 |
| `active_project_unavailable` | thread 没有绑定项目 | 检查 thread/project 绑定 |
| `subagent_stage_not_ready` | Agent 与当前阶段不匹配 | 先完成当前阶段所需提交 |
| `subagent_result_must_be_committed` | 上一份结果尚未提交 | 先调用 `commit_subagent_result` |
| `subagent_retry_limit_reached` | 同范围已有两次无效结果 | 保存 `RuntimeIssue` 或跳过当前论文 |
| `literature_scout_limit_reached` | 超出 Scout 委派边界 | 使用首次结果；仅一次纠错重派例外 |
| `paper_reader_not_in_screening_decision` | Reader 读取未入选论文 | 使用最新 `ScreeningDecision` 的 ID |
| `human_search_review_required` | Agent 尝试代替用户确认审核 | 通过 search-feedback API操作 |

这些错误由中间件在工具执行前返回，通常没有 Python 堆栈。

---

## 7. PDF 与 PaperCard 故障

### 7.1 全文获取结果

`fetch_paper_text` 依次组合可信 arXiv 地址、OpenAlex 开放位置、DOI 关联的 Semantic Scholar 预印本等候选 URL。它不会把普通 DOI 落地页当作 PDF。

常见结构化错误：

| `error_code` | 含义 |
| --- | --- |
| `workspace_unavailable` | 没有可用论文缓存目录 |
| `open_full_text_unavailable` | 所有开放全文候选都失败 |
| `path_outside_workspace` | `extract_pdf_text` 路径逃出允许根目录 |
| `pdf_not_found` | 指定缓存文件不存在 |
| `pdf_unreadable` | PDF 下载存在，但解析失败 |
| `duplicate_paper_fetch` | 同一论文重复使用相同参数 |
| `paper_fetch_limit_reached` | 同一论文已达到全文尝试上限 |

每篇论文默认最多两组不同的获取请求，由 `RESEARCH_AGENT_MAX_PAPER_FETCHES_PER_PAPER` 控制。缓存命中会直接复用本地 PDF。

### 7.2 没有全文时怎样继续

- 有摘要：Reader 可以保存 `source_scope="abstract"`、`page=null` 的摘要级 Evidence；
- 全文和摘要均为空：保存 findings 为空的 `PaperCard`，并在 limitations 说明范围；
- 所有论文 findings 都为空：仍允许进入 `EXTRACTED`，随后保存四个结论列表均为空的 `SynthesisReport`。

进入 `EXTRACTED` 的硬前置条件是最新 `ScreeningDecision` 至少包含一篇论文，并且每个入选 ID都有一份 `PaperCard`。Evidence 数量不是这一步的硬门禁。

### 7.3 PaperCard 身份校验

保存时会检查：

- 当前阶段必须为 `SCREENED`；
- 存在 `ScreeningDecision`；
- `paper_id` 属于最新入选集合；
- 卡内 `evidence_id` 不重复；
- 每条 Evidence 的 `paper_id` 与卡片一致。

简单 Evidence ID会在 schema 归一化过程中补成以论文 ID为前缀的稳定形式。跨卡片出现相同 `evidence_id` 时，后续建立证据索引会拒绝。

---

## 8. 综合、证据审查与数值声明

### 8.1 无依据数值的当前处理

系统从 `proposed_hypothesis` 和该 gap 引用的 Evidence quote 中提取规范化数值。处理包括：

- 忽略 `2D`、`3DVG`、`FFL-3DOG`、`ResNet-50`、`GPT-4` 等技术标识；
- 忽略年份对短数字的错误支持；
- 规范化千位分隔符、空格、百分号和 `x/×/倍`；
- 使用精确 token 集合比较。

发现 quote 未支持的数值时，系统会把整条 `proposed_hypothesis` 替换为保守文本：具体效应大小必须由新增证据确定。`SynthesisReport` 仍可提交并进入 `SYNTHESIZED`。

日志中的 `Synthesis hypothesis contains unsupported numeric claims` 是降级警告，可在已保存 Artifact 中确认假设已经被替换。

### 8.2 综合证据警告

以下情况当前记录 warning，通常不阻止保存：

- Synthesis 引用未知 Evidence ID；
- gap 的 `supporting_paper_ids` 与所引 Evidence 论文不一致；
- 没有任何 PaperCard findings 时仍输出非空结论列表。

这些警告表示证据质量风险。正式判断应检查已保存 `SynthesisReport` 与 PaperCard 证据索引。

### 8.3 ReviewResult 硬门禁

审查提交会拒绝：

- `verified_evidence_ids` 含未知 ID；
- verdict 为 `PASS`，但没有任何 verified Evidence。

因此空综合可以进入审查，但无法在没有可验证 Evidence 的情况下获得合法 PASS。

### 8.4 连续 REVISE

- 第一次 `REVISE`：项目回到 `EXTRACTED`，复用已有 PaperCard 修订综合，再独立审查一次；
- 第二次 `REVISE`：保存 `RuntimeIssue(reason="review_revision_limit_reached")`，项目留在 `REVIEWED`，等待人工处理；
- `/continue` 遇到两次 REVISE 会返回 409，避免无限自动循环。

修订流程不会重新检索或重新读取论文。

---

## 9. 提纲、章节和成稿故障

### 9.1 ReviewOutline

`ReviewOutline` 至少需要一个非空 `section_id`，并要求所有 section ID唯一。合法 PASS `ReviewResult` 是进入叙事写作的前置条件。

### 9.2 SectionDraft

每个草稿：

- 只能在 `OUTLINED` 保存；
- `section_id` 必须存在于最新提纲；
- 同一提纲之后不能重复保存相同 section ID。

继续 `OUTLINED` 项目时，Supervisor 会从持久化上下文读取已经完成的 section ID，只生成缺失章节。

### 9.3 Chief Editor 的确定性兜底

当 chief-editor 返回 `_subagent_error`，或 `NarrativeReview` 的 schema/前置校验失败时，提交工具会调用 `assemble_narrative_review()`。该方法使用：

- 最新 `ReviewOutline`；
- 提纲之后保存的全部 `SectionDraft`；
- PaperCard 和候选元数据；
- 草稿中的 Evidence 引用；

确定性组装 `NarrativeReview` 并以 actor `chief-editor-fallback` 进入 `COMPLETED`。

兜底仍要求每个提纲章节都有非空草稿。缺少章节时会保持 `OUTLINED`，先恢复生成缺失 `SectionDraft`。

### 9.4 COMPLETED 门禁

合法完成要求存在至少一个章节的 `NarrativeReview`。历史数据库中若出现缺失该产物的 `COMPLETED`，`prepare_continuation()` 会识别为错误完成，并根据已保存产物重新打开到安全阶段。

---

## 10. `RuntimeIssue`、`INCONCLUSIVE` 与离线降级

### 10.1 `RuntimeIssue`

用于记录可恢复的执行问题，例如：

- 子 Agent 连续生成无效结果；
- 两次审查均要求修订；
- 格式、schema 或阶段提交故障；
- 模型超时后需要从已保存进度恢复。

保存 `RuntimeIssue` 不迁移项目阶段。下一次继续运行从当前安全阶段和已提交 Artifact 开始。

### 10.2 `INCONCLUSIVE`

用于受控的业务终止：

- 用户在检索审核中选择 stop；
- 证据范围确实不足以回答问题；
- 工作流明确要求停止生成科研结论。

真实证据不足是终态，`/continue` 会拒绝恢复。

当前代码保留兼容恢复逻辑：历史版本若把结构化响应、subagent、timeout、missing field、chief-editor 等执行故障错误记录为 `InsufficientEvidence` 并进入 `INCONCLUSIVE`，`prepare_continuation()` 会从 Artifact 推导安全阶段并追加 `workflow-recovery` 事件。该识别依赖最新 `InsufficientEvidence.reason/recommendation` 中的操作故障关键词。

### 10.3 离线降级

只有 Provider 可用性类别进入 `OfflineFallback`，包括认证、连接、超时、限流、Provider 内部错误、Bedrock/Boto 和底层网络错误。阶段非法、schema 校验、Artifact 冲突等业务错误不会触发降级。

`RuntimeFallback` 只记录失败原因和说明，项目保持原阶段。API 兼容入口可能先产生 `error` 技术日志，再由路由返回 fallback；CLI 在同一日志生命周期中显示 `fallback`。

---

## 11. 阶段恢复矩阵

| 当前阶段 | 推荐入口 | 恢复行为 |
| --- | --- | --- |
| `CREATED` | 启动初始会话任务 | 执行 Scout；`/continue` 不接受 |
| `SEARCHED` | search-review/feedback | 处理被过滤为空的人工恢复场景 |
| `SEARCH_REVIEW_PENDING` | search-feedback | refine、选择、accept、stop 或 undo |
| `SCREENED` | 会话 resume 或 `/continue` | 读取最新 `ScreeningDecision`，跳过已保存 PaperCard |
| `EXTRACTED` | resume/continue | 委派 Synthesizer |
| `SYNTHESIZED` | resume/continue | 先推进 `REVIEW_PENDING` |
| `REVIEW_PENDING` | resume/continue | 委派 Reviewer |
| `REVIEWED + PASS` | resume/continue | 生成提纲与正文 |
| `REVIEWED + REVISE` | resume/continue | 首次回到 `EXTRACTED`；第二次要求人工处理 |
| `OUTLINED` | resume/continue | 只补缺失章节，再运行 Chief Editor |
| `NARRATED` | resume/continue | 兼容旧阶段，验证既有成稿后完成 |
| 合法 `COMPLETED` | 无 | 已完成终态 |
| 缺成稿的旧 `COMPLETED` | resume/continue | 推导安全阶段并重新打开 |
| 真实证据不足的 `INCONCLUSIVE` | 无 | 终态；撤销 search stop 是审核期特例 |
| 历史操作故障型 `INCONCLUSIVE` | resume/continue | 识别关键词后推导安全阶段恢复 |

对于会话项目，优先使用后台 resume 路径，让 `ConversationRun` 和页面状态完整记录本轮恢复。直接 POST `/api/projects/{project_id}/continue` 是同步兼容入口，不创建 Web 后台任务记录。

---

## 12. 当前实现限制

- Web 后台协程和实时进度没有外部任务队列，API 重启后无法自动接管；
- 会话任务 ID与技术日志 ID缺少直接关联字段；
- 技术日志没有轮转、容量限制和自动清理；
- 日志脱敏覆盖不完整，工具输入输出可能含敏感数据；
- outputs 快照中的会话任务和消息可能落后于 SQLite；
- 多浏览器候选审核没有基于 `snapshot_version` 的乐观并发控制；
- Synthesis 的若干证据一致性检查只记录 warning；
- `INCONCLUSIVE` 历史恢复通过文本关键词识别，适合兼容旧数据，精度受原因文本影响；
- Web 首页只展示 portfolio 范围的一部分实时事件，且活动列表最多 14 条；
- 前端继续运行的耗时基线取项目创建时间，可能显示项目累计年龄。

---

## 13. 最小排障记录模板

报告问题时建议提供以下经过脱敏的信息：

```text
project_id:
conversation_id:
conversation_run_id:
technical_run_id:
project_stage:
conversation_run_status:
technical_run_status:
last_state_event:
last_successful_runtime_event:
first_failure_event:
latest_artifact_id_and_kind:
RuntimeIssue_or_InsufficientEvidence_reason:
provider_and_model:
health_status:
reproduction_entrypoint:
```

不要直接附上完整 `messages.jsonl`、密钥、cookie、Authorization header、论文全文或未脱敏的用户数据。

---

## 14. 关键实现和测试位置

| 范围 | 实现文件 |
| --- | --- |
| Provider、环境变量和数据目录 | `src/research_agent/infrastructure/config.py` |
| Supervisor 入口、模型构建和降级判定 | `src/research_agent/agents/supervisor.py` |
| 子 Agent 结果回收与 PDF 请求上限 | `src/research_agent/agents/runtime_state.py` |
| 委派与阶段守卫 | `src/research_agent/agents/workflow_guard.py` |
| Artifact 校验和恢复推导 | `src/research_agent/application/research_service.py` |
| 人工检索审核与撤销 | `src/research_agent/application/search_review.py` |
| Artifact 提交和 RuntimeIssue | `src/research_agent/tools/project_tools.py` |
| 学术检索、重试和 PDF 工具 | `src/research_agent/tools/literature_tools.py` |
| 阶段迁移规则 | `src/research_agent/domain/workflow.py` |
| Web 后台任务 | `src/research_agent/api/background_runs.py` |
| HTTP 状态和 API 入口 | `src/research_agent/api/app.py` |
| 技术日志 | `src/research_agent/infrastructure/run_logger.py` |

主要回归测试：

- `tests/test_service_and_fallback.py`
- `tests/test_search_review.py`
- `tests/test_tools.py`
- `tests/test_supervisor_policy.py`
- `tests/test_state_machine.py`
- `tests/test_observability.py`
- `tests/test_conversation_isolation.py`
- `tests/test_frontend_run_failure.py`

---

## 15. 小结

排障的核心是先确认已提交的领域事实，再定位本次执行的首个失败点。可恢复执行故障通过 `RuntimeIssue` 保留在安全阶段，真实证据不足通过 `InsufficientEvidence` 受控结束；Provider 可用性故障可以进入有限的离线降级。恢复过程复用 SQLite 中的 SearchReport、ScreeningDecision、PaperCard、Evidence、综合、审查、提纲和章节草稿，从而避免重复已经完成的研究工作。
