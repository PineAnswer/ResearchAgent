# Research Agent

基于 Deep Agents、LangChain、LangGraph 和 Pydantic 的科研文献 Agent。系统通过检索、论文阅读、跨论文综合和证据审查生成可追踪的研究产物。

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
RESEARCH_AGENT_MODEL=openai:your-model-name
OPENAI_API_KEY=your-key
RESEARCH_AGENT_BASE_URL=https://your-provider.example/v1
```

完整配置项及默认值见 [.env.example](.env.example)。

### 3. 先运行离线演示

```powershell
research-agent demo
```

离线演示不调用模型或外部检索 API，用于验证 SQLite、产物保存和状态机门禁是否正常。

### 4. 运行科研 Agent

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

### 5. 查询已保存项目

```powershell
research-agent status RP-20260715-example
```

请将示例 ID 替换为创建项目时返回的真实 `project_id`。

### 6. 启动 HTTP API

```powershell
research-agent serve --host 127.0.0.1 --port 8000
```

可视化测试台：<http://127.0.0.1:8000/>

测试台支持发起新研究、浏览最近项目、查看候选论文、勾选排除、补充检索词、手动加入 DOI、确认候选集和继续后续研究。所有操作与 API、CLI 共用同一个 SQLite 项目状态；提交中的按钮会锁定，避免浏览器重复发送同一操作。

人工审核的 `action` 只接受 `refine`、`accept` 和 `stop`。论文排除、候选集确认和终止操作当前没有撤销接口；测试台会在提交前显示确认提示。多人同时测试同一项目时仍应避免并发修改。

Swagger API 页面：<http://127.0.0.1:8000/docs>

主要接口：

```text
GET  /health
POST /api/research/invoke
POST /api/research/stream
GET  /api/projects
GET  /api/projects/{project_id}
GET  /api/projects/{project_id}/search-review
POST /api/projects/{project_id}/search-feedback
POST /api/projects/{project_id}/continue
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

补充检索词、加入论文或排除论文：

```json
{
  "action": "refine",
  "suggested_queries": ["few-shot remote sensing augmentation limitations"],
  "added_papers": [{"doi": "10.1000/example"}],
  "excluded_paper_ids": ["https://openalex.org/W123"],
  "comment": "补充小样本场景，排除只讨论目标检测的论文。"
}
```

确认当前候选集：

```json
{
  "action": "accept",
  "comment": "确认当前候选论文，继续精读。"
}
```

确认后调用：

```text
POST /api/projects/RP-.../continue
```

服务会从 SQLite 恢复已确认项目并从 `paper-reader` 阶段继续。用户反馈、补充检索报告和每版候选集快照均以 append-only 产物保存。

`/continue` 只接受 `SCREENED` 项目。`INCONCLUSIVE` 是当前状态机的终态，已有 `PaperCard` 和 Evidence 仍保存在 SQLite，但当前 API 没有从 `EXTRACTED` 重新执行综合的恢复入口。

### 7. 运行测试

```powershell
pytest -q
ruff check .
```

## 核心能力

- `research-supervisor` 统一接收 CLI 和 API 请求并控制科研状态主线。
- 四个窄工具子 Agent 分别执行检索、单篇阅读、综合和证据审查。
- OpenAlex、Crossref、开放 PDF 下载与本地 PDF 文本提取。
- Pydantic 结构化输出及 SQLite 原子产物提交。
- Python 状态机、Reviewer 门禁和 append-only 状态事件。
- 初次检索后暂停、候选论文人工增删、DOI 核验、多轮补充检索和跨进程继续。
- `InMemorySaver` 短期图状态、`ResearchRuntimeState` 子 Agent 交接状态和 `AGENTS.md` 长期规则。
- 模型或网络不可用时生成可追踪的 `RuntimeFallback`，不伪造文献、证据或结论。
- CLI 实时进度、完整运行日志、JSON 产物镜像和 Markdown 报告。

## 四个子 Agent

| Agent | 职责 | 实际可用 Tool | 结构化输出 | 运行保护 |
|---|---|---|---|---|
| `literature-scout` | 检索、去重和候选筛选 | `search_openalex`；可选 `search_crossref` | `SearchReport` | 串行执行；检索次数受配置限制；每个项目只委派一次 |
| `paper-reader` | 获取开放全文或使用摘要提取单篇证据 | `fetch_paper_text`、`extract_pdf_text` | `PaperCard` | 全文请求受限；本地 PDF 最多解析一次；模型最多调用四次 |
| `research-synthesizer` | 跨论文比较并识别研究空白 | `get_active_research_project` | `SynthesisReport` | 最多两次工具调用，只能引用已保存 Evidence |
| `evidence-reviewer` | 审查结论、引文和 Evidence 对应关系 | `get_active_research_project` | `ReviewResult` | 项目最多读取一次；模型最多调用三次 |

主 Agent 和四个子 Agent 都在启动时读取并注入各自的完整 Skill：主 Agent 使用 `research-protocol`，四个子 Agent 分别使用 `literature-search`、`paper-reading`、`research-synthesis` 和 `evidence-review`。Skill 全文直接进入对应 system prompt，窄化子 Agent 仍然没有通用文件系统能力；工具权限、中间件、结构化响应 schema 和 Python 状态机继续提供硬边界。

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
      ├─ PASS   → COMPLETED
      └─ REVISE → EXTRACTED → 重新综合与审查

SEARCHED / SEARCH_REVIEW_PENDING / SCREENED / EXTRACTED / SYNTHESIZED / REVIEW_PENDING / REVIEWED
  └─ 证据不足或无法继续 → INCONCLUSIVE
```

所有状态变化都经过 `ResearchService → Repository → validate_transition`。只有 `COMPLETED + PASS` 表示科研项目正式完成；等待用户审核时运行结果为 `awaiting_input`，其他未闭环结果会标记为 `incomplete`、`needs_revision` 或 `inconclusive`。

## 分层架构

```text
CLI / FastAPI
      ↓
ResearchSupervisor
      ├─ 主 Agent + research-protocol Skill
      └─ 四个窄化子 Agent
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
RESEARCH_AGENT_MAX_OPENALEX_SEARCHES=3
RESEARCH_AGENT_MAX_CROSSREF_SEARCHES=1
RESEARCH_AGENT_MAX_PAPER_FETCHES_PER_PAPER=2
RESEARCH_AGENT_SEARCH_MAX_RETRIES=3
RESEARCH_AGENT_SEARCH_BACKOFF_SECONDS=1.0
RESEARCH_AGENT_SEARCH_MAX_RETRY_WAIT_SECONDS=30.0
RESEARCH_AGENT_MAX_SEARCH_REVIEW_ROUNDS=3
RESEARCH_AGENT_MAX_SUGGESTED_QUERIES_PER_ROUND=3
```

- OpenAlex/Crossref 遇到 429 时优先遵循 `Retry-After`，否则按指数退避等待。
- 搜索达到上限或部分请求失败时，Scout 使用已经取得的真实结果生成 `SearchReport`。
- `fetch_paper_text` 成功时直接返回带页码文本并缓存 PDF，无需再调用 `extract_pdf_text`。
- `extract_pdf_text` 只解析任务明确提供且已经存在于工作区的本地 PDF。
- 全文不可用但摘要存在时，Reader 可以生成标明 `section="abstract"`、`page=null` 的摘要级 Evidence。
- 全文与摘要都不可用时，`findings` 为空，并在 `limitations` 中说明证据缺失。

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

连续两份无效子 Agent 结果也会受控进入 `INCONCLUSIVE`。该状态既可能表示证据不足，也可能表示结构化输出或业务校验连续失败；诊断时应读取 `InsufficientEvidence.reason`、`events.jsonl` 中的首个 `artifact.commit_failed` 以及项目快照。当前数值校验会把 `2D`、`3DVG`、`FFL-3DOG` 等术语中的数字识别为数值 token，属于已知限制，详见[《故障诊断与当前限制》](docs/troubleshooting.md)。

## 测试范围

- 状态迁移、Reviewer 门禁和 `INCONCLUSIVE` 路径。
- SQLite 项目、状态事件、产物和 JSON 镜像。
- PDF 工作区路径边界、下载缓存及调用次数保护。
- Supervisor、窄化子 Agent、中间件和运行时状态。
- 运行日志、最终状态和 Markdown 报告。
- 人工检索审核、查询去重、DOI 加入、论文排除和跨请求续跑。
- FastAPI 路由、可视化测试台、SSE 和无密钥降级启动。
