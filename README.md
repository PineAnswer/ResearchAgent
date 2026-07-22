# Research Agent

基于 Deep Agents、LangChain、LangGraph 和 Pydantic 的科研文献 Agent。系统通过检索、人工候选审核、论文阅读、跨论文综合、证据审查和长篇综述写作生成可追踪的研究产物。

## 使用方法

### 1. 准备环境

需要 Python 3.11 或更高版本。下面两种环境管理方式任选其一。

#### 方式 A：使用 Python venv

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

#### 方式 B：使用 Conda

```powershell
conda create -n research-agent python=3.11 pip -y
conda activate research-agent
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Conda 负责隔离 Python 环境，pip 负责读取 `requirements.txt` 并安装项目。当前文件包含 pip 的 editable 安装语法，因此不要使用 `conda install --file requirements.txt`。

`requirements.txt` 会以 editable 模式安装当前项目及开发依赖。无论使用 venv 还是 Conda，安装后都可以直接使用 `research-agent` 命令。

### 2. 配置环境变量

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少填写模型需要的 API Key：

```dotenv
RESEARCH_AGENT_MODEL=openai:gpt-4.1-mini
OPENAI_API_KEY=your-key

# 可选：留空时使用 OpenAlex 匿名访问
OPENALEX_API_KEY=your-openalex-key
OPENALEX_EMAIL=your-email@example.com
```

`OPENALEX_API_KEY` 和 `OPENALEX_EMAIL` 均可留空，项目仍可通过 OpenAlex 匿名接口检索文献。匿名访问额度较低，也更容易遇到 HTTP 429；需要频繁检索或更稳定的配额时，建议申请免费的 OpenAlex API Key。`OPENALEX_EMAIL` 仅作为 OpenAlex/Crossref polite usage 的联系信息。

使用 OpenAI 兼容接口时，同时设置：

```dotenv
RESEARCH_AGENT_PROVIDER=openai
RESEARCH_AGENT_MODEL=openai:your-model-name
OPENAI_API_KEY=your-key
RESEARCH_AGENT_BASE_URL=https://your-provider.example/v1
```

使用 Claude 时走原生 Anthropic Messages 协议，不经过 OpenAI 兼容层：

```dotenv
RESEARCH_AGENT_PROVIDER=anthropic
RESEARCH_AGENT_MODEL=anthropic:claude-sonnet-4-6
ANTHROPIC_API_KEY=your-claude-key
RESEARCH_AGENT_ANTHROPIC_BASE_URL=https://your-provider.example
```

两套配置可以同时保留在 `.env` 中；切换时只需修改
`RESEARCH_AGENT_PROVIDER` 和 `RESEARCH_AGENT_MODEL`。系统不会根据 `sk-` 前缀猜测
Key 的类型，也不会用 `OPENAI_API_KEY` 代替缺失的 `ANTHROPIC_API_KEY`。
Anthropic 地址应填写服务根地址，客户端会自行追加 `/v1/messages`；OpenAI 兼容地址通常
保留 `/v1`。

完整配置项及默认值见 [.env.example](.env.example)。

### 3. 推荐：启动前端版

```powershell
research-agent serve --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。前端与 FastAPI 同源提供，无需安装 Node.js 或执行单独的前端构建命令。

推荐从前端开始使用：

1. 填写研究主题、研究问题和检索偏好后发起研究。
2. `literature-scout` 默认直接进行外部检索；启用“优先检索本地文献库”后才会先复用本地论文。它会把长问题拆成多条互补短查询，同时检索 OpenAlex、Crossref、Semantic Scholar 和 arXiv。系统按 DOI/标题合并重复论文，保留每篇论文命中的来源和查询轨迹，再进行标题摘要级筛选；候选数量或单个来源失败不会阻止系统提交已有真实结果。
3. 在候选论文区查看 Agent 初筛意见，进行最终手筛，也可以补充检索词、手动加入 DOI 或排除论文。
4. 确认候选集后，系统立即进入精读模式，依次完成论文精读、综合、证据审查、综述提纲、分节写作和总编整合；完整综述生成后项目结束。
5. 在项目详情中查看状态事件和产物。产物默认以结构化 HTML 展示，也可切换到 JSON 原文。

每个子 Agent 开始工作前都会收到由已提交产物生成的共享记忆账本：任务账本说明当前
阶段和本次职责，进度账本压缩前序结论、章节衔接和审核意见，同时保留
`artifact_id`、`paper_id`、`evidence_id` 的来源关系。数据库产物始终是事实来源，
压缩记忆只作为有界的工作上下文，不会另存一份可能分叉的事实副本。

前端只会锁定当前对话的重复提交按钮；你仍可新建或切换到其他对话。`refine`、`accept` 和人工 `stop` 会追加新的候选快照，在后续研究运行开始前可通过补偿事件撤销，历史记录保持不变。

Swagger API 页面：<http://127.0.0.1:8000/docs>

#### 多用户与并发对话

Web 端现在把每次新建研究作为一个独立对话。对话、项目、运行记录、消息和用户归属均持久化到关系数据库 SQLite：

- 本机默认使用单用户共享模式：Codex 内置页面、Chrome 和 Edge 会自动映射到同一个本地用户，因此能看到相同的历史记录。
- 浏览器仍会获得只保存在 HttpOnly Cookie 中的随机会话令牌，数据库只保存令牌哈希。
- 如需真正的多人隔离，将 `RESEARCH_AGENT_MULTI_USER_MODE=true`；此时项目、对话、运行记录和个人文献数据会严格按 `user_id` 隔离。
- 每个对话使用独立的 `conversation_id`、`project_id` 和 LangGraph `thread_id`，因此状态和短期上下文不会串线。
- 同一个对话一次只允许一个活动任务；不同对话可以同时调研。切换对话或新建对话不会取消已在后台运行的任务。
- 服务异常重启时，未正常结束的运行会标记为 `interrupted`，项目和已经生成的产物仍可恢复。

这是本机单服务部署，SQLite 使用短事务、外键约束和 30 秒忙等待。若以后部署为多台 API 服务器，建议迁移到 PostgreSQL，并用数据库 Row-Level Security 作为额外的用户隔离边界。

### 4. 可选：使用命令行运行

```powershell
research-agent run "小样本遥感图像分类" `
  --question "哪些数据增强方法存在证据不足？" `
  --thread-id "remote-sensing-001"
```

参数说明：

- `topic`：必填的位置参数，表示研究主题。
- `--question`：具体研究问题；省略时使用内置默认问题。
- `--thread-id`：可选的 LangGraph 短期会话标识。同一长期运行进程中复用该值可以延续短期图状态。

CLI 每次执行都会启动新进程，因此 `InMemorySaver` 不会跨两次 CLI 命令保留；已经提交的项目、证据和状态持续保存在 SQLite 中。

运行过程中会实时显示模型调用、工具选择、检索结果、论文处理序号、阶段变化和降级信息。结束后会打印运行日志目录。

CLI 适合自动化调用和观察终端实时日志。初次检索得到候选论文后仍会停在 `SEARCH_REVIEW_PENDING`，候选集调整和确认建议在前端完成。

### 5. 可选：运行离线演示

```powershell
research-agent demo
```

离线演示不调用模型或外部检索 API，用于验证 SQLite、产物保存和状态机门禁是否正常。

### 6. 查询已保存项目

```powershell
research-agent status RP-20260715-example
```

请将示例 ID 替换为创建项目时返回的真实 `project_id`。

### 7. HTTP API

```powershell
research-agent serve --host 127.0.0.1 --port 8000
```

前端、API 和 CLI 共用同一个 SQLite 项目状态。人工审核的 `action` 只接受 `refine`、`accept` 和 `stop`。

主要接口：

```text
GET  /health
GET  /api/users/me
POST /api/conversations
GET  /api/conversations
GET  /api/conversations/{conversation_id}
POST /api/conversations/{conversation_id}/continue
GET  /api/runs/{run_id}
POST /api/research/invoke
POST /api/research/stream
GET  /api/projects
GET  /api/projects/{project_id}
GET  /api/projects/{project_id}/search-review
POST /api/projects/{project_id}/search-feedback
POST /api/projects/{project_id}/continue
GET  /api/library
GET  /api/library/overview
POST /api/library/papers
GET  /api/library/papers/{library_id}
PATCH /api/library/papers/{library_id}
DELETE /api/library/papers/{library_id}
POST /api/library/papers/{library_id}/restore
POST /api/library/papers/{library_id}/notes
POST /api/library/papers/{library_id}/attachments
POST /api/library/papers/{library_id}/attachments/upload
POST /api/library/collections
POST /api/library/bulk
GET  /api/library/duplicates
POST /api/library/merge
POST /api/library/compare
POST /api/library/assistant
POST /api/library/import
GET  /api/library/export?format=bibtex
GET  /api/projects/{project_id}/library
POST /api/projects/{project_id}/library
```

请求体示例：

```json
{
  "topic": "小样本遥感图像分类",
  "research_question": "哪些数据增强方法存在证据不足？",
  "thread_id": "remote-sensing-001"
}
```

`/api/research/stream` 使用 SSE 返回 `update`、`awaiting_input`、`done`、`fallback` 或 `error` 事件。初次检索得到候选论文后，项目进入 `SEARCH_REVIEW_PENDING` 并停止本轮 Agent 执行。

查询当前候选集：

```text
GET /api/projects/RP-.../search-review
```

补充检索词、加入论文、调整篇数/轮数限制或排除论文：

```json
{
  "action": "refine",
  "suggested_queries": ["few-shot remote sensing augmentation limitations"],
  "added_papers": [{"doi": "10.1000/example"}],
  "excluded_paper_ids": ["https://openalex.org/W123"],
  "max_search_rounds": 3
}
```

确认当前候选集：

```json
{
  "action": "accept"
}
```

确认后，筛选反馈接口会直接创建后续研究任务，前端无需再次确认。外部集成、旧的 `SCREENED` 项目或中断恢复仍可以手动调用：

```text
POST /api/projects/RP-.../continue
```

服务会从 SQLite 恢复已确认项目并从 `paper-reader` 阶段继续。继续阶段只读取最新 `ScreeningDecision.included_paper_ids`，不会精读被用户或 Agent 排除的候选论文。若管线意外中断，同一接口也会从 `EXTRACTED`、`SYNTHESIZED`、`REVIEW_PENDING`、`REVIEWED`、`OUTLINED` 或 `NARRATED` 恢复，并跳过已保存的工作。首次审查为 `REVISE` 时会在同一运行内自动返回综合阶段并复审一次；用户反馈、补充检索报告和每版候选集快照均以 append-only 产物保存。

`/continue` 接受 `SCREENED`、`EXTRACTED`、`SYNTHESIZED`、`REVIEW_PENDING`、`REVIEWED`、`OUTLINED` 和旧版本遗留的 `NARRATED` 项目。错误标记为 `COMPLETED`、但缺少完整综述的项目，也可通过该接口受控恢复；恢复会写入状态事件且不会重新检索。`INCONCLUSIVE` 仍是终态，已有 `PaperCard` 和 Evidence 会保留在 SQLite。

### 8. 文献库

前端侧栏的“文献库”保存跨项目共享的论文元数据。候选审核页可以单独收藏论文；人工确认纳入的论文会自动进入文献库。论文按 DOI、OpenAlex ID 或标题与年份去重，同一论文在不同研究项目中的纳入、排除和原因分别保存。

文献库工作台包含以下整理与精读能力：

- 通过全部、重点、未加入文件夹和回收站等智能视图快速筛选。
- 创建最多三层的嵌套文件夹树，并将同一篇论文放入一个或多个文件夹。每个一级、二级文件夹都可直接新建子文件夹；删除父文件夹后论文记录继续保留，直属子文件夹自动提升一级。
- 多选论文后批量修改重点标记和标签，加入文件夹或研究项目，导出所选 BibTeX/RIS，也可批量归档、恢复和永久删除。
- 在右侧详情中编辑标题、作者、年份、DOI、来源、链接、标签和摘要，记录可复用的阅读笔记，上传本地 PDF，或维护外部资料链接。单个上传文件上限为 30 MB。
- 汇总论文在不同项目中形成的 `PaperCard` 方法、数据集、发现和局限，保留每个项目自己的纳入状态与理由。
- 选择 2–8 篇论文进行横向对照，并向文献管理助手提问。模型可用时，助手基于选中材料生成带 `[1]`、`[2]` 来源标记的回答；模型不可用时返回摘要、笔记与证据摘录。
- 检查标题相近、作者和年份接近的疑似重复项。合并时会迁移文件夹、项目、笔记和附件关联。

打开旧项目时，服务会渐进建立论文关联并复用现有 Artifact，不会改写历史研究记录。文献库支持 BibTeX 和 RIS 导入导出；普通删除会将论文移入回收站，已有项目关联和研究产物继续保留。永久删除仅能从回收站执行。

### 9. 运行测试

```powershell
pytest -q
ruff check .
```

## 核心能力

- `research-supervisor` 统一接收 CLI 和 API 请求并控制科研状态主线。
- 七个窄工具子 Agent 覆盖检索、单篇阅读、综合、证据审查、提纲设计、分节写作和总编整合。
- OpenAlex、Crossref、开放 PDF 下载与本地 PDF 文本提取。
- Pydantic 结构化输出及 SQLite 原子产物提交。
- Python 状态机、Reviewer 门禁和 append-only 状态事件。
- 初次检索后暂停、候选论文人工增删、DOI 核验、多轮补充检索和跨进程继续。
- 跨项目文献库、论文去重、候选收藏、旧项目渐进关联和 BibTeX/RIS 互通。
- `InMemorySaver` 短期图状态、`ResearchRuntimeState` 子 Agent 交接状态和 `AGENTS.md` 长期规则。
- 模型或网络不可用时生成可追踪的 `RuntimeFallback`，不伪造文献、证据或结论。
- 前端 HTML/JSON 双视图、CLI 实时进度、完整运行日志、JSON 产物镜像和 Markdown 报告。

## 七个子 Agent

| Agent | 职责 | 实际可用 Tool | 结构化输出 | 运行保护 |
|---|---|---|---|---|
| `literature-scout` | 设计检索策略、标题摘要级初筛和覆盖分析 | `search_library`、`search_multi_source` | `SearchReport` | 多条短查询同时覆盖 OpenAlex、Crossref、Semantic Scholar 和 arXiv；系统捕获原始结果并重建 `candidates` |
| `paper-reader` | 获取开放全文或使用摘要提取单篇证据 | `fetch_paper_text`、`extract_pdf_text` | `PaperCard` | 全文请求受限；本地 PDF 最多解析一次；模型最多调用四次 |
| `research-synthesizer` | 跨论文比较并识别研究空白 | `get_active_research_project` | `SynthesisReport` | 最多两次工具调用，只能引用已保存 Evidence |
| `evidence-reviewer` | 审查结论、引文和 Evidence 对应关系 | `get_active_research_project` | `ReviewResult` | 项目最多读取一次；模型最多调用三次 |
| `research-outliner` | 根据论文卡片、综合结果和证据设计综述章节 | `get_active_research_project` | `ReviewOutline` | 仅在 `REVIEWED` 委派；最多两次工具调用 |
| `narrative-writer` | 按提纲逐节撰写连贯正文 | `get_active_research_project` | `SectionDraft` | 每次只写一个 `section_id`；草稿逐份保存 |
| `chief-editor` | 整合分节草稿、摘要、引言、结论和参考文献 | `get_active_research_project` | `NarrativeReview` | 仅在 `OUTLINED` 委派；提交后直接进入 `COMPLETED` |

主 Agent 使用完整的 `research-protocol` Skill。检索、阅读、综合和证据审查四个子 Agent 分别注入 `literature-search`、`paper-reading`、`research-synthesis` 和 `evidence-review` Skill；三个综述写作子 Agent 使用各自的专用 system prompt。所有子 Agent 都只获得表中列出的业务工具，并受结构化响应 schema、中间件和 Python 状态机约束。

`verify_doi` 未分配给任何 Agent，由 `SearchReviewService` 在用户手动添加 DOI 时调用。Reviewer 依据项目内的 `claim`、`evidence_id`、quote、page 和 section 审查证据对应关系。

## 状态机

```text
CREATED
  → SEARCHED
  → SEARCH_REVIEW_PENDING
      ├─ 用户补充查询、加入或排除论文 → 保持等待
      ├─ 用户确认 → SCREENED
      └─ 用户停止 → INCONCLUSIVE
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

`ReviewResult.PASS` 是进入综述写作阶段的门禁。`ReviewOutline` 提交后进入 `OUTLINED`；`NarrativeReview` 提交后直接进入 `COMPLETED`。所有状态变化都经过 `ResearchService → Repository → validate_transition`。
首次 `REVISE` 会自动返回 `EXTRACTED` 修订并复审一次；连续两次 `REVISE` 会保存 `RuntimeIssue` 并停在 `REVIEWED`，避免无限循环。

## 分层架构

```text
CLI / FastAPI
      ↓
ResearchSupervisor
      ├─ 主 Agent + research-protocol Skill
      └─ 七个窄化子 Agent
              ↓
       LangChain Tools
              ↓
       ResearchService
              ↓
       Repository Port
              ↑
       SQLite Repository
              ↓
       Domain Models + Workflow
```

源码目录：

```text
src/research_agent/
├── domain/                 # 数据契约和状态规则
├── application/            # 用例服务、Repository 接口和降级流程
├── infrastructure/         # 配置、SQLite、JSON导出、工作区和运行日志
├── tools/                  # Agent 可调用的项目、检索和PDF能力
├── skills/                 # 打包的操作规程
├── memories/AGENTS.md      # 长期身份和科研约束
├── agents/                 # Supervisor、子Agent注册、中间件和Prompt
├── api/                    # FastAPI、SSE 与本地可视化测试台
├── cli.py                  # CLI 入口
└── demo.py                 # 无模型离线验证
```

详细说明：

- [架构与依赖说明](docs/architecture.md)
- [主 Agent 与子 Agent 交互流程分析](docs/主Agent与子Agent交互流程分析.md)
- [运行进度、日志与导出产物](docs/runtime-observability.md)
- [故障诊断与当前限制](docs/troubleshooting.md)

## 检索与论文读取策略

默认预算：

```dotenv
RESEARCH_AGENT_MAX_PAPER_FETCHES_PER_PAPER=2
RESEARCH_AGENT_SEARCH_MAX_RETRIES=3
RESEARCH_AGENT_SEARCH_BACKOFF_SECONDS=1.0
RESEARCH_AGENT_SEARCH_MAX_RETRY_WAIT_SECONDS=30.0
RESEARCH_AGENT_MAX_SEARCH_REVIEW_ROUNDS=3
RESEARCH_AGENT_MAX_SUGGESTED_QUERIES_PER_ROUND=3
```

- OpenAlex/Crossref 遇到 429 时优先遵循 `Retry-After`，否则按指数退避等待。
- 搜索中间件保存 OpenAlex/Crossref 的原始返回；Scout 只生成候选 ID、三态筛选决定、理由、覆盖盲区和迭代日志，运行时据此重建完整 `SearchReport.candidates`。
- 搜索达到上限或部分请求失败时，Scout 使用已经取得的真实结果生成 `SearchReport`。
- `fetch_paper_text` 成功时直接返回带页码文本并缓存 PDF，无需再调用 `extract_pdf_text`。
- `extract_pdf_text` 只解析任务明确提供且已经存在于工作区的本地 PDF。
- 全文不可用但摘要存在时，Reader 可以生成标明 `section="abstract"`、`page=null` 的摘要级 Evidence。
- 全文与摘要都不可用时，`findings` 为空，并在 `limitations` 中说明证据缺失。

## 年份与期刊、会议信息

网页端在开始调研前提供发表年份硬筛选，允许选择 `2000-2026`，默认
`2024-2026`。年份范围会传给多源检索适配器，并在本地再次校验。

候选论文不会按场馆等级硬过滤。卡片仍会显示期刊或会议名称、CCF 评级、JCR
分区、影响因子及其数据年份，用于排序参考和人工判断。未可靠命中的来源会明确显示
“暂无可靠评级数据”。也可以通过 `GET /api/venues/lookup?q=TPAMI&venue_type=journal`
单独查询本地评级库。

评级数据在启动时导入同一个 SQLite 数据库，并通过规范化别名、精确匹配和全文候选重排完成快速检索。当前种子数据包括 CCF 2026 第七版全部 A 类会议和期刊、IEEE 官方 2026 title list 中的 Q1/Q2 期刊，以及 Nature Portfolio 官方 2025 指标页中的期刊：

- CCF 目录是计算领域的推荐目录，不等同于单篇论文质量评价。
- JCR 分区和影响因子属于特定年份、特定学科口径；界面始终展示数据年份。
- Nature 官方指标页未提供 JCR 分区时，只显示 Nature Portfolio 身份和官方影响因子，不补猜“一区”。
- 可运行 `python scripts/build_venue_seed.py --ccf-json <CCF_JSON> --ieee-pdf <IEEE_PDF>` 更新种子数据；生成文件位于 `src/research_agent/data/venue_rankings.json`。

## 运行时数据

默认数据目录：

```text
.research-agent/
├── research_agent.db
├── filesystem/
│   ├── papers/             # 开放PDF缓存
│   ├── skills/             # 启动时复制的Skills
│   └── memories/AGENTS.md  # 长期规则
├── runs/<run-id>/          # 单次运行日志和报告
└── outputs/<project-id>/   # 项目快照、产物JSON和最终报告
```

SQLite 保存权威业务事实；`outputs/` 是便于人工检查的镜像；`runs/` 记录每次模型与工具执行过程。

## 降级行为

模型初始化、认证、限流、连接超时或外部网络可用性异常可以进入离线降级。已有项目会被复用并写入 `RuntimeFallback`；没有项目时创建 `CREATED` 项目。

Pydantic 校验失败、非法状态迁移和缺少前置产物会返回给 Agent 或调用方处理，不会生成伪科研结果。

连续两份无效子 Agent 结果会保存为可恢复的 `RuntimeIssue`，项目保持在原阶段，不再被误标为“证据不足”并自动进入 `INCONCLUSIVE`。诊断时应读取 `RuntimeIssue.reason`、`events.jsonl` 中的首个 `artifact.commit_failed` 以及项目快照。当前数值校验会把 `2D`、`3DVG`、`FFL-3DOG` 等术语中的数字识别为数值 token，属于已知限制，详见[《故障诊断与当前限制》](docs/troubleshooting.md)。

## 测试范围

- 状态迁移、Reviewer 门禁和 `INCONCLUSIVE` 路径。
- SQLite 项目、状态事件、产物和 JSON 镜像。
- PDF 工作区路径边界、下载缓存及调用次数保护。
- Supervisor、窄化子 Agent、中间件和运行时状态。
- 运行日志、最终状态和 Markdown 报告。
- 人工检索审核、查询去重、DOI 加入、论文排除和跨请求续跑。
- FastAPI 路由、可视化测试台、SSE 和无密钥降级启动。
