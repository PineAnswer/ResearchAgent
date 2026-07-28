# 架构与依赖说明

本文说明当前源码中的分层、入口链路、Agent 边界、工具权限、状态约束和持久化方式。具体的主 Agent—子 Agent 调用时序见[《主Agent与子Agent交互流程分析》](主Agent与子Agent交互流程分析.md)；本文先给出从用户请求到研究产物落库的全局工作流，再解释各模块为何这样划分。

## 1. 设计目标

项目采用 Supervisor–Worker 多 Agent 模式：`research-supervisor` 负责流程编排，七个窄化子 Agent 分别负责检索、单篇阅读、综合、证据审查、提纲设计、分节写作和总编整合。Web 端再用 `ConversationRunManager` 把一次 Agent 图执行包装成可持久化的后台运行，使不同会话可以并发、同一会话保持串行。

这里的“窄化”指每个子 Agent 只能调用完成本角色所需的少量工具。模型负责理解任务、选择下一步和生成结构化内容；工具与应用服务负责 API 请求、PDF 解析、数据校验、事务写入等可重复执行的确定性能力。这样做有以下目的：

- 缩小权限面，降低角色越权、误写文件或绕过状态机的风险。
- 让科研产物经过统一数据契约和状态门禁后再进入数据库。
- 将模型推理与业务事实分开；即使模型调用失败，已提交的项目、证据和状态仍可追踪。
- 将“会话、一次运行、科研项目”分成三个标识：`conversation_id` 管理用户工作区，`run_id` 描述一次后台执行，`project_id` 承载可恢复的科研事实；LangGraph `thread_id` 只承担进程内短期图状态。
- 在首次自动检索后强制进入人工候选审核，确认后的续跑只读取最新 `ScreeningDecision`，避免模型替用户决定最终精读集合。

“业务层”在本文中主要指 `domain/` 与 `application/`：前者定义项目、证据和状态规则，后者组织这些规则并提供统一服务入口。它们表达“科研项目允许发生什么”，不负责网页检索、模型调用或命令行展示。

## 2. 各层职责

### 2.1 Domain：业务事实与状态规则

- `domain/models.py`：定义 `ResearchProject`、`ScoutReport`、`SearchReport`、`PaperCard`、`Evidence`、`SynthesisReport`、`ReviewResult`、`ReviewOutline`、`SectionDraft` 和 `NarrativeReview` 等研究契约，也定义 `ResearchConversation`、`ConversationRun`、文献库、阅读进度、批注和项目谱系等持久化模型。
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
      ├─ PASS   → OUTLINED → COMPLETED
      └─ REVISE → EXTRACTED → 重新综合与审查

CREATED / SEARCHED / SEARCH_REVIEW_PENDING / SCREENED / EXTRACTED / SYNTHESIZED / REVIEW_PENDING / REVIEWED / OUTLINED
  ├─ 人工停止或真实证据不足 → INCONCLUSIVE
  └─ 执行故障 → RuntimeIssue，并保持当前阶段
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
- `NARRATED`：旧版本遗留状态；新流程不会再进入该阶段，已有 `NarrativeReview` 的历史项目可以直接推进到完成。
- `COMPLETED`：完整 `NarrativeReview` 已生成，项目流程完成。
- `INCONCLUSIVE`：用户主动停止或真实证据不足使流程无法形成可靠结论。

如果检索确有结果、但全部结果都被年份条件过滤，系统会保存 `CandidateSetSnapshot` 并暂留 `SEARCHED`，允许用户从被过滤列表手动恢复候选；恢复出至少一篇候选后才进入 `SEARCH_REVIEW_PENDING`。空检索结果本身可以携带空快照进入 `SEARCH_REVIEW_PENDING`，供用户补充查询或手动加论文。

执行管线故障、模型超时或连续结构校验失败会保存 `RuntimeIssue` 并保持当前阶段。判断具体原因时必须读取 `InsufficientEvidence.reason`、`RuntimeIssue.reason` 和运行事件。普通状态迁移中 `COMPLETED` 与 `INCONCLUSIVE` 都是终态；`prepare_continuation()` 只会通过专用恢复事务修复两类历史异常：缺少有效 `NarrativeReview` 的错误完成，以及带可恢复执行故障标记的旧式 `INCONCLUSIVE`。真实证据不足形成的 `INCONCLUSIVE` 不会恢复。

状态机把“执行完成”和“科研结论可信”分开。例如 Agent 图正常返回，只说明本次程序调用结束；只有 `COMPLETED + PASS` 才代表科研流程正式完成。

### 2.2 Application：业务用例

- `application/ports.py`：定义 Repository 需要提供的接口。
- `application/research_service.py`：CLI、API 和项目工具共用的业务入口，负责项目、产物、状态和快照操作。
- `application/search_review.py`：承接系统自动检索迭代后的候选集，保存 Agent 初筛结果、用户反馈、补充检索报告、篇数/轮数控制项和最终 `ScreeningDecision`。
- `application/candidate_ranking.py`：在原始搜索结果被捕获和去重后，计算相关性、影响力、权威性、多样性与组合排序；撤稿标记和 Agent 三态决定也参与最终排序。
- `application/library_service.py`：组织跨项目文献库、文件夹、笔记、附件、全文分块、阅读工作台、问答、导入导出和项目论文关联。
- `application/paper_ids.py`：统一 DOI、OpenAlex URL、裸 ID 和标题键，供候选合并、筛选决定与 PaperCard 校验共同使用。
- `application/artifact_normalization.py`：归一化已知的 Agent 边界字段别名，再交给正式模型校验。
- `application/fallback.py`：模型或外部网络不可用时，在现有项目中保存 `RuntimeFallback`；没有项目时创建 `CREATED` 项目。

应用服务让所有入口共享同一组规则。CLI、HTTP API 和 Agent Tool 无需分别实现状态迁移与数据校验。

### 2.3 Infrastructure：外部实现与持久化

- `infrastructure/config.py`：读取模型、路径、检索次数、重试和降级等配置。
- `infrastructure/sqlite_repository.py`：实现 Repository，持久化项目、产物和状态事件。
- `infrastructure/artifact_exporter.py`：把数据库快照和产物镜像为便于查看的 UTF-8 JSON；先写 `.tmp` 再替换目标文件。
- `infrastructure/run_logger.py`：记录每次运行的进度事件、模型/工具交互、最终状态和 Markdown 报告。
- `infrastructure/workspace.py`：把打包的 Skills 与 `memories/AGENTS.md` 复制到运行工作区，并清理目标位置的旧版本。
- `infrastructure/venue_rankings.py`：把本地场馆种子数据导入 SQLite，规范化别名并为候选论文补充 CCF、JCR/影响因子和来源解释。
- `infrastructure/observable_chat_model.py`：保留 OpenAI/Anthropic 原始响应供回调日志诊断，并在原生 Anthropic 路径强制使用 Tool Strategy 生成结构化输出。

SQLite 是业务事实的权威来源；其中除了项目、产物和状态事件，还保存用户会话、后台运行、消息、候选勾选状态、文献库、全文分块、批注和项目谱系。`outputs/` 是可读镜像；`runs/` 记录单次 Agent 执行过程。三者用途不同，详见[《运行进度、日志与导出产物》](runtime-observability.md)。

### 2.4 Tools 与 Skills：能力和操作规程

Tool 是可调用函数，例如 `search_multi_source(queries, limit_per_source, year_from, year_to)`、`search_library(query, limit)`、`fetch_paper_text(paper_id, doi, url, max_pages)`、`commit_subagent_result(project_id, subagent_type)`。参数由框架根据函数签名暴露给模型，函数返回 JSON 字符串或结构化结果。

Skill 是写给 Agent 的操作规程，描述执行顺序、证据要求和输出约束。当前实现中：

- 主 Agent 启动时读取 `research-protocol` Skill 全文并直接注入 system prompt，用于约束全流程。
- `WorkspaceBootstrapper` 会复制全部 Skill 目录，便于工作区保持完整。
- `build_subagent_registry()` 将 `literature-search`、`paper-reading`、`research-synthesis` 和 `evidence-review` 全文分别注入前四个研究子 Agent 的 system prompt；提纲、写作和总编角色使用专用 system prompt。七个子 Agent 都没有通用文件系统或 todo 能力，只使用各自的业务工具和结构化响应 schema。

这个边界保证子 Agent 的行为主要由角色提示词、输出 schema、工具白名单和中间件共同约束。

### 2.5 Agents：编排与角色分工

`agents/supervisor.py` 是唯一编排入口。它创建 Deep Agents 图、`FilesystemBackend`、`InMemorySaver`、工作流门禁、七个子 Agent 和运行日志回调。

主 Agent 可直接使用的项目工具为：

- `create_research_project(topic, research_question)`：创建项目并绑定当前线程。
- `get_research_project(project_id)`：读取权威项目快照。
- `save_screening_decision(project_id, included_paper_ids, excluded_paper_ids, reasons)`：保留的内部筛选工具；人工审核阶段会被 Guard 拦截，正式确认由反馈 API 完成。
- `commit_subagent_result(project_id, subagent_type)`：校验当前线程绑定的项目，并提交 `ResearchRuntimeState` 中刚产生的结构化结果。
- `advance_project_stage(project_id, target_stage, actor)`：仅允许显式推进到 `EXTRACTED`、`REVIEW_PENDING` 或 `COMPLETED`。
- `record_research_issue(project_id, ...)`：保存可恢复 `RuntimeIssue` 并保持当前阶段；人工 `stop` 才会保存 `InsufficientEvidence` 并进入 `INCONCLUSIVE`。

Deep Agents 框架还为主 Agent 提供任务委派及工作区类能力；项目源码隐藏了通用写产物工具、检索工具、PDF 工具、`get_active_research_project` 和 `verify_doi`，避免主 Agent 绕过专门角色和提交路径。

七个子 Agent 的实际工具权限如下：

| 子 Agent | 允许调用的工具 | 结构化输出 | 关键限制 |
|---|---|---|---|
| `literature-scout` | `search_library`、`search_multi_source` | `ScoutReport`（提交前重建为 `SearchReport`） | 多条短查询覆盖 OpenAlex、Crossref、Semantic Scholar 和 arXiv；中间件注入年份/轮数限制、捕获原始结果并重建候选集 |
| `paper-reader` | `retrieve_library_passages`、`fetch_paper_text`、`extract_pdf_text` | `PaperCard` | 已有 `library_id` 时复用本地索引；其余情况才获取开放全文或解析明确给出的本地 PDF；工具串行，模型调用最多四次 |
| `research-synthesizer` | `get_active_research_project` | `SynthesisReport` | 最多两次工具调用；只能基于项目中的 Evidence 综合 |
| `evidence-reviewer` | `get_active_research_project` | `ReviewResult` | 项目读取最多一次；第二次读取会直接结束本次 Reviewer；模型调用最多三次；无文件系统工具；不执行网络 DOI 校验 |
| `research-outliner` | `get_active_research_project` | `ReviewOutline` | 仅在 `REVIEWED` 委派；最多两次工具调用 |
| `narrative-writer` | `get_active_research_project` | `SectionDraft` | 仅在 `OUTLINED` 委派；一次只负责一个章节；草稿逐份提交 |
| `chief-editor` | `get_active_research_project` | `NarrativeReview` | 仅在 `OUTLINED` 委派；有效提交后直接进入 `COMPLETED`；结构缺失时可从已保存提纲和草稿执行确定性装配 |

`verify_doi(doi)` 没有分配给主 Agent 或任何子 Agent，由 `SearchReviewService` 在用户手动添加 DOI 时调用。Reviewer 把 DOI 当作论文元数据，实际审查集中在 `claim`、`evidence_id`、原文引句、页码/章节和结论之间的对应关系。

`fetch_paper_text` 与 `extract_pdf_text` 的区别：前者根据论文标识、DOI 或 URL 查找并下载开放 PDF，写入工作区缓存后提取页面文本；后者只解析工作区中已经存在的本地 PDF。前者包含网络获取与缓存，后者只做本地文本提取。

文献库问答使用独立的 `library-research-agent`，不参与科研状态机。它先在 system prompt 中接收最多 500 篇、或调用方指定范围内的文献目录，再按问题串行调用 `search_library`、`retrieve_library_passages` 或 `get_library_paper_context`；模型调用总上限为 20。该 Agent 返回自由文本，运行时从最后一条 AI 消息提取正文和 `<!-- coverage: ... -->`，再把工具来源注册表中存在的 `[[source_id]]` 转换为编号引用。模型不可用、执行失败或正文为空时退回最多八条关键词检索摘录，并标记为离线检索模式。项目选段、综述和项目论文范围的同步问答走另一条有界上下文链路，继续使用 `LibraryAgentResponse` 结构化输出与最多四条摘录兜底。

### 2.6 Entry Points：前端、CLI 与 API

- `cli.py`：提供 `demo`、`run`、`status`、`serve`。`run` 直接调用 Supervisor，适合终端自动化；它不会创建可跨进程保留的 `ConversationRun`。
- `api/app.py`：构造一个共享 `ResearchSupervisor` 和 `ConversationRunManager`，提供本地前端、健康检查、会话/项目/运行查询、普通调用、SSE 流式调用、检索审核、研究续跑、文献库与阅读工作台接口。
- `api/background_runs.py`：把 Web 会话提交转换为 `queued → running → awaiting_input/completed/inconclusive/interrupted/failed` 的后台运行记录；SQLite 的部分唯一索引保证同一会话最多一个活动运行，不同会话可以并发。
- `api/frontend/`：原生 HTML、CSS 和 JavaScript 界面，与 API 同源运行，不引入独立前端构建链；支持产物 HTML/JSON 双视图。
- 推荐用户执行 `research-agent serve` 后从根路径前端发起研究和完成人工审核；CLI 与 HTTP API 保留给自动化和集成场景。
- 推荐的 Web 链路先创建 `ResearchConversation + ResearchProject`，再异步启动初始运行；用户确认候选集时自动启动续跑。兼容接口 `/api/research/invoke` 与 `/api/research/stream` 直接调用 Supervisor，不创建后台会话运行记录。
- Web、兼容 API 和 CLI 最终都通过 `ResearchSupervisor` 执行科研流程，共享同一 Repository、应用服务、状态机和降级规则。

## 3. 依赖方向

```text
前端
  ↓
FastAPI ──→ ConversationRunManager ──→ ResearchSupervisor
  │                                         ├─→ 主 Agent / 七个窄化子 Agent
  │                                         ├─→ Project / Literature / Library Tools
  │                                         └─→ ResearchRuntimeState + InMemorySaver
  ├─→ SearchReviewService
  └─→ LibraryService
            ↓
       ResearchService
            ↓
       Repository Port
            ↑ 实现
       SQLite Repository ──→ JSON Artifact Exporter
            ↓
       Domain Models + Workflow

CLI 与兼容 HTTP API 可以直接进入 ResearchSupervisor；它们仍复用下方同一组服务与持久化边界。

横切能力：ResearchRunLogger 通过回调观察 Supervisor、模型和 Tool 的运行过程。
```

Domain 位于依赖核心，其他层可以依赖 Domain；Domain 不反向导入其他层。Application 依赖 Repository 接口，Infrastructure 提供具体实现，这让业务规则可以在不启动模型或网络的情况下测试。

## 4. 一次子 Agent 结果提交

```text
Supervisor 调用 task 委派子 Agent
  ↓
子 Agent 返回符合 schema 的结构化响应
  ↓
recording_runnable 注入角色相关记忆并将精确响应暂存到 ResearchRuntimeState
  ↓
Supervisor 调用 commit_subagent_result(project_id, subagent_type)
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

- Scout：模型只生成轻量 `ScoutReport`，不复制标题、摘要等大字段。`ExecutedSearchTrackingMiddleware` 把真实执行过的查询和工具原始结果保存到线程状态；`recording_runnable` 用真实 `candidate_ids` 匹配、去重、补充 Agent 决策并调用 `rank_candidates()`，形成待提交的完整 `SearchReport`。兼容旧模型临时编号时会按搜索结果顺序映射真实 ID，但正式提示词和 Guard 始终要求真实 paper ID/DOI。提交后保存 `SearchReport`，再由回调创建 `CandidateSetSnapshot` 并打开人工审核。
- 检索审核：保存 `SearchFeedback`、可选的 `SupplementalSearchReport` 和新版 `CandidateSetSnapshot`；用户确认后保存 `ScreeningDecision`、进入 `SCREENED`，并立即创建后续研究任务开始精读。
- Reader：保存单篇 `PaperCard`，阶段暂不变化；全部入选论文处理完后由主 Agent推进 `SCREENED → EXTRACTED`。
- Synthesizer：保存 `SynthesisReport`，`EXTRACTED → SYNTHESIZED`。
- Reviewer：保存 `ReviewResult`，`REVIEW_PENDING → REVIEWED`。PASS 在同一运行内继续写作；首次 REVISE 自动回到 `EXTRACTED` 修订并复审一次，第二次仍需修订时保存 `RuntimeIssue` 并停止。
- Outliner：保存 `ReviewOutline`，`REVIEWED → OUTLINED`。
- Writer：逐节保存 `SectionDraft`，项目保持 `OUTLINED`。
- Chief editor：优先保存模型返回的 `NarrativeReview` 并执行 `OUTLINED → COMPLETED`；若子 Agent 缺失结构化结果或正文结构校验失败，`assemble_narrative_review()` 会用最新 `ReviewOutline` 与每节 `SectionDraft` 组装可追踪的综述，再以 `chief-editor-fallback` 提交完成。

若结构化结果校验失败，该份待提交结果会被释放，错误响应会指示是否允许重试。连续两份无效结果后保存 `RuntimeIssue` 并保持当前阶段，不会把执行故障记为证据不足。

Synthesizer 的边界会检查研究空白引用的 Evidence、支持论文集合和假设中的数字。未知 Evidence 或支持论文不一致会写警告供诊断；未被所引原文支持的量化假设会在提交前降级为定性表述。数字提取会忽略 `2D`、`3DVG`、`FFL-3DOG`、`ResNet-50` 等技术标识，并对规范化数值执行精确集合比较。Reviewer 的 PASS 仍属于硬门禁：必须至少包含一个存在于最新 PaperCard 的 `verified_evidence_id`。

`ResearchWorkflowGuardMiddleware` 还会执行以下硬约束：项目必须先创建或由 Supervisor 绑定恢复；只能委派七个注册角色；角色必须处于指定阶段；上一份待提交结果必须先提交；同一任务的 Scout 通常只能调用一次，首份结果被系统拒绝后允许一次纠错重派；`SEARCH_REVIEW_PENDING` 阶段的确认和停止只能来自用户反馈 API。这些检查位于中间件中，不依赖模型自行遵守提示词。

## 5. 一次 Supervisor 构建

```text
ResearchSupervisor.__init__
  ├─ Settings
  ├─ SqliteResearchRepository
  ├─ VenueRankingIndex
  ├─ JsonArtifactExporter
  ├─ ResearchService
  │    └─ LibraryService
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
       ├─ 七个窄化子 Agent
       ├─ research-protocol Skill
       └─ FilesystemBackend(virtual_mode=True)
```

## 6. Memory 与状态边界

- `InMemorySaver`：保存同一 `thread_id` 的 LangGraph 短期图状态；进程重启后清空。
- `ResearchRuntimeState`：保存线程绑定的 `project_id/user_id/conversation_id`、年份与检索轮数约束、实际执行过的查询、查询轮次、原始搜索结果、待提交子 Agent 结果、按论文隔离的拒绝次数和全文获取签名；它同样属于进程内状态。
- `memories/AGENTS.md`：保存跨会话的科研身份与长期约束，启动时复制到运行工作区。
- SQLite：保存项目、正式产物、状态事件、用户会话、后台运行、消息和文献库，是可恢复的业务事实。
- `SearchReviewService`：从 SQLite 读取最新候选集，执行用户给出的补充 query、DOI 核验、增删合并和确认；不依赖进程内图状态。
- `runs/`：保存一次调用的技术执行记录。

分开存储可以避免把聊天上下文、临时交接数据、长期规则和科研事实混为同一类状态。

## 7. 失败与降级原则

仅模型不可用、认证失败、限流、连接超时及外部网络可用性异常进入 `OfflineFallback`。降级会优先复用已创建项目并保存 `RuntimeFallback`；没有可用项目时才创建 `CREATED` 项目。

Pydantic 校验失败、非法状态迁移和缺少前置产物属于业务错误，返回给 Agent 或调用方修正。降级流程不会伪造检索结果、论文证据或研究结论。

无论图执行成功或抛错，Supervisor 都尽量从 SQLite 重新读取当前项目状态写入运行结果，避免最后一条模型消息掩盖已经提交的权威阶段。

## 8. Web 端到端内部工作流

下面这条链路是当前前端创建新研究时的真实执行顺序：

```text
POST /api/conversations
  ↓
ResearchService.create_conversation
  ├─ SQLite 创建 ResearchProject(CREATED)
  ├─ SQLite 创建 ResearchConversation(project_id + thread_id)
  └─ 可选：创建父研究 → 子研究的 ResearchRelation
  ↓
ConversationRunManager.start_initial
  ├─ SQLite 创建 ConversationRun(queued)
  └─ asyncio 后台任务改为 running
  ↓
ResearchSupervisor.astart_project
  ├─ 把既有 project_id 绑定到 ResearchRuntimeState 与 WorkflowGuard
  ├─ 注册年份、最大检索轮数、是否优先本地库
  └─ 运行 Supervisor 图
       ↓
  literature-scout → ScoutReport → 系统重建 SearchReport
       ↓
  原子提交 SearchReport：CREATED → SEARCHED
       ↓
  SearchReviewService.begin_review
       ├─ 应用年份条件、场馆信息和 Agent 三态意见
       ├─ 保存 CandidateSetSnapshot 与逐论文勾选状态
       └─ SEARCHED → SEARCH_REVIEW_PENDING（全部被过滤时暂留 SEARCHED）
       ↓
ConversationRun 进入 awaiting_input，初始 Agent 图停止
  ↓
用户 PATCH 勾选状态，并 POST search-feedback
       ├─ refine：补充查询/DOI/手工论文，追加快照，继续等待
       ├─ undo：在续跑尚未开始时追加补偿快照，恢复上一次审核前状态
       ├─ stop：保存 InsufficientEvidence，进入 INCONCLUSIVE
       └─ accept：保存 ScreeningDecision，进入 SCREENED
                     ↓
              自动 start_continue
                     ↓
              paper-reader × 入选论文
                     ↓
              SCREENED → EXTRACTED
                     ↓
              research-synthesizer → SYNTHESIZED
                     ↓
              advance_project_stage → REVIEW_PENDING
                     ↓
              evidence-reviewer → REVIEWED
                 ├─ REVISE：回到 EXTRACTED，最多自动复审一次
                 └─ PASS：research-outliner → OUTLINED
                                          ↓
                            narrative-writer × 提纲章节
                                          ↓
                                  chief-editor
                                          ↓
                         NarrativeReview + COMPLETED
```

每次 `save_artifact_and_transition()` 都在一个 `BEGIN IMMEDIATE` SQLite 事务中写入产物、更新项目阶段并追加 `state_events`；事务提交后，应用层才刷新 `outputs/<project-id>/` 下的 JSON 镜像。前端轮询会话快照和运行状态，运行中的即时进度来自 `ConversationRunManager` 的进程内事件缓存，正式科研结果始终从 SQLite 快照读取。
