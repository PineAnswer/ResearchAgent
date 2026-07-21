# 运行进度、日志与导出产物

本项目把“当前界面看到的进度”“单次调用的技术记录”和“跨调用保留的科研事实”分开保存。这样既能定位模型或工具故障，也能判断项目真正推进到了哪个业务阶段。

## 1. 三类可观测信息

| 信息 | 位置 | 回答的问题 |
|---|---|---|
| CLI 实时进度 / API SSE | 控制台或 HTTP 响应流 | 现在执行到哪一步？ |
| 运行日志 | `.research-agent/runs/<run-id>/` | 这一次模型和工具具体做了什么？ |
| 科研产物镜像 | `.research-agent/outputs/<project-id>/` | 当前项目已经正式保存了哪些事实？ |

`.research-agent` 是默认数据目录；如果配置修改了 `data_dir`，上述 `runs/` 和 `outputs/` 会随之移动。

## 2. CLI 与 API 的实时进度

`research-agent run` 会显示模型开始分析、工具选择、检索结果、论文处理序号、全文获取、产物提交、阶段推进、失败和降级等事件。源码中的 `llm.thinking` 是“模型调用已开始”的进度标签，不表示系统暴露了模型的隐藏推理过程。

Windows 下 CLI 启动时会把控制台输出调整为 UTF-8，减少中文日志乱码。命令结束后还会显示权威项目状态、Agent 报告草稿和本次运行日志目录。

HTTP 流式接口使用 Server-Sent Events（SSE）：

- `event: update`：Deep Agents 图以 `stream_mode="updates"` 返回的增量更新。
- `event: awaiting_input`：候选集已保存，项目进入 `SEARCH_REVIEW_PENDING`，本轮执行暂停并等待用户反馈。
- `event: done`：图正常结束。
- `event: fallback`：发生允许降级的模型或网络可用性错误，并已生成降级结果。
- `event: error`：其他异常；事件数据中包含错误文本。

SSE 适合驱动前端进度展示；持久化运行日志适合事后复盘。两者的粒度和数据结构不要求完全一致。

启动 `research-agent serve` 后，根路径前端会直接消费这些 API 与项目快照。项目详情中的产物默认使用专用 HTML 渲染器展示，并提供 JSON/HTML 切换；未知产物类型会回退到通用结构化视图。该切换只影响浏览器展示，SQLite 和导出的 JSON 内容不会改变。

## 3. 单次运行日志

`ResearchSupervisor.invoke()`、`ainvoke()`、`astream()` 和 CLI 的降级包装都会创建 `ResearchRunLogger`。每次调用生成一个独立目录：

```text
.research-agent/runs/<run-id>/
├── run.json
├── events.jsonl
├── messages.jsonl
├── final-result.json       # 有 result 时生成
├── final-report.md         # 最终状态中存在 Agent 文本回复时生成
└── summary.json
```

`run-id` 由 UTC 时间戳和随机短标识组成，例如 `20260715T083012-a1b2c3d4`。

### 3.1 `run.json`：本次调用的索引

开始时写入：

- `run_id`：运行唯一标识。
- `thread_id`：LangGraph 短期状态使用的线程标识。
- `topic`、`research_question`：本次输入。
- `started_at`、`status`：开始时间和初始 `running` 状态。

结束时追加 `finished_at`、`project_id`、`project_stage`、`review_verdict` 和 `error`，并更新有效运行状态。

### 3.2 `events.jsonl`：面向人的进度事件

每行是一个独立 JSON 对象，字段为：

```json
{
  "timestamp": "UTC ISO-8601 时间",
  "type": "事件类型",
  "message": "便于显示的中文说明",
  "data": "该事件的附加结构化数据"
}
```

常见事件包括：

- 运行：`run.started`、`run.finished`、`run.fallback`、`run.inconclusive`。
- 模型：`llm.thinking`、`llm.tool_choice`、`llm.tool_choice_batch`、`llm.reply`、`llm.error`。
- 检索：`search.started`、`search.results`、`search.rate_limited`、`search.failed`。
- 论文：`paper.started`、`pdf.fetch_started`、`pdf.fetched`、`pdf.unavailable`、`pdf.extracted`、`paper.completed`。
- 产物：`artifact.committing`、`artifact.committed`、`artifact.commit_failed`、`screening.completed`。
- 状态：`stage.transition`、`stage.changed`、`stage.rejected`。
- 通用工具：`tool.started`、`tool.completed`、`tool.error`。

模型在一次回复中提出多个工具调用时，`llm.tool_choice_batch` 会说明当前串行中间件只执行第一项。这个事件用于区分“模型提出多个调用”与“系统已经并行执行多个调用”。

论文事件会尽量记录真实 `paper_id`、标题、序号和尝试次数。全文成功、不可用、本地 PDF 提取及单篇 `PaperCard` 完成分别记录，便于定位某篇论文卡在哪一步。

### 3.3 `messages.jsonl`：模型与工具交互记录

该文件按发生顺序保存回调捕获的交互：

- `llm.request`：模型标识、输入消息、`run_id` 和 `parent_run_id`。
- `llm.response`：模型响应对象及调用关系。
- `tool.request`：工具名、标准化输入参数及调用关系。
- `tool.response`：工具输出及调用关系。
- `llm.error`、`tool.error`：异常信息。

它用于还原调用链、检查模型选择了哪个工具以及工具实际收到了什么参数。日志内容可能包含研究问题、论文文本、引文和模型回复，部署时应按研究数据的敏感级别设置目录权限与保留周期。

### 3.4 `final-result.json`：图返回结果与权威项目状态

只要 `finish()` 收到结果对象，就会写入该文件。内容通常包括 Deep Agents 返回状态、`project_status` 和最终计算得到的 `run_status`。

当图执行异常时，Supervisor 会尽量依据当前线程绑定的 `project_id` 重新读取 SQLite 快照，并把这个权威状态写入结果。已经成功提交的阶段不会因最后一次模型调用失败而从记录中消失。

### 3.5 `final-report.md`：本次运行报告

如果最终结果中存在非空的最后一条 AI 文本回复，日志器会生成 Markdown 报告，并在报告头标明：

- 有效运行状态；
- 当前科研阶段；
- 报告性质是正式完成还是运行草稿。

同一内容还会写入：

```text
.research-agent/outputs/<project-id>/final-report.md
```

只有 `run_status=completed`、`project_stage=COMPLETED` 且 `review_verdict=PASS` 时，该报告才是正式最终产物。其他情况生成的文件属于运行草稿，供排错或继续执行使用。

### 3.6 `summary.json`：结束摘要

摘要记录：

- `status`、`project_stage`、`review_verdict`、`finished_at` 和 `error`；
- `events_file`、`messages_file`、`result_file`、`report_file`、`project_report_file` 的绝对路径或空值。

有效运行状态按业务事实计算：

| `status` | 条件 | 含义 |
|---|---|---|
| `completed` | 阶段为 `COMPLETED` 且审查为 `PASS` | 科研流程正式完成 |
| `awaiting_input` | 阶段为 `SEARCH_REVIEW_PENDING` | 初次或补充检索已保存，等待用户审核候选集 |
| `inconclusive` | 阶段为 `INCONCLUSIVE` | 流程受控结束；可能是证据不足、用户停止或连续结构化结果失败 |
| `needs_revision` | 审查为 `REVISE` | 报告需要回到提取/综合环节修订 |
| `incomplete` | 调用正常返回，但未满足以上条件 | 本轮结束，科研状态尚未闭环 |
| `fallback` | 可用性异常触发离线降级 | 已记录失败，未生成科研结论 |
| `error` | 未被降级处理的异常 | 本次调用失败 |

这一区分可防止把“Python 调用没有抛异常”直接解释为“科研任务已经完成”。

## 4. 科研产物导出

SQLite 保存项目的权威业务事实。每次保存产物或推进阶段后，`JsonArtifactExporter` 会刷新便于人工阅读的镜像：

```text
.research-agent/outputs/<project-id>/
├── project.json
├── snapshot.json
├── state-events.json
├── final-report.md                 # 有 Agent 文本报告时生成
└── artifacts/
    ├── 000001-SearchReport.json
    ├── 000002-ScreeningDecision.json
    ├── 000003-PaperCard.json
    ├── 00000x-ReviewOutline.json
    ├── 00000x-SectionDraft.json
    ├── 00000x-NarrativeReview.json
    └── ...
```

- `project.json`：当前项目主体和当前阶段。
- `snapshot.json`：项目、全部产物及状态事件的聚合快照。
- `state-events.json`：状态迁移历史。
- `artifacts/*.json`：按数据库 `artifact_id` 排序的单份产物。
- `final-report.md`：最近一次为该项目生成的 Agent 文本报告；每次生成会覆盖同名文件。

正式业务产物在应用服务中经过相应 Pydantic schema 校验；当前包括检索、论文卡片、综合、审查、综述提纲、分节草稿和完整综述。`RuntimeFallback`、`InsufficientEvidence` 等系统产物按各自受控路径保存。导出器写文件时先生成 `.tmp`，再替换正式文件，减少进程中断留下半份 JSON 的概率。

## 5. 排错时的推荐阅读顺序

1. 先看 `summary.json`，确认本轮状态、项目阶段、审查结论和错误。
2. 再看 `events.jsonl`，找到最后一个成功步骤和首个失败事件。
3. 需要核对参数或返回值时查看 `messages.jsonl`。
4. 查看 `outputs/<project-id>/snapshot.json`，确认数据库中已经正式提交的事实。
5. 若运行记录与项目快照有差异，以 SQLite/项目快照反映的业务状态为准；运行日志描述的是一次执行过程。

看到 `INCONCLUSIVE` 时，应继续读取最新 `InsufficientEvidence.reason`。例如 Synthesizer 连续两次提交失败也会进入该终态，即使项目已经保存多份 `PaperCard` 和 Evidence。已知故障模式、数值校验误判和恢复边界见[《故障诊断与当前限制》](troubleshooting.md)。
