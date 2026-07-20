# 架构与依赖说明

本文说明当前源码中的分层、Agent 边界、工具权限、状态约束和持久化方式。具体的主 Agent—子 Agent 调用时序见[《主Agent与子Agent交互流程分析》](主Agent与子Agent交互流程分析.md)；本文侧重解释各模块为何这样划分。

## 1. 设计目标

项目采用 Supervisor–Worker 多 Agent 模式：`research-supervisor` 负责流程编排，八个窄化子 Agent 分别负责检索、单篇阅读、综合、证据审查、提纲设计、分节写作、总编整合和事实核查。

这里的“窄化”指每个子 Agent 只能调用完成本角色所需的少量工具。模型负责理解任务、选择下一步和生成结构化内容；工具与应用服务负责 API 请求、PDF 解析、数据校验、事务写入等可重复执行的确定性能力。这样做有三个目的：

- 缩小权限面，降低角色越权、误写文件或绕过状态机的风险。
- 让科研产物经过统一数据契约和状态门禁后再进入数据库。
- 将模型推理与业务事实分开；即使模型调用失败，已提交的项目、证据和状态仍可追踪。

“业务层”在本文中主要指 `domain/` 与 `application/`：前者定义项目、证据和状态规则，后者组织这些规则并提供统一服务入口。它们表达“科研项目允许发生什么”，不负责网页检索、模型调用或命令行展示。

## 2. 各层职责

### 2.1 Domain：业务事实与状态规则

- `domain/models.py`：定义 `ResearchProject`、`SearchReport`、`PaperCard`、`Evidence`、`SynthesisReport`、`ReviewResult`、`ReviewOutline`、`SectionDraft`、`NarrativeReview` 和 `FactCheckReport` 等 Pydantic 数据契约。
- `domain/workflow.py`：定义合法状态迁移及 Reviewer 门禁。
- Domain 不读取环境变量、不访问 SQLite，也不依赖 LangChain、Deep Agents 或 API 框架。

状态主线如下：

```text
CREATED
  → SEARCHED
  → SEARCH_REVIEW_PENDING
  → SCREENED
  → EXTRACTED
  → SYNTHESIZED
  → REVIEW_PENDING
  → REVIEWED
      ├─ PASS   → OUTLINED → NARRATED → COMPLETED
      └─ REVISE → EXTRACTED → 重新综合与审查

NARRATED
  ├─ 最新事实核查全部 PASS → COMPLETED
  └─ 存在 REVISE → REVISION_PENDING → NARRATED → 重新核查

CREATED / SEARCHED / SEARCH_REVIEW_PENDING / SCREENED / EXTRACTED / SYNTHESIZED / REVIEW_PENDING / REVIEWED / OUTLINED / NARRATED
  └─ 证据不足或无法继续 → INCONCLUSIVE
```

各状态表示已经写入的业务事实，而非模型准备执行的计划：

- `CREATED`：项目已创建。
- `SEARCHED`：`SearchReport` 已通过校验并提交。
- `SEARCH_REVIEW_PENDING`：系统自动检索-筛选迭代已经结束，候选集已持久化，等待用户最终手筛、补充检索词、增删论文或确认。
- `SCREENED`：筛选决定已提交，入选论文集合已经固定；后续 `paper-reader` 只能处理最新 `ScreeningDecision.included_paper_ids` 中的论文。
- `EXTRACTED`：入选论文的 `PaperCard` 处理结束，可以进入综合。
- `SYNTHESIZED`：`SynthesisReport` 已提交。
- `REVIEW_PENDING`：项目已进入待审查阶段。
- `REVIEWED`：结构化 `ReviewResult` 已提交。
- `OUTLINED`：证据审查已通过，`ReviewOutline` 已保存，分节正文可以开始写作。
- `NARRATED`：`NarrativeReview` 已整合完成，等待逐节事实核查收尾。
- `REVISION_PENDING`：最新事实核查发现问题，只允许重写被标记章节并重新整合。
- `COMPLETED`：综述正文及各节 `FactCheckReport` 已生成，项目流程完成。
- `INCONCLUSIVE`：证据不足、连续结构校验失败或其他受控原因使流程无法形成可靠结论。

当前 `INCONCLUSIVE` 同时承载科研证据不足与执行管线无法安全继续两类终止原因。判断具体原因时必须读取 `InsufficientEvidence.reason` 和运行事件；该阶段在当前状态机中不可重新打开。

状态机把“执行完成”和“科研结论可信”分开。例如 Agent 图正常返回，只说明本次程序调用结束；只有 `COMPLETED + PASS` 才代表科研流程正式完成。

### 2.2 Application：业务用例

- `application/ports.py`：定义 Repository 需要提供的接口。
- `application/research_service.py`：CLI、API 和项目工具共用的业务入口，负责项目、产物、状态和快照操作。
- `application/search_review.py`：承接系统自动检索迭代后的候选集，保存 Agent 初筛结果、用户反馈、补充检索报告、篇数/轮数控制项和最终 `ScreeningDecision`。
- `application/artifact_normalization.py`：归一化已知的 Agent 边界字段别名，再交给正式模型校验。
- `application/fallback.py`：模型或外部网络不可用时，在现有项目中保存 `RuntimeFallback`；没有项目时创建 `CREATED` 项目。

应用服务让所有入口共享同一组规则。CLI、HTTP API 和 Agent Tool 无需分别实现状态迁移与数据校验。

### 2.3 Infrastructure：外部实现与持久化

- `infrastructure/config.py`：读取模型、路径、检索次数、重试和降级等配置。
- `infrastructure/sqlite_repository.py`：实现 Repository，持久化项目、产物和状态事件。
- `infrastructure/artifact_exporter.py`：把数据库快照和产物镜像为便于查看的 UTF-8 JSON；先写 `.tmp` 再替换目标文件。
- `infrastructure/run_logger.py`：记录每次运行的进度事件、模型/工具交互、最终状态和 Markdown 报告。
- `infrastructure/workspace.py`：把打包的 Skills 与 `memories/AGENTS.md` 复制到运行工作区，并清理目标位置的旧版本。

SQLite 是业务事实的权威来源；`outputs/` 是可读镜像；`runs/` 记录单次执行过程。三者用途不同，详见[《运行进度、日志与导出产物》](runtime-observability.md)。

### 2.4 Tools 与 Skills：能力和操作规程

Tool 是可调用函数，例如 `search_openalex(query, limit)`、`fetch_paper_text(paper_id, doi, url, max_pages)`、`commit_subagent_result(subagent_type)`。参数由框架根据函数签名暴露给模型，函数返回 JSON 字符串或结构化结果。

Skill 是写给 Agent 的操作规程，描述执行顺序、证据要求和输出约束。当前实现中：

- 主 Agent 启动时读取 `research-protocol` Skill 全文并直接注入 system prompt，用于约束全流程。
- `WorkspaceBootstrapper` 会复制全部 Skill 目录，便于工作区保持完整。
- `build_subagent_registry()` 将 `literature-search`、`paper-reading`、`research-synthesis` 和 `evidence-review` 全文分别注入前四个研究子 Agent 的 system prompt；提纲、写作、总编和事实核查角色使用专用 system prompt。八个子 Agent 都没有通用文件系统或 todo 能力，只使用各自的业务工具和结构化响应 schema。

这个边界保证子 Agent 的行为主要由角色提示词、输出 schema、工具白名单和中间件共同约束。

### 2.5 Agents：编排与角色分工

`agents/supervisor.py` 是唯一编排入口。它创建 Deep Agents 图、`FilesystemBackend`、`InMemorySaver`、工作流门禁、八个子 Agent 和运行日志回调。

主 Agent 可直接使用的项目工具为：

- `create_research_project(topic, research_question)`：创建项目并绑定当前线程。
- `get_research_project(project_id)`：读取权威项目快照。
- `save_screening_decision(project_id, included_paper_ids, excluded_paper_ids, reasons)`：保留的内部筛选工具；人工审核阶段会被 Guard 拦截，正式确认由反馈 API 完成。
- `commit_subagent_result(subagent_type)`：提交 `ResearchRuntimeState` 中刚产生的结构化结果。
- `advance_project_stage(project_id, target_stage, actor)`：仅允许显式推进到 `EXTRACTED`、`REVIEW_PENDING` 或 `COMPLETED`。
- `finish_inconclusive(project_id, reason, actor)`：保存 `InsufficientEvidence` 并进入 `INCONCLUSIVE`。

Deep Agents 框架还为主 Agent 提供任务委派及工作区类能力；项目源码隐藏了通用写产物工具、检索工具、PDF 工具、`get_active_research_project` 和 `verify_doi`，避免主 Agent 绕过专门角色和提交路径。

八个子 Agent 的实际工具权限如下：

| 子 Agent | 允许调用的工具 | 结构化输出 | 关键限制 |
|---|---|---|---|
| `literature-scout` | `search_openalex`；配置允许时增加 `search_crossref` | `SearchReport` | 工具串行；中间件捕获原始搜索结果；模型只返回候选 ID、初筛决定和覆盖分析；每个任务只委派一次 |
| `paper-reader` | `fetch_paper_text`、`extract_pdf_text` | `PaperCard` | 工具串行；同一论文的全文获取次数受限；本地 PDF 最多解析一次；模型调用最多四次 |
| `research-synthesizer` | `get_active_research_project` | `SynthesisReport` | 最多两次工具调用；只能基于项目中的 Evidence 综合 |
| `evidence-reviewer` | `get_active_research_project` | `ReviewResult` | 项目读取最多一次；第二次读取会直接结束本次 Reviewer；模型调用最多三次；无文件系统工具；不执行网络 DOI 校验 |
| `research-outliner` | `get_active_research_project` | `ReviewOutline` | 仅在 `REVIEWED` 委派；最多两次工具调用 |
| `narrative-writer` | `get_active_research_project` | `SectionDraft` | 仅在 `OUTLINED` 委派；一次只负责一个章节；草稿逐份提交 |
| `chief-editor` | `get_active_research_project` | `NarrativeReview` | 仅在 `OUTLINED` 委派；整合全部草稿后进入 `NARRATED` |
| `fact-checker` | `get_active_research_project` | `FactCheckReport` | 仅在 `NARRATED` 委派；按章节输出 `PASS` 或 `REVISE` 诊断 |

`verify_doi(doi)` 没有分配给主 Agent 或任何子 Agent，由 `SearchReviewService` 在用户手动添加 DOI 时调用。Reviewer 把 DOI 当作论文元数据，实际审查集中在 `claim`、`evidence_id`、原文引句、页码/章节和结论之间的对应关系。

`fetch_paper_text` 与 `extract_pdf_text` 的区别：前者根据论文标识、DOI 或 URL 查找并下载开放 PDF，写入工作区缓存后提取页面文本；后者只解析工作区中已经存在的本地 PDF。前者包含网络获取与缓存，后者只做本地文本提取。

### 2.6 Entry Points：前端、CLI 与 API

- `cli.py`：提供 `demo`、`run`、`status`、`serve`。
- `api/app.py`：提供本地前端、健康检查、项目列表与快照、普通调用、SSE 流式调用、检索审核反馈和项目继续接口。
- `api/frontend/`：原生 HTML、CSS 和 JavaScript 界面，与 API 同源运行，不引入独立前端构建链；支持产物 HTML/JSON 双视图。
- 推荐用户执行 `research-agent serve` 后从根路径前端发起研究和完成人工审核；CLI 与 HTTP API 保留给自动化和集成场景。
- 三个入口都通过 `ResearchSupervisor` 执行科研流程，共享同一 Repository、应用服务、状态机和降级规则。

## 3. 依赖方向

```text
前端 / CLI / HTTP API
      ↓
ResearchSupervisor ──→ 主 Agent / 八个窄化子 Agent
      ↓                         ↓
Project Tools              Literature Tools
      ↓                         ↓
ResearchService             外部 API / 本地 PDF
      ↓
Repository Port
      ↑ 实现
SQLite Repository ──→ JSON Artifact Exporter
      ↓
Domain Models + Workflow

横切能力：ResearchRunLogger 通过回调观察 Supervisor、模型和 Tool 的运行过程。
```

Domain 位于依赖核心，其他层可以依赖 Domain；Domain 不反向导入其他层。Application 依赖 Repository 接口，Infrastructure 提供具体实现，这让业务规则可以在不启动模型或网络的情况下测试。

## 4. 一次子 Agent 结果提交

```text
Supervisor 调用 task 委派子 Agent
  ↓
子 Agent 返回符合 schema 的结构化响应
  ↓
recording_runnable 将精确响应暂存到 ResearchRuntimeState
  ↓
Supervisor 调用 commit_subagent_result(subagent_type)
  ↓
ResearchService 归一化边界字段并执行 Pydantic 校验
  ↓
SqliteResearchRepository 在同一 SQLite 事务中：
  ├─ 校验状态迁移
  ├─ 写入 artifact
  ├─ 更新 project
  └─ 追加 state_event
  ↓
JsonArtifactExporter 刷新对应 JSON 镜像
```

主 Agent 只传 `subagent_type`，无需复制子 Agent 的整段 JSON，因此可以减少字段丢失、引文被改写和模型二次拼装造成的格式错误。

不同子 Agent 的提交效果为：

- Scout：搜索中间件捕获原始论文元数据，`recording_runnable` 再用 `candidate_ids` 筛选并重建 `candidates`。Scout 在一次子任务内按前端限制自动完成多轮检索、标题摘要级筛选、覆盖盲区分析和检索词调整；随后保存最终 `SearchReport`。非空候选集会同时创建 `CandidateSetSnapshot`，项目经 `SEARCHED` 进入 `SEARCH_REVIEW_PENDING` 并等待用户最终手筛。
- 检索审核：保存 `SearchFeedback`、可选的 `SupplementalSearchReport` 和新版 `CandidateSetSnapshot`；用户确认后保存 `ScreeningDecision` 并进入 `SCREENED`。后续运行开始前可追加补偿事件撤销到上一版候选集；点击继续后进入精读。
- Reader：保存单篇 `PaperCard`，阶段暂不变化；全部入选论文处理完后由主 Agent推进 `SCREENED → EXTRACTED`。
- Synthesizer：保存 `SynthesisReport`，`EXTRACTED → SYNTHESIZED`。
- Reviewer：保存 `ReviewResult`，`REVIEW_PENDING → REVIEWED`。
- Outliner：保存 `ReviewOutline`，`REVIEWED → OUTLINED`。
- Writer：逐节保存 `SectionDraft`，项目保持 `OUTLINED`。
- Chief editor：保存 `NarrativeReview`，`OUTLINED → NARRATED`。
- Fact checker：逐节保存 `FactCheckReport`；全部 `PASS` 才进入 `COMPLETED`，存在 `REVISE` 则进入 `REVISION_PENDING` 定向修订并重新核查。

若结构化结果校验失败，该份待提交结果会被释放，错误响应会指示是否允许重试。Scout 不重新委派；其余角色最多容许一次重新委派，连续两份无效结果后要求调用 `finish_inconclusive`。

Synthesizer 的业务校验还会检查研究空白引用的 Evidence、支持论文集合和假设中的数字。数字提取会忽略 `2D`、`3DVG`、`FFL-3DOG`、`ResNet-50` 等技术标识，并对规范化数值执行精确集合比较。

`ResearchWorkflowGuardMiddleware` 还会执行以下硬约束：项目必须先创建或由 Supervisor 绑定恢复；只能委派八个注册角色；角色必须处于指定阶段；上一份待提交结果必须先提交；同一任务的 Scout 只能调用一次；`SEARCH_REVIEW_PENDING` 阶段的确认和停止只能来自用户反馈 API。这些检查位于中间件中，不依赖模型自行遵守提示词。

## 5. 一次 Supervisor 构建

```text
ResearchSupervisor.__init__
  ├─ Settings
  ├─ SqliteResearchRepository
  ├─ JsonArtifactExporter
  ├─ ResearchService
  ├─ SearchReviewService
  ├─ OfflineFallback
  ├─ WorkspaceBootstrapper
  │    ├─ skills/*/SKILL.md
  │    └─ memories/AGENTS.md
  ├─ ResearchRuntimeState
  ├─ InMemorySaver
  └─ create_deep_agent
       ├─ 主 Agent 项目工具
       ├─ ResearchWorkflowGuardMiddleware
       ├─ SerialToolExecutionMiddleware
       ├─ 八个窄化子 Agent
       ├─ research-protocol Skill
       └─ FilesystemBackend(virtual_mode=True)
```

## 6. Memory 与状态边界

- `InMemorySaver`：保存同一 `thread_id` 的 LangGraph 短期图状态；进程重启后清空。
- `ResearchRuntimeState`：保存线程绑定的 `project_id`、实际执行过的检索词、原始搜索结果、待提交子 Agent 结果、拒绝次数和论文获取签名；它同样属于进程内状态。
- `memories/AGENTS.md`：保存跨会话的科研身份与长期约束，启动时复制到运行工作区。
- SQLite：保存项目、正式产物和状态事件，是可恢复的业务事实。
- `SearchReviewService`：从 SQLite 读取最新候选集，执行用户给出的补充 query、DOI 核验、增删合并和确认；不依赖进程内图状态。
- `runs/`：保存一次调用的技术执行记录。

分开存储可以避免把聊天上下文、临时交接数据、长期规则和科研事实混为同一类状态。

## 7. 失败与降级原则

仅模型不可用、认证失败、限流、连接超时及外部网络可用性异常进入 `OfflineFallback`。降级会优先复用已创建项目并保存 `RuntimeFallback`；没有可用项目时才创建 `CREATED` 项目。

Pydantic 校验失败、非法状态迁移和缺少前置产物属于业务错误，返回给 Agent 或调用方修正。降级流程不会伪造检索结果、论文证据或研究结论。

无论图执行成功或抛错，Supervisor 都尽量从 SQLite 重新读取当前项目状态写入运行结果，避免最后一条模型消息掩盖已经提交的权威阶段。
