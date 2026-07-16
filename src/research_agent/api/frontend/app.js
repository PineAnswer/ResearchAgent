"use strict";

const STAGES = [
  ["CREATED", "创建"],
  ["SEARCHED", "检索"],
  ["SEARCH_REVIEW_PENDING", "人工审核"],
  ["SCREENED", "候选确认"],
  ["EXTRACTED", "证据提取"],
  ["SYNTHESIZED", "综合"],
  ["REVIEW_PENDING", "等待审查"],
  ["REVIEWED", "审查完成"],
  ["OUTLINED", "提纲设计"],
  ["NARRATED", "综述已生成"],
  ["COMPLETED", "完成"],
];

const STAGE_LABELS = Object.fromEntries(STAGES);
STAGE_LABELS.INCONCLUSIVE = "证据不足";
STAGE_LABELS.OUTLINED = "提纲设计";
STAGE_LABELS.NARRATED = "综述已生成";

const ARTIFACT_LABELS = {
  SearchReport: "检索结果",
  SupplementalSearchReport: "补充检索结果",
  CandidateSetSnapshot: "候选集快照",
  SearchFeedback: "反馈与补搜",
  ScreeningDecision: "入选论文",
  PaperCard: "论文精读卡",
  SynthesisReport: "综合结论",
  ReviewResult: "证据审查",
  ReviewOutline: "综述提纲",
  SectionDraft: "章节草稿",
  NarrativeReview: "最终综述",
  FactCheckReport: "事实核查",
  InsufficientEvidence: "停止原因",
};

function artifactLabel(kind) {
  return ARTIFACT_LABELS[kind] || kind;
}

const state = {
  projects: [],
  projectId: null,
  project: null,
  snapshot: null,
  review: null,
  candidates: [],
  selectedIds: new Set(),
  busy: false,
  agentAvailable: false,
  toastTimer: null,
  artifactViewMode: "html",
  sidebarPreference: null,
  inspectorOpen: false,
  inspectorPreviousFocus: null,
};

const byId = (id) => document.getElementById(id);

const elements = {
  appShell: byId("appShell"),
  sidebarToggle: byId("sidebarToggle"),
  healthBadge: byId("healthBadge"),
  toolsMenuToggle: byId("toolsMenuToggle"),
  toolsMenu: byId("toolsMenu"),
  newProjectToggle: byId("newProjectToggle"),
  newProjectForm: byId("newProjectForm"),
  createView: byId("createView"),
  cancelNewProject: byId("cancelNewProject"),
  cancelNewProjectSecondary: byId("cancelNewProjectSecondary"),
  emptyNewProject: byId("emptyNewProject"),
  recentProjects: byId("recentProjects"),
  recentProjectList: byId("recentProjectList"),
  projectList: byId("projectList"),
  refreshProjects: byId("refreshProjects"),
  projectLookupForm: byId("projectLookupForm"),
  projectIdInput: byId("projectIdInput"),
  initialMinPapers: byId("initialMinPapers"),
  initialMaxPapers: byId("initialMaxPapers"),
  initialMaxSearchRounds: byId("initialMaxSearchRounds"),
  emptyState: byId("emptyState"),
  projectView: byId("projectView"),
  stageBadge: byId("stageBadge"),
  projectIdLabel: byId("projectIdLabel"),
  projectTopic: byId("projectTopic"),
  projectQuestion: byId("projectQuestion"),
  copyProjectId: byId("copyProjectId"),
  reloadProject: byId("reloadProject"),
  inspectorToggle: byId("inspectorToggle"),
  projectMenuToggle: byId("projectMenuToggle"),
  projectMenu: byId("projectMenu"),
  deleteProject: byId("deleteProject"),
  stageStepper: byId("stageStepper"),
  projectSummary: byId("projectSummary"),
  nextActionTitle: byId("nextActionTitle"),
  nextActionText: byId("nextActionText"),
  resultHighlights: byId("resultHighlights"),
  primaryOutcome: byId("primaryOutcome"),
  runPanel: byId("runPanel"),
  runStatusText: byId("runStatusText"),
  activityLog: byId("activityLog"),
  reviewPanel: byId("reviewPanel"),
  candidateCount: byId("candidateCount"),
  selectedCount: byId("selectedCount"),
  roundCount: byId("roundCount"),
  candidateFilter: byId("candidateFilter"),
  candidateGrid: byId("candidateGrid"),
  selectAll: byId("selectAll"),
  clearAll: byId("clearAll"),
  minPapers: byId("minPapers"),
  maxPapers: byId("maxPapers"),
  maxSearchRounds: byId("maxSearchRounds"),
  querySuggestions: byId("querySuggestions"),
  manualDois: byId("manualDois"),
  feedbackComment: byId("feedbackComment"),
  refineReview: byId("refineReview"),
  acceptReview: byId("acceptReview"),
  stopReview: byId("stopReview"),
  continuePanel: byId("continuePanel"),
  continueEyebrow: byId("continueEyebrow"),
  continueTitle: byId("continueTitle"),
  continueText: byId("continueText"),
  continueButtonLabel: byId("continueButtonLabel"),
  continueResearch: byId("continueResearch"),
  projectInspector: byId("projectInspector"),
  inspectorBackdrop: byId("inspectorBackdrop"),
  closeInspector: byId("closeInspector"),
  processTab: byId("processTab"),
  artifactsTab: byId("artifactsTab"),
  processPanel: byId("processPanel"),
  artifactsPanel: byId("artifactsPanel"),
  projectDetails: byId("projectDetails"),
  artifactSummary: byId("artifactSummary"),
  eventTimeline: byId("eventTimeline"),
  artifactList: byId("artifactList"),
  toast: byId("toast"),
};

const SIDEBAR_STORAGE_KEY = "research-agent.sidebar-state";

function iconNode(name) {
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", name);
  icon.setAttribute("aria-hidden", "true");
  return icon;
}

function refreshIcons() {
  if (window.lucide?.createIcons) window.lucide.createIcons();
}

function showWorkspace(view) {
  elements.emptyState.hidden = view !== "empty";
  elements.createView.hidden = view !== "create";
  elements.projectView.hidden = view !== "project";
}

function setPopover(toggle, popover, open) {
  popover.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
}

function closeMenus() {
  setPopover(elements.toolsMenuToggle, elements.toolsMenu, false);
  setPopover(elements.projectMenuToggle, elements.projectMenu, false);
}

function readSidebarPreference() {
  try {
    const value = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    return ["expanded", "collapsed"].includes(value) ? value : null;
  } catch {
    return null;
  }
}

function applySidebarState(value, persist = false) {
  const next = value === "collapsed" ? "collapsed" : "expanded";
  elements.appShell.dataset.sidebar = next;
  const expanded = next === "expanded";
  const label = expanded ? "收起项目侧栏" : "展开项目侧栏";
  elements.sidebarToggle.setAttribute("aria-expanded", String(expanded));
  elements.sidebarToggle.setAttribute("aria-label", label);
  elements.sidebarToggle.title = label;
  elements.sidebarToggle.replaceChildren(iconNode(expanded ? "panel-left-close" : "panel-left-open"));
  if (persist) {
    state.sidebarPreference = next;
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next);
    } catch {
      // The layout still works when browser storage is unavailable.
    }
  }
  refreshIcons();
}

function initializeSidebar() {
  state.sidebarPreference = readSidebarPreference();
  const initial = state.sidebarPreference || (window.innerWidth >= 1440 ? "expanded" : "collapsed");
  applySidebarState(initial);
  window.addEventListener("resize", () => {
    if (!state.sidebarPreference) {
      applySidebarState(window.innerWidth >= 1440 ? "expanded" : "collapsed");
    }
  });
}

function setInspectorTab(tab) {
  const showArtifacts = tab === "artifacts";
  elements.processTab.classList.toggle("is-active", !showArtifacts);
  elements.artifactsTab.classList.toggle("is-active", showArtifacts);
  elements.processTab.setAttribute("aria-selected", String(!showArtifacts));
  elements.artifactsTab.setAttribute("aria-selected", String(showArtifacts));
  elements.processPanel.hidden = showArtifacts;
  elements.artifactsPanel.hidden = !showArtifacts;
}

function openInspector(tab = "process") {
  if (!state.projectId) return;
  state.inspectorPreviousFocus = document.activeElement;
  state.inspectorOpen = true;
  setInspectorTab(tab);
  elements.projectInspector.classList.add("is-open");
  elements.projectInspector.setAttribute("aria-hidden", "false");
  elements.projectInspector.inert = false;
  elements.inspectorBackdrop.hidden = false;
  elements.inspectorToggle.setAttribute("aria-expanded", "true");
  window.setTimeout(() => elements.closeInspector.focus(), 0);
}

function closeInspector({ restoreFocus = true } = {}) {
  if (!state.inspectorOpen) return;
  state.inspectorOpen = false;
  elements.projectInspector.classList.remove("is-open");
  elements.projectInspector.setAttribute("aria-hidden", "true");
  elements.projectInspector.inert = true;
  elements.inspectorBackdrop.hidden = true;
  elements.inspectorToggle.setAttribute("aria-expanded", "false");
  if (restoreFocus && state.inspectorPreviousFocus instanceof HTMLElement) {
    state.inspectorPreviousFocus.focus();
  }
  state.inspectorPreviousFocus = null;
}

function candidateId(candidate) {
  return candidate.paper_id || candidate.doi || `title:${candidate.title || ""}`;
}

function normalizePaperId(value) {
  const raw = String(value || "").trim().replace(/[.,;，。；)]+$/, "");
  const match = raw.match(/(?:https?:\/\/)?(?:api\.)?openalex\.org\/(?:works\/)?(W\d+)/i);
  return match ? match[1].toUpperCase() : raw;
}

function numberInputValue(element, fallback) {
  const parsed = Number.parseInt(element?.value || "", 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function agentDecisionFor(snapshot, id) {
  if (!snapshot) return null;
  const normalizedId = normalizePaperId(id);
  if ((snapshot.agent_included_paper_ids || []).some((item) => normalizePaperId(item) === normalizedId)) return "include";
  if ((snapshot.agent_excluded_paper_ids || []).some((item) => normalizePaperId(item) === normalizedId)) return "exclude";
  if ((snapshot.agent_uncertain_paper_ids || []).some((item) => normalizePaperId(item) === normalizedId)) return "uncertain";
  return null;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function parseList(value, splitComma = false) {
  const pattern = splitComma ? /[\n,，;；]+/ : /\n+/;
  return [...new Set(value.split(pattern).map((item) => item.trim()).filter(Boolean))];
}

function safeHttpUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function errorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || JSON.stringify(item))
      .filter(Boolean)
      .join("；");
  }
  return payload?.message || fallback;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { message: text };
  }
  if (!response.ok) {
    throw new Error(errorMessage(payload, `请求失败（HTTP ${response.status}）`));
  }
  return payload;
}

function notify(message, isError = false) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4200);
}

function setBusy(busy) {
  state.busy = busy;
  [
    elements.refineReview,
    elements.acceptReview,
    elements.stopReview,
    elements.reloadProject,
    elements.deleteProject,
  ].forEach((button) => {
    button.disabled = busy;
  });
  elements.continueResearch.disabled =
    busy || (Boolean(continuationMode(state.snapshot)) && !state.agentAvailable);
}

function setHealth(data) {
  state.agentAvailable = Boolean(data?.agent_available);
  elements.healthBadge.classList.remove("is-checking", "is-error");
  if (data?.status === "ok") {
    elements.healthBadge.innerHTML = '<span class="status-dot" aria-hidden="true"></span>服务正常';
    return;
  }
  elements.healthBadge.classList.add("is-error");
  elements.healthBadge.innerHTML = '<span class="status-dot" aria-hidden="true"></span>服务降级';
  if (data?.initialization_error) {
    elements.healthBadge.title = data.initialization_error;
  }
}

async function checkHealth() {
  try {
    const payload = await api("/health");
    setHealth(payload.data);
  } catch (error) {
    elements.healthBadge.classList.remove("is-checking");
    elements.healthBadge.classList.add("is-error");
    elements.healthBadge.innerHTML = '<span class="status-dot" aria-hidden="true"></span>连接失败';
    elements.healthBadge.title = error.message;
  }
}

function renderRecentProjects() {
  elements.recentProjectList.replaceChildren();
  elements.recentProjects.hidden = !state.projects.length;
  state.projects.slice(0, 3).forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "start-recent-item";
    button.setAttribute("aria-label", `打开研究：${project.topic || "未命名研究"}`);

    const copy = document.createElement("span");
    copy.className = "start-recent-copy";
    const title = document.createElement("strong");
    title.textContent = project.topic || "未命名研究";
    const meta = document.createElement("span");
    meta.textContent = `${STAGE_LABELS[project.stage] || project.stage} · ${formatDate(project.updated_at)}`;
    copy.append(title, meta);

    button.append(copy, iconNode("arrow-right"));
    button.addEventListener("click", () => loadProject(project.project_id));
    elements.recentProjectList.append(button);
  });
  refreshIcons();
}

function renderProjectList() {
  elements.projectList.replaceChildren();
  renderRecentProjects();
  if (!state.projects.length) {
    const empty = document.createElement("p");
    empty.className = "muted small sidebar-label";
    empty.textContent = "还没有项目记录";
    elements.projectList.append(empty);
    return;
  }

  state.projects.forEach((project) => {
    const displayStage =
      project.project_id === state.projectId && continuationMode(state.snapshot) === "recovery"
        ? recoverableOperationalFailure(state.snapshot)
          ? "写作待恢复"
          : "成果待补全"
        : STAGE_LABELS[project.stage] || project.stage;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-list-item";
    button.classList.toggle("is-active", project.project_id === state.projectId);
    button.setAttribute("aria-current", project.project_id === state.projectId ? "page" : "false");
    button.setAttribute("aria-label", `${project.topic || "未命名研究"}，${displayStage}`);
    button.title = project.topic || "未命名研究";

    const icon = document.createElement("span");
    icon.className = "project-list-icon";
    icon.append(iconNode(project.project_id === state.projectId ? "folder-open" : "folder"));

    const content = document.createElement("span");
    content.className = "project-list-content";

    const title = document.createElement("span");
    title.className = "project-list-title";
    title.textContent = project.topic || "未命名研究";

    const meta = document.createElement("span");
    meta.className = "project-list-meta";
    const stage = document.createElement("span");
    stage.textContent = displayStage;
    const date = document.createElement("span");
    date.textContent = formatDate(project.updated_at);
    meta.append(stage, date);

    content.append(title, meta);
    button.append(icon, content);
    button.addEventListener("click", () => loadProject(project.project_id));
    elements.projectList.append(button);
  });
  refreshIcons();
}

async function loadProjects() {
  try {
    const payload = await api("/api/projects?limit=30");
    state.projects = payload.data || [];
    renderProjectList();
  } catch (error) {
    const message = document.createElement("p");
    message.className = "muted small sidebar-label";
    message.textContent = `项目载入失败：${error.message}`;
    elements.projectList.replaceChildren(message);
  }
}

function clearProjectView() {
  state.projectId = null;
  state.project = null;
  state.snapshot = null;
  state.review = null;
  state.candidates = [];
  state.selectedIds = new Set();
  closeInspector({ restoreFocus: false });
  closeMenus();
  showWorkspace("empty");
  elements.projectIdInput.value = "";
  window.history.replaceState({}, "", window.location.pathname);
  renderProjectList();
}

async function deleteCurrentProject() {
  if (!state.projectId || state.busy) return;
  closeMenus();
  const projectId = state.projectId;
  const topic = state.project?.topic || "未命名研究";
  const confirmed = window.confirm(
    `确定永久删除“${topic}”吗？\n\n项目、研究产物和状态记录都会被删除，此操作无法撤销。`,
  );
  if (!confirmed) return;

  setBusy(true);
  try {
    await api(`/api/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    state.projects = state.projects.filter((project) => project.project_id !== projectId);
    clearProjectView();
    await loadProjects();
    notify("研究项目已删除");
  } catch (error) {
    notify(`删除失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function stageIndex(stage) {
  return STAGES.findIndex(([key]) => key === stage);
}

function renderStepper(stage) {
  elements.stageStepper.replaceChildren();
  const current = stageIndex(stage);
  STAGES.forEach(([key, label], index) => {
    const item = document.createElement("li");
    item.className = "stage-step";
    if (current >= 0 && index < current) item.classList.add("is-complete");
    if (index === current) item.classList.add("is-current");
    item.textContent = label;
    elements.stageStepper.append(item);
  });
}

function renderProjectHeader(project) {
  state.project = project;
  state.projectId = project.project_id;
  showWorkspace("project");
  elements.projectIdLabel.textContent = project.project_id;
  elements.projectTopic.textContent = project.topic || "未命名研究";
  elements.projectQuestion.textContent = project.research_question || "";
  elements.stageBadge.textContent = STAGE_LABELS[project.stage] || project.stage;
  elements.stageBadge.className = "stage-badge";
  if (["COMPLETED", "REVIEWED", "NARRATED"].includes(project.stage)) {
    elements.stageBadge.classList.add("is-done");
  }
  if (project.stage === "INCONCLUSIVE") {
    elements.stageBadge.classList.add("is-terminal");
  }
  renderStepper(project.stage);
  renderProjectList();
  refreshIcons();
}

// ── Artifact HTML renderers ──────────────────────────────────────────

function renderSearchReportHTML(payload) {
  const parts = [];
  if (payload.query) {
    parts.push(h('div', {cls:'aw-row'}, [h('span',{cls:'aw-label'},'检索主题'), h('code',{},payload.query)]));
  }
  const terms = payload.search_terms || [];
  if (terms.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'执行查询'),
      h('ul',{cls:'aw-tags'}, terms.map(t => h('li',{cls:'aw-tag'},t)))
    ]));
  }
  const candidates = payload.candidates || [];
  parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'候选论文'), h('span',{cls:'aw-badge'},`${candidates.length} 篇`)]));
  const decisions = payload.screening_decisions || {};
  const dk = Object.keys(decisions);
  if (dk.length) {
    const inc = dk.filter(k => decisions[k]==='include').length;
    const exc = dk.filter(k => decisions[k]==='exclude').length;
    const unc = dk.filter(k => decisions[k]==='uncertain').length;
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'初筛结果'),
      h('div',{cls:'aw-inline'}, [
        h('span',{cls:'aw-pill inc'},`纳入 ${inc}`),
        h('span',{cls:'aw-pill exc'},`排除 ${exc}`),
        h('span',{cls:'aw-pill unc'},`待定 ${unc}`),
      ])
    ]));
  }
  const reasons = payload.screening_reasons || {};
  const rk = Object.keys(reasons);
  if (rk.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'筛选理由'),
      h('ul',{cls:'aw-reasons'}, rk.slice(0,12).map(id => h('li',{}, [h('code',{},id), h('span',{},reasons[id])])))
    ]));
  }
  const gaps = payload.coverage_gaps || [];
  if (gaps.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'覆盖盲区'),
      h('ul',{cls:'aw-list'}, gaps.map(g => h('li',{},g)))
    ]));
  }
  const log = payload.search_iteration_log || [];
  if (log.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'检索迭代'),
      h('div',{}, log.map((entry,i) => h('div',{cls:'aw-iter'}, [
        h('span',{cls:'aw-iter-idx'},`#${i+1}`),
        h('code',{},entry.query||''),
        h('span',{cls:'aw-iter-meta'},`命中 ${entry.count||0} 篇` + (entry.rationale ? ` — ${entry.rationale}` : ''))
      ])))
    ]));
  }
  const notes = payload.selection_notes || [];
  if (notes.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'筛选说明'),
      h('ul',{cls:'aw-list'}, notes.map(n => h('li',{},n)))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderPaperCardHTML(payload) {
  const parts = [];
  if (payload.title) {
    parts.push(h('h4',{cls:'aw-card-title'},payload.title));
  }
  if (payload.research_question) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'研究问题'), h('span',{},payload.research_question)]));
  }
  const methods = payload.methods || [];
  if (methods.length) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'方法'), h('ul',{cls:'aw-tags'}, methods.map(m => h('li',{cls:'aw-tag'},m)))]));
  }
  const datasets = payload.datasets || [];
  if (datasets.length) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'数据集'), h('ul',{cls:'aw-tags'}, datasets.map(d => h('li',{cls:'aw-tag'},d)))]));
  }
  const findings = payload.findings || [];
  if (findings.length) {
    const rows = findings.map(f => h('tr',{}, [
      h('td',{cls:'aw-ev-id'},h('code',{},f.evidence_id||'')),
      h('td',{},f.claim||''),
      h('td',{cls:'aw-ev-quote'},h('q',{},(f.quote||'').slice(0,200) + ((f.quote||'').length>200?'…':''))),
      h('td',{cls:'aw-ev-src'},[f.section||'', f.page ? ` p.${f.page}` : ''].filter(Boolean).join(' ')),
    ]));
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},`证据（${findings.length} 条）`),
      h('div',{cls:'aw-table-wrap'}, h('table',{cls:'aw-table'}, [
        h('thead',{}, h('tr',{}, [h('th',{},'ID'), h('th',{},'结论'), h('th',{},'原文'), h('th',{},'出处')])),
        h('tbody',{}, rows),
      ]))
    ]));
  }
  const limitations = payload.limitations || [];
  if (limitations.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'局限'),
      h('ul',{cls:'aw-list'}, limitations.map(l => h('li',{},l)))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderSynthesisReportHTML(payload) {
  const parts = [];
  if (payload.topic) {
    parts.push(h('h4',{cls:'aw-card-title'},payload.topic));
  }
  const sections = [
    ['共识结论', 'consensus'],
    ['冲突与争议', 'conflicts'],
    ['方法比较', 'method_comparison'],
  ];
  sections.forEach(([label, key]) => {
    const items = payload[key] || [];
    if (!items.length) return;
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},`${label}（${items.length}）`),
      h('ol',{cls:'aw-claims'}, items.map(c => h('li',{}, [
        h('p',{},c.statement||''),
        h('div',{cls:'aw-ev-refs'}, (c.evidence_ids||[]).map(eid => h('code',{cls:'aw-ev-ref'},eid))),
      ])))
    ]));
  });
  const gaps = payload.gaps || [];
  if (gaps.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},`研究空白（${gaps.length}）`),
      h('div',{}, gaps.map(g => h('div',{cls:'aw-gap'}, [
        h('div',{cls:'aw-gap-head'}, [
          h('span',{cls:`aw-conf conf-${(g.confidence||'low').toLowerCase()}`},g.confidence||'LOW'),
          h('p',{},g.description||''),
        ]),
        h('p',{cls:'aw-gap-hypo'},h('em',{},`假设: ${g.proposed_hypothesis||''}`)),
        h('div',{cls:'aw-ev-refs'}, [
          h('span',{cls:'muted'},'支持: '),
          ...(g.supporting_paper_ids||[]).map(pid => h('code',{cls:'aw-ev-ref'},pid)),
          ...(g.conflicting_paper_ids||[]).length ? [h('span',{cls:'muted'},' 冲突: '), ...(g.conflicting_paper_ids||[]).map(pid => h('code',{cls:'aw-ev-ref warn'},pid))] : [],
        ]),
      ])))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderReviewResultHTML(payload) {
  const parts = [];
  const verdict = payload.verdict || '';
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'审查结论'),
    h('span',{cls:`aw-verdict ${verdict==='PASS'?'pass':'revise'}`}, verdict==='PASS' ? '通过' : '需修订'),
  ]));
  const fatal = payload.fatal_issues || [];
  if (fatal.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'严重问题'),
      h('ul',{cls:'aw-list issues'}, fatal.map(f => h('li',{},f)))
    ]));
  }
  const suggestions = payload.suggestions || [];
  if (suggestions.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'修订建议'),
      h('ol',{cls:'aw-list'}, suggestions.map(s => h('li',{},s)))
    ]));
  }
  const verified = payload.verified_evidence_ids || [];
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},`已验证证据（${verified.length}）`),
    verified.length ? h('div',{cls:'aw-inline'}, verified.map(eid => h('code',{cls:'aw-ev-ref ok'},eid))) : h('span',{cls:'muted'},'无'),
  ]));
  return h('div',{cls:'artifact-html'}, parts);
}

function renderScreeningDecisionHTML(payload) {
  const parts = [];
  const included = payload.included_paper_ids || [];
  const excluded = payload.excluded_paper_ids || [];
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'筛选结果'),
    h('div',{cls:'aw-inline'}, [
      h('span',{cls:'aw-pill inc'},`纳入 ${included.length} 篇`),
      h('span',{cls:'aw-pill exc'},`排除 ${excluded.length} 篇`),
    ])
  ]));
  if (included.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'纳入论文'),
      h('div',{cls:'aw-inline'}, included.map(id => h('code',{cls:'aw-ev-ref'},id)))
    ]));
  }
  const reasons = payload.reasons || [];
  if (reasons.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'筛选理由'),
      h('ul',{cls:'aw-list'}, reasons.map(r => h('li',{},r)))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderCandidateSetSnapshotHTML(payload) {
  const parts = [];
  const candidates = payload.candidates || [];
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'候选集'),
    h('div',{cls:'aw-inline'}, [
      h('span',{cls:'aw-badge'},`${candidates.length} 篇候选`),
      h('span',{cls:'aw-badge'},`${(payload.excluded_paper_ids||[]).length} 篇已排除`),
    ])
  ]));
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'检索轮次'),
    h('span',{},`第 ${payload.search_round||0} / ${payload.max_search_rounds||3} 轮`)
  ]));
  const queries = payload.executed_queries || [];
  if (queries.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'已执行查询'),
      h('ul',{cls:'aw-list'}, queries.map(q => h('li',{},q)))
    ]));
  }
  const comments = payload.user_comments || [];
  if (comments.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'用户备注'),
      h('ul',{cls:'aw-list'}, comments.map(c => h('li',{},c)))
    ]));
  }
  const failures = payload.search_failures || [];
  if (failures.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label warn'},'检索失败'),
      h('ul',{cls:'aw-list issues'}, failures.map(f => h('li',{},f)))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderInsufficientEvidenceHTML(payload) {
  const parts = [];
  if (payload.reason) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'原因'), h('p',{cls:'aw-text'},payload.reason)]));
  }
  const queries = payload.queries_attempted || [];
  if (queries.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'尝试的查询'),
      h('ul',{cls:'aw-list'}, queries.map(q => h('li',{},q)))
    ]));
  }
  const failures = payload.search_failures || [];
  if (failures.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label warn'},'失败'),
      h('ul',{cls:'aw-list issues'}, failures.map(f => h('li',{},f)))
    ]));
  }
  if (payload.recommendation) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'建议'), h('p',{cls:'aw-text'},payload.recommendation)]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderSearchFeedbackHTML(payload) {
  const parts = [];
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'操作'),
    h('span',{cls:'aw-badge'},payload.action||'')
  ]));
  const queries = payload.suggested_queries || [];
  if (queries.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'补充查询'),
      h('ul',{cls:'aw-tags'}, queries.map(q => h('li',{cls:'aw-tag'},q)))
    ]));
  }
  const added = payload.added_papers || [];
  if (added.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},`手动加入（${added.length} 篇）`),
      h('ul',{cls:'aw-list'}, added.map(p => h('li',{},p.doi||p.paper_id||p.title||'未知')))
    ]));
  }
  if (payload.comment) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'备注'), h('p',{cls:'aw-text'},payload.comment)]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderReviewOutlineHTML(payload) {
  const parts = [];
  if (payload.title) {
    parts.push(h('h4',{cls:'aw-card-title'},payload.title));
  }
  if (payload.narrative_arc) {
    parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'叙事线'), h('p',{cls:'aw-text'},payload.narrative_arc)]));
  }
  const sections = payload.sections || [];
  if (sections.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},`章节（${sections.length}）`),
      h('ol',{cls:'aw-outline'}, sections.map((s,i) => h('li',{}, [
        h('strong',{},`${s.heading||s.section_id||''}`),
        h('div',{cls:'aw-outline-meta'}, [
          h('span',{},`目标 ${s.target_words||300} 词`),
          h('span',{},`${(s.assigned_paper_ids||[]).length} 篇论文`),
          h('span',{},`${(s.assigned_evidence_ids||[]).length} 条证据`),
        ]),
        (s.key_claims||[]).length ? h('ul',{cls:'aw-list'}, s.key_claims.map(c => h('li',{},c))) : null,
      ])))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

const MARKDOWN_ALLOWED_TAGS = new Set([
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
]);

const MARKDOWN_BLOCKED_TAGS = new Set([
  "embed",
  "form",
  "iframe",
  "math",
  "object",
  "script",
  "style",
  "svg",
  "template",
]);

const MARKDOWN_PARSER = (() => {
  if (!window.marked?.Marked) return null;
  const parser = new window.marked.Marked();
  parser.use({
    tokenizer: {
      html(source) {
        if (!source.startsWith("<")) return false;
        return { type: "text", raw: "<", text: "&lt;" };
      },
    },
  });
  return parser;
})();

function safeMarkdownHref(value) {
  try {
    const url = new URL(String(value || "").trim(), window.location.href);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url : null;
  } catch {
    return null;
  }
}

function cloneMarkdownChildren(node) {
  const fragment = document.createDocumentFragment();
  node.childNodes.forEach((child) => {
    const safeChild = cloneMarkdownNode(child);
    if (safeChild) fragment.append(safeChild);
  });
  return fragment;
}

function cloneMarkdownNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    return document.createTextNode(node.textContent || "");
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return null;

  const tag = node.tagName.toLowerCase();
  if (MARKDOWN_BLOCKED_TAGS.has(tag)) return null;
  if (!MARKDOWN_ALLOWED_TAGS.has(tag)) return cloneMarkdownChildren(node);

  if (tag === "a") {
    const href = safeMarkdownHref(node.getAttribute("href"));
    if (!href) return cloneMarkdownChildren(node);
    const link = document.createElement("a");
    link.href = href.href;
    const title = node.getAttribute("title");
    if (title) link.title = title;
    if (["http:", "https:"].includes(href.protocol) && href.origin !== window.location.origin) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    link.append(cloneMarkdownChildren(node));
    return link;
  }

  const clean = document.createElement(tag);
  if (tag === "code") {
    const languageClass = [...node.classList].find((name) =>
      /^language-[a-z0-9_-]+$/i.test(name),
    );
    if (languageClass) clean.classList.add(languageClass);
  }
  if (tag === "ol") {
    const start = Number.parseInt(node.getAttribute("start") || "", 10);
    if (Number.isInteger(start) && start > 1) clean.setAttribute("start", String(start));
  }
  if (["td", "th"].includes(tag)) {
    const align = node.getAttribute("align");
    if (["left", "center", "right"].includes(align)) clean.setAttribute("align", align);
  }
  clean.append(cloneMarkdownChildren(node));
  return clean;
}

function sanitizedMarkdownFragment(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  return cloneMarkdownChildren(template.content);
}

function renderMarkdown(markdown, className = "aw-markdown") {
  const container = h("div", { cls: className });
  const source = String(markdown || "").trim();
  if (!source) return container;

  try {
    if (!MARKDOWN_PARSER) throw new Error("Markdown parser unavailable");
    const rendered = MARKDOWN_PARSER.parse(source, {
      async: false,
      breaks: false,
      gfm: true,
    });
    container.append(sanitizedMarkdownFragment(rendered));
  } catch {
    source.split(/\n{2,}/).forEach((paragraph) => {
      container.append(h("p", { cls: "aw-para" }, paragraph));
    });
  }
  return container;
}

function stripDuplicateLeadingMarkdownHeading(markdown, heading) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const firstContentLine = lines.findIndex((line) => line.trim());
  if (firstContentLine < 0) return "";

  const match = lines[firstContentLine].match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
  if (!match) return lines.join("\n");
  const plainHeading = match[1]
    .replace(/\[([^\]]+)]\([^)]+\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .trim();
  if (plainHeading !== String(heading || "").trim()) return lines.join("\n");

  lines.splice(firstContentLine, 1);
  while (lines[firstContentLine] !== undefined && !lines[firstContentLine].trim()) {
    lines.splice(firstContentLine, 1);
  }
  return lines.join("\n");
}

function renderSectionDraftHTML(payload) {
  const parts = [];
  if (payload.heading) {
    parts.push(h('h4',{cls:'aw-card-title'},payload.heading));
  }
  if (payload.transition_from) {
    parts.push(h('div',{cls:'aw-transition'}, [h('span',{cls:'aw-label'},'接上文'), h('p',{},payload.transition_from)]));
  }
  if (payload.content) {
    parts.push(
      renderMarkdown(
        stripDuplicateLeadingMarkdownHeading(payload.content, payload.heading),
        "aw-content aw-markdown",
      ),
    );
  }
  if (payload.transition_to) {
    parts.push(h('div',{cls:'aw-transition'}, [h('span',{cls:'aw-label'},'启下文'), h('p',{},payload.transition_to)]));
  }
  const cited = payload.cited_evidence || [];
  if (cited.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},'引用证据'),
      h('div',{cls:'aw-inline'}, cited.map(eid => h('code',{cls:'aw-ev-ref'},eid)))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

let narrativeRenderSequence = 0;

function renderNarrativeReviewHTML(payload) {
  narrativeRenderSequence += 1;
  const anchorPrefix = `review-${narrativeRenderSequence}`;
  const parts = [];
  // Title + abstract
  if (payload.title) {
    parts.push(h('h3',{cls:'aw-review-title'},payload.title));
  }
  if (payload.abstract) {
    parts.push(h('div',{cls:'aw-abstract'}, [
      h('strong',{},'摘要'),
      renderMarkdown(payload.abstract, "aw-markdown aw-abstract-body"),
    ]));
  }
  // Meta
  parts.push(h('div',{cls:'aw-review-meta'}, [
    h('span',{},`${payload.writing_style||'academic-survey'}`),
    h('span',{},`约 ${payload.word_count||0} 词`),
    h('span',{},`${(payload.references||[]).length} 篇参考文献`),
  ]));
  // Sections with TOC
  const sections = payload.sections || [];
  if (sections.length > 1) {
    parts.push(h('div',{cls:'aw-toc'}, [
      h('strong',{},'目录'),
      h('ol',{}, sections.map((s, index) => h('li',{}, h('a',{href:`#${anchorPrefix}-${s.section_id||index}`},s.heading||'')))),
    ]));
  }
  // Section bodies
  sections.forEach((s, index) => {
    const cited = (s.cited_evidence||[]);
    parts.push(h('div',{cls:'aw-section', id:`${anchorPrefix}-${s.section_id||index}`}, [
      h('h4',{cls:'aw-section-heading'},s.heading||''),
      renderMarkdown(
        stripDuplicateLeadingMarkdownHeading(s.content || "", s.heading || ""),
        "aw-content aw-markdown",
      ),
      cited.length ? h('div',{cls:'aw-cited'}, [
        h('span',{cls:'muted'},'引用: '),
        ...cited.map(eid => h('code',{cls:'aw-ev-ref'},eid)),
      ]) : null,
      // Subsections
      ...(s.subsections||[]).map(sub => h('div',{cls:'aw-subsection'}, [
        h('h5',{},sub.heading||''),
        renderMarkdown(
          stripDuplicateLeadingMarkdownHeading(sub.content || "", sub.heading || ""),
          "aw-content aw-markdown",
        ),
      ])),
    ]));
  });
  // References
  const refs = payload.references || [];
  if (refs.length) {
    parts.push(h('div',{cls:'aw-refs'}, [
      h('h4',{cls:'aw-section-heading'},'参考文献'),
      h('ol',{cls:'aw-ref-list'}, refs.map((r,i) => h('li',{id:`${anchorPrefix}-ref-${i+1}`}, [
        h('span',{cls:'aw-ref-text'},r.text||r.paper_id||''),
        r.bibtex ? h('details',{cls:'aw-bibtex'}, [
          h('summary',{},'BibTeX'),
          h('pre',{},r.bibtex),
        ]) : null,
      ]))),
    ]));
  }
  return h('div',{cls:'artifact-html narrative-review'}, parts);
}

function renderFactCheckReportHTML(payload) {
  const parts = [];
  const verdict = payload.verdict || '';
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'核查结论'),
    h('span',{cls:`aw-verdict ${verdict==='PASS'?'pass':'revise'}`}, verdict==='PASS' ? '通过' : '需修订'),
  ]));
  parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'章节'), h('code',{},payload.section_id||'')]));
  const issues = payload.issues || [];
  if (issues.length) {
    parts.push(h('div',{cls:'aw-row'}, [
      h('span',{cls:'aw-label'},`问题（${issues.length}）`),
      h('div',{}, issues.map(iss => h('div',{cls:'aw-fc-issue'}, [
        h('p',{cls:'aw-fc-claim'}, h('q',{}, iss.claim||'')),
        h('div',{cls:'aw-fc-meta'}, [
          h('code',{cls:'aw-ev-ref warn'}, iss.evidence_id||''),
          h('span',{cls:'aw-pill exc'}, iss.problem||''),
        ]),
        iss.correction ? h('p',{cls:'aw-fc-fix'}, `建议: ${iss.correction}`) : null,
      ])))
    ]));
  }
  return h('div',{cls:'artifact-html'}, parts);
}

function renderGenericArtifactHTML(payload) {
  return h('div',{cls:'artifact-html'}, [
    h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'内容'), h('pre',{cls:'aw-pre'}, JSON.stringify(payload, null, 2))])
  ]);
}

const ARTIFACT_HTML_RENDERERS = {
  SearchReport: renderSearchReportHTML,
  SupplementalSearchReport: renderSearchReportHTML,
  PaperCard: renderPaperCardHTML,
  SynthesisReport: renderSynthesisReportHTML,
  ReviewResult: renderReviewResultHTML,
  ScreeningDecision: renderScreeningDecisionHTML,
  CandidateSetSnapshot: renderCandidateSetSnapshotHTML,
  InsufficientEvidence: renderInsufficientEvidenceHTML,
  SearchFeedback: renderSearchFeedbackHTML,
  ReviewOutline: renderReviewOutlineHTML,
  SectionDraft: renderSectionDraftHTML,
  NarrativeReview: renderNarrativeReviewHTML,
  FactCheckReport: renderFactCheckReportHTML,
};

// ── Tiny DOM builder ────────────────────────────────────────────────

function h(tag, attrs, children) {
  const el = document.createElement(tag);
  if (attrs) {
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'cls') { el.className = v; return; }
      if (k === 'for') { el.setAttribute('for', v); return; }
      if (k.startsWith('on')) { el.addEventListener(k.slice(2).toLowerCase(), v); return; }
      el.setAttribute(k, v);
    });
  }
  if (children) {
    (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      el.append(typeof c === 'string' ? document.createTextNode(c) : c);
    });
  }
  return el;
}

const USER_STAGE_GUIDANCE = {
  CREATED: ["准备检索", "Agent 正在准备研究项目，下一步会开始查找相关论文。"],
  SEARCHED: ["已找到候选论文", "系统已完成初步检索，正在生成可供你审核的候选集。"],
  SEARCH_REVIEW_PENDING: ["请审核候选论文", "勾选需要精读的论文；你也可以补充检索词或手动加入 DOI。"],
  SCREENED: ["可以开始精读", "候选集已经确认，点击继续研究后将逐篇读取论文并提取证据。"],
  EXTRACTED: ["证据已提取", "论文精读已经完成，下一步会综合比较证据。"],
  SYNTHESIZED: ["综合完成", "系统已经形成共识、冲突和研究空白，等待独立审查。"],
  REVIEW_PENDING: ["等待证据审查", "系统将检查综合结论是否都有证据支撑。"],
  REVIEWED: ["证据审查通过", "证据链已经通过审查，下一步生成文献综述大纲。"],
  OUTLINED: ["大纲已生成", "系统已规划章节结构，下一步逐节撰写正文。"],
  NARRATED: ["综述已生成", "完整综述已经生成，接下来进行事实核查。"],
  COMPLETED: ["研究已完成", "最终综述已生成并完成事实核查，可查看下方成果。"],
  INCONCLUSIVE: ["研究已停止", "系统认为证据不足或流程遇到阻断，请查看原因和建议。"],
};

function artifactsOf(snapshot, kind) {
  return (snapshot?.artifacts || []).filter((artifact) => artifact.kind === kind);
}

function latestArtifact(snapshot, kind) {
  const matches = artifactsOf(snapshot, kind);
  return matches[matches.length - 1] || null;
}

function countFindings(snapshot) {
  return artifactsOf(snapshot, "PaperCard").reduce(
    (total, artifact) => total + (artifact.payload?.findings || []).length,
    0,
  );
}

function factCheckSummary(snapshot) {
  const reports = artifactsOf(snapshot, "FactCheckReport");
  const revise = reports.filter((artifact) => artifact.payload?.verdict === "REVISE");
  const issues = reports.reduce(
    (total, artifact) => total + (artifact.payload?.issues || []).length,
    0,
  );
  return { reports: reports.length, revise: revise.length, issues };
}

function latestReviewPassed(snapshot) {
  return latestArtifact(snapshot, "ReviewResult")?.payload?.verdict === "PASS";
}

function narrativeCompletion(snapshot) {
  const narrativeArtifact = latestArtifact(snapshot, "NarrativeReview");
  const sections = narrativeArtifact?.payload?.sections || [];
  if (!sections.length) return { complete: false, missing: [], narrative: null };
  const narrativeId = Number(narrativeArtifact.artifact_id || 0);
  const checked = new Set(
    artifactsOf(snapshot, "FactCheckReport")
      .filter((artifact) => Number(artifact.artifact_id || 0) > narrativeId)
      .map((artifact) => artifact.payload?.section_id)
      .filter(Boolean),
  );
  const missing = sections
    .map((section) => section.section_id)
    .filter((sectionId) => sectionId && !checked.has(sectionId));
  return { complete: missing.length === 0, missing, narrative: narrativeArtifact.payload };
}

function currentSectionDrafts(snapshot) {
  const outline = latestArtifact(snapshot, "ReviewOutline");
  if (!outline) return [];
  const outlineId = Number(outline.artifact_id || 0);
  const drafts = new Map();
  artifactsOf(snapshot, "SectionDraft")
    .filter((artifact) => Number(artifact.artifact_id || 0) > outlineId)
    .forEach((artifact) => {
      const sectionId = artifact.payload?.section_id;
      if (sectionId) drafts.set(sectionId, artifact.payload);
    });
  return [...drafts.values()];
}

function recoverableOperationalFailure(snapshot) {
  if (snapshot?.project?.stage !== "INCONCLUSIVE" || !latestReviewPassed(snapshot)) {
    return false;
  }
  const failure = latestArtifact(snapshot, "InsufficientEvidence")?.payload;
  const details = `${failure?.reason || ""}\n${failure?.recommendation || ""}`.toLowerCase();
  return [
    "chief-editor",
    "fact-checker",
    "narrative-writer",
    "research-outliner",
    "structured_response",
    "structured response",
    "subagent",
    "invalid result",
    "missing field",
    "timeout",
    "结构化",
    "无效结果",
    "缺少字段",
    "模型超时",
  ].some((marker) => details.includes(marker));
}

function continuationMode(snapshot) {
  const stage = snapshot?.project?.stage;
  if (stage === "SCREENED") return "screening";
  if (["REVIEWED", "OUTLINED", "NARRATED"].includes(stage) && latestReviewPassed(snapshot)) {
    return "narrative";
  }
  if (stage === "COMPLETED" && latestReviewPassed(snapshot)) {
    return narrativeCompletion(snapshot).complete ? null : "recovery";
  }
  if (recoverableOperationalFailure(snapshot)) return "recovery";
  return null;
}

function effectiveRecoveryStage(snapshot) {
  if (latestArtifact(snapshot, "NarrativeReview")) return "NARRATED";
  if (latestArtifact(snapshot, "ReviewOutline")) return "OUTLINED";
  return "REVIEWED";
}

function metricCard(label, value, hint = "") {
  return h("div", { cls: "summary-card" }, [
    h("strong", {}, String(value)),
    h("span", {}, label),
    hint ? h("small", {}, hint) : null,
  ]);
}

function renderOutcome(snapshot) {
  const narrative = latestArtifact(snapshot, "NarrativeReview")?.payload;
  const insufficient = snapshot?.project?.stage === "INCONCLUSIVE"
    ? latestArtifact(snapshot, "InsufficientEvidence")?.payload
    : null;
  const facts = factCheckSummary(snapshot);
  const needsRecovery = continuationMode(snapshot) === "recovery";
  const operationalRecovery = recoverableOperationalFailure(snapshot);
  const savedDrafts = currentSectionDrafts(snapshot);
  elements.primaryOutcome.replaceChildren();
  elements.projectSummary.classList.remove("has-outcome");

  if (narrative) {
    elements.primaryOutcome.hidden = false;
    elements.projectSummary.classList.add("has-outcome");
    elements.primaryOutcome.append(
      h("div", { cls: "outcome-header" }, [
        h("div", {}, [
          h("p", { cls: "eyebrow" }, needsRecovery ? "成果待核查" : "最终成果"),
          h("h3", {}, narrative.title || "文献综述已生成"),
        ]),
        h(
          "span",
          { cls: `outcome-badge${needsRecovery ? " is-warning" : ""}` },
          needsRecovery ? "待补全" : `${narrative.sections?.length || 0} 章`,
        ),
      ]),
      narrative.abstract
        ? h("p", { cls: "outcome-abstract" }, narrative.abstract)
        : h("p", { cls: "outcome-abstract muted" }, "综述已生成，摘要暂未填写。"),
    );
    if (facts.issues) {
      elements.primaryOutcome.append(
        h(
          "p",
          { cls: "quality-note" },
          `事实核查发现 ${facts.issues} 条可改进点，主要用于修订引用粒度和措辞。`,
        ),
      );
    }
    elements.primaryOutcome.append(renderNarrativeReviewHTML(narrative));
    return;
  }

  if (needsRecovery) {
    elements.primaryOutcome.hidden = false;
    elements.projectSummary.classList.add("has-outcome");
    elements.primaryOutcome.append(
      h("div", { cls: "outcome-header" }, [
        h("div", {}, [
          h("p", { cls: "eyebrow" }, operationalRecovery ? "写作中断" : "成果待补全"),
          h(
            "h3",
            {},
            operationalRecovery && savedDrafts.length
              ? `${savedDrafts.length} 个章节草稿已保存，等待整合`
              : "综述正文尚未生成",
          ),
        ]),
        h("span", { cls: "outcome-badge is-warning" }, "可恢复"),
      ]),
      h(
        "p",
        { cls: "outcome-abstract" },
        operationalRecovery
          ? "检索、筛选、证据提取和章节写作均已保留；本次停止来自主编输出格式故障，不代表证据不足。继续后将直接恢复综述整合与事实核查。"
          : "检索、论文筛选、证据提取和证据审查均已完成，但旧流程提前结束了项目。可复用现有证据继续生成提纲、正文与事实核查。",
      ),
    );
    return;
  }

  if (insufficient) {
    elements.primaryOutcome.hidden = false;
    elements.projectSummary.classList.add("has-outcome");
    elements.primaryOutcome.append(
      h("div", { cls: "outcome-header" }, [
        h("div", {}, [
          h("p", { cls: "eyebrow" }, "停止原因"),
          h("h3", {}, "当前研究未形成最终综述"),
        ]),
        h("span", { cls: "outcome-badge is-warning" }, "需处理"),
      ]),
      h("p", { cls: "outcome-abstract" }, insufficient.reason || "证据不足。"),
      insufficient.recommendation
        ? h("p", { cls: "quality-note" }, insufficient.recommendation)
        : null,
    );
    return;
  }

  elements.primaryOutcome.hidden = true;
}

function renderProjectSummary(snapshot) {
  const project = snapshot?.project;
  if (!project) {
    elements.projectSummary.hidden = true;
    return;
  }
  elements.projectSummary.hidden = false;

  let [title, text] = USER_STAGE_GUIDANCE[project.stage] || [
    STAGE_LABELS[project.stage] || project.stage,
    "项目正在推进中。",
  ];
  const needsRecovery = continuationMode(snapshot) === "recovery";
  if (needsRecovery) {
    const completion = narrativeCompletion(snapshot);
    const operationalRecovery = recoverableOperationalFailure(snapshot);
    const savedDrafts = currentSectionDrafts(snapshot);
    title = completion.narrative
      ? "事实核查尚未完成"
      : operationalRecovery && savedDrafts.length
        ? `${savedDrafts.length} 个章节草稿等待整合`
        : "综述正文尚未生成";
    text = completion.narrative
      ? `还需核查 ${completion.missing.length} 个章节，点击下方按钮即可继续。`
      : operationalRecovery
        ? "主编结构化输出中断，现有论文、证据、提纲和章节草稿均已保留。点击下方按钮从整合阶段继续。"
        : "旧流程提前结束了项目；点击下方按钮可复用现有证据补全最终综述。";
    elements.stageBadge.textContent = operationalRecovery ? "写作待恢复" : "成果待补全";
    elements.stageBadge.className = "stage-badge is-warning";
    renderStepper(effectiveRecoveryStage(snapshot));
  }
  elements.nextActionTitle.textContent = title;
  elements.nextActionText.textContent = text;

  const latestCandidateSet = latestArtifact(snapshot, "CandidateSetSnapshot")?.payload;
  const latestSearch = latestArtifact(snapshot, "SearchReport")?.payload;
  const latestScreening = latestArtifact(snapshot, "ScreeningDecision")?.payload;
  const narrative = latestArtifact(snapshot, "NarrativeReview")?.payload;
  const facts = factCheckSummary(snapshot);
  elements.resultHighlights.replaceChildren(
    metricCard("候选论文", latestCandidateSet?.candidates?.length || latestSearch?.candidates?.length || 0, "当前可审核范围"),
    metricCard("入选精读", latestScreening?.included_paper_ids?.length || 0, "你确认的论文"),
    metricCard("证据摘录", countFindings(snapshot), "可追踪 evidence"),
    metricCard("综述章节", narrative?.sections?.length || 0, facts.revise ? `${facts.revise} 章需修订` : "最终正文"),
  );

  renderOutcome(snapshot);
}

// ── Main render ─────────────────────────────────────────────────────

function renderDetails(snapshot) {
  state.snapshot = snapshot;
  const artifacts = snapshot?.artifacts || [];
  const events = snapshot?.events || [];
  elements.projectDetails.hidden = false;

  const counts = new Map();
  artifacts.forEach((artifact) => {
    counts.set(artifact.kind, (counts.get(artifact.kind) || 0) + 1);
  });
  elements.artifactSummary.replaceChildren();
  counts.forEach((count, kind) => {
    const chip = document.createElement("span");
    chip.className = "artifact-chip";
    chip.textContent = `${artifactLabel(kind)} ${count}`;
    elements.artifactSummary.append(chip);
  });

  elements.eventTimeline.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("li");
    empty.className = "muted small";
    empty.textContent = "尚无状态变化";
    elements.eventTimeline.append(empty);
  } else {
    events.forEach((event) => {
      const item = document.createElement("li");
      item.className = "event-item";
      const transition = document.createElement("strong");
      transition.textContent = `${STAGE_LABELS[event.from_stage] || event.from_stage} 至 ${STAGE_LABELS[event.to_stage] || event.to_stage}`;
      const meta = document.createElement("span");
      meta.textContent = `${event.actor} · ${formatDate(event.created_at)}`;
      item.append(transition, meta);
      elements.eventTimeline.append(item);
    });
  }

  // ── Artifact list with toggle ──
  elements.artifactList.replaceChildren();

  // Toggle bar
  const toggleBar = h('div', {cls:'artifact-toggle-bar'}, [
    h('span',{cls:'toggle-label'},'查看方式'),
    h('button',{
      cls:`toggle-btn ${state.artifactViewMode==='json'?'is-active':''}`,
      'data-mode':'json',
      onClick: (e) => switchArtifactView('json')
    }, '原始数据'),
    h('button',{
      cls:`toggle-btn ${state.artifactViewMode==='html'?'is-active':''}`,
      'data-mode':'html',
      onClick: (e) => switchArtifactView('html')
    }, '可读摘要'),
  ]);
  elements.artifactList.append(toggleBar);

  if (!artifacts.length) {
    const empty = document.createElement("p");
    empty.className = "muted small";
    empty.textContent = "尚无研究产物";
    elements.artifactList.append(empty);
  } else {
    [...artifacts].reverse().forEach((artifact) => {
      const details = document.createElement("details");
      details.className = "artifact-item";
      const summary = document.createElement("summary");
      const kind = document.createElement("span");
      kind.textContent = artifactLabel(artifact.kind);
      const time = document.createElement("span");
      time.className = "muted";
      time.textContent = formatDate(artifact.created_at);
      summary.append(kind, time);

      // JSON view
      const pre = document.createElement("pre");
      pre.className = "artifact-json";
      pre.textContent = JSON.stringify(artifact.payload, null, 2);

      // HTML view
      const renderer = ARTIFACT_HTML_RENDERERS[artifact.kind] || renderGenericArtifactHTML;
      const htmlView = renderer(artifact.payload);

      // Apply current view mode
      if (state.artifactViewMode === 'json') {
        pre.style.display = '';
        htmlView.style.display = 'none';
      } else {
        pre.style.display = 'none';
        htmlView.style.display = '';
      }

      details.append(summary, pre, htmlView);
      elements.artifactList.append(details);
    });
  }
}

function switchArtifactView(mode) {
  state.artifactViewMode = mode;
  // Update toggle buttons
  elements.artifactList.querySelectorAll('.toggle-btn').forEach(btn => {
    btn.classList.toggle('is-active', btn.dataset.mode === mode);
  });
  // Toggle all artifact view panes
  elements.artifactList.querySelectorAll('.artifact-json').forEach(el => {
    el.style.display = mode === 'json' ? '' : 'none';
  });
  elements.artifactList.querySelectorAll('.artifact-html').forEach(el => {
    el.style.display = mode === 'html' ? '' : 'none';
  });
}

function renderStagePanels(snapshot) {
  const mode = continuationMode(snapshot);
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = !mode;
  if (mode === "screening") {
    elements.continueEyebrow.textContent = "Screening complete";
    elements.continueTitle.textContent = "候选集已经确认";
    elements.continueText.textContent = "继续后将读取论文、提取证据并完成综述。";
    elements.continueButtonLabel.textContent = "继续研究";
  } else if (mode) {
    const operationalRecovery = recoverableOperationalFailure(snapshot);
    const savedDraftCount = currentSectionDrafts(snapshot).length;
    elements.continueEyebrow.textContent =
      mode === "recovery" ? "Outcome recovery" : "Resume writing";
    elements.continueTitle.textContent =
      operationalRecovery ? "恢复综述整合" : mode === "recovery" ? "补全最终综述" : "继续综述写作";
    elements.continueText.textContent =
      operationalRecovery
        ? `系统将复用已保存的 ${savedDraftCount} 个章节草稿，从整合阶段继续，不会重新检索或重写章节。`
        : "系统将复用已保存的论文卡片和证据，从当前写作阶段继续，不会重新检索。";
    elements.continueButtonLabel.textContent = "继续生成综述";
  }
  if (mode && !state.agentAvailable) {
    elements.continueResearch.disabled = true;
    elements.continueResearch.title = "Agent 当前不可用，请检查模型配置";
  } else {
    elements.continueResearch.disabled = state.busy;
    elements.continueResearch.title = "";
  }
  refreshIcons();
}

async function loadProject(projectId, quiet = false, force = false) {
  if (!projectId) return;
  if (state.busy && !force) {
    notify("当前操作尚未结束，请稍候", true);
    return;
  }
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(projectId)}`);
    const snapshot = payload.data;
    state.snapshot = snapshot;
    renderProjectHeader(snapshot.project);
    renderProjectSummary(snapshot);
    renderStagePanels(snapshot);
    renderDetails(snapshot);
    elements.runPanel.hidden = true;
    if (snapshot.project.stage === "SEARCH_REVIEW_PENDING") {
      const reviewPayload = await api(
        `/api/projects/${encodeURIComponent(projectId)}/search-review`,
      );
      renderReview(reviewPayload.data);
    }
    elements.projectIdInput.value = projectId;
    if (!quiet) notify("项目状态已载入");
  } catch (error) {
    notify(`无法打开项目：${error.message}`, true);
  }
}

function candidateMatches(candidate, query) {
  if (!query) return true;
  const haystack = [
    candidate.title,
    candidate.doi,
    candidate.paper_id,
    ...(candidate.authors || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
  return haystack.includes(query.toLocaleLowerCase());
}

function renderCandidateCards() {
  const filter = elements.candidateFilter.value.trim();
  elements.candidateGrid.replaceChildren();
  const visible = state.candidates.filter((candidate) => candidateMatches(candidate, filter));

  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = filter ? "没有匹配的候选论文" : "当前候选集为空";
    elements.candidateGrid.append(empty);
  }

  visible.forEach((candidate) => {
    const id = candidateId(candidate);
    const snapshot = state.review?.candidate_set;
    const agentDecision = agentDecisionFor(snapshot, id);
    const agentReason = snapshot?.agent_screening_reasons?.[id];
    const selected = state.selectedIds.has(id);
    const card = document.createElement("article");
    card.className = "candidate-card";
    card.classList.toggle("is-excluded", !selected);

    const head = document.createElement("div");
    head.className = "candidate-card-head";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "candidate-toggle";
    checkbox.checked = selected;
    checkbox.setAttribute("aria-label", `保留论文：${candidate.title || "未命名论文"}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedIds.add(id);
      else state.selectedIds.delete(id);
      renderCandidateCards();
      updateReviewStats();
    });
    const title = document.createElement("h4");
    title.className = "candidate-title";
    title.textContent = candidate.title || "未命名论文";
    head.append(checkbox, title);

    const meta = document.createElement("div");
    meta.className = "candidate-meta";
    const source = document.createElement("span");
    source.className = "candidate-source";
    source.textContent = candidate.source || "未知来源";
    const year = document.createElement("span");
    year.textContent = candidate.year || "年份未知";
    meta.append(source, year);
    if (agentDecision) {
      const badge = document.createElement("span");
      badge.className = "candidate-source";
      badge.textContent = `Agent: ${agentDecision}`;
      meta.append(badge);
    }

    const authors = document.createElement("p");
    authors.className = "candidate-authors";
    authors.textContent = (candidate.authors || []).length
      ? candidate.authors.join("、")
      : "作者信息暂缺";

    const reason = document.createElement("p");
    reason.className = "candidate-reason";
    reason.textContent = agentReason ? `筛选意见：${agentReason}` : "";

    const abstract = document.createElement("p");
    abstract.className = "candidate-abstract";
    abstract.textContent = candidate.abstract || "暂无摘要，可在后续精读阶段尝试获取全文。";

    const identifiers = document.createElement("div");
    identifiers.className = "candidate-identifiers";
    const code = document.createElement("code");
    code.textContent = candidate.doi || id;
    identifiers.append(code);
    const url = safeHttpUrl(candidate.url);
    if (url) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.append(document.createTextNode("查看原文"), iconNode("external-link"));
      identifiers.append(link);
    }

    card.append(head, meta, authors);
    if (agentReason) card.append(reason);
    card.append(abstract, identifiers);
    elements.candidateGrid.append(card);
  });
  refreshIcons();
}

function updateReviewStats() {
  elements.candidateCount.textContent = String(state.candidates.length);
  elements.selectedCount.textContent = String(state.selectedIds.size);
  const snapshot = state.review?.candidate_set;
  elements.roundCount.textContent = `${snapshot?.search_round || 0} / ${snapshot?.max_search_rounds ?? 3}`;
}

function renderReview(review) {
  state.review = review;
  state.candidates = review.candidate_set?.candidates || [];
  const snapshot = review.candidate_set || {};
  const agentIncluded = new Set(
    (snapshot.agent_included_paper_ids || []).map(normalizePaperId),
  );
  state.selectedIds = agentIncluded.size
    ? new Set(
        state.candidates
          .map(candidateId)
          .filter((id) => agentIncluded.has(normalizePaperId(id))),
      )
    : new Set(state.candidates.map(candidateId));
  elements.minPapers.value = snapshot.min_papers ?? 1;
  elements.maxPapers.value = snapshot.max_papers ?? 8;
  elements.maxSearchRounds.value = snapshot.max_search_rounds ?? 3;
  renderProjectHeader(review.project);
  renderProjectSummary({
    project: review.project,
    artifacts: [...(state.snapshot?.artifacts || []), { kind: "CandidateSetSnapshot", payload: snapshot }],
    events: state.snapshot?.events || [],
  });
  elements.reviewPanel.hidden = false;
  elements.continuePanel.hidden = true;
  renderCandidateCards();
  updateReviewStats();
}

function feedbackBody(action) {
  const exclusions = state.candidates
    .map(candidateId)
    .filter((id) => !state.selectedIds.has(id));
  const queries = parseList(elements.querySuggestions.value);
  const dois = parseList(elements.manualDois.value, true);
  return {
    action,
    suggested_queries: queries,
    added_papers: dois.map((doi) => ({ doi })),
    excluded_paper_ids: exclusions,
    comment: elements.feedbackComment.value.trim(),
    min_papers: numberInputValue(elements.minPapers, 1),
    max_papers: numberInputValue(elements.maxPapers, 8),
    max_search_rounds: numberInputValue(elements.maxSearchRounds, 3),
  };
}

async function submitFeedback(action) {
  if (!state.projectId || state.busy) return;
  const body = feedbackBody(action);
  if (body.suggested_queries.length > 3) {
    notify("每轮最多提交 3 条补充检索词", true);
    return;
  }
  if (body.min_papers > body.max_papers) {
    notify("精读篇数下限不能大于上限", true);
    return;
  }
  if (action === "refine") {
    const snapshot = state.review?.candidate_set || {};
    const controlsChanged =
      body.min_papers !== (snapshot.min_papers ?? 1) ||
      body.max_papers !== (snapshot.max_papers ?? 8) ||
      body.max_search_rounds !== (snapshot.max_search_rounds ?? 3);
    const hasChange =
      body.suggested_queries.length ||
      body.added_papers.length ||
      body.excluded_paper_ids.length ||
      controlsChanged ||
      body.comment;
    if (!hasChange) {
      notify("请先填写检索建议、DOI、排除论文或审核说明", true);
      return;
    }
    if (
      body.excluded_paper_ids.length &&
      !window.confirm(
        `将从候选集中移除 ${body.excluded_paper_ids.length} 篇论文。提交后当前版本无法一键恢复，是否继续？`,
      )
    ) {
      return;
    }
  }

  if (action === "accept") {
    const acceptedCount = state.selectedIds.size + body.added_papers.length;
    if (!acceptedCount) {
      notify("至少保留或加入一篇论文后才能确认", true);
      return;
    }
    if (acceptedCount < body.min_papers || acceptedCount > body.max_papers) {
      notify(`当前保留 ${acceptedCount} 篇，需位于 ${body.min_papers}-${body.max_papers} 篇之间`, true);
      return;
    }
    if (
      !window.confirm(
        `确认保留 ${acceptedCount} 篇论文并结束人工审核？`,
      )
    ) {
      return;
    }
  }

  if (
    action === "stop" &&
    !window.confirm("这会以证据不足状态结束当前项目，确认继续吗？")
  ) {
    return;
  }

  setBusy(true);
  try {
    const payload = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/search-feedback`,
      { method: "POST", body: JSON.stringify(body) },
    );
    const result = payload.data;
    elements.querySuggestions.value = "";
    elements.manualDois.value = "";
    elements.feedbackComment.value = "";
    if (action === "refine") {
      renderReview(result);
      const failures = result.search_failures || [];
      notify(
        failures.length
          ? `候选集已更新，${failures.length} 条检索出现失败，请查看项目产物`
          : "候选集已更新",
        failures.length > 0,
      );
    } else if (action === "accept" && result.ready_to_continue) {
      elements.reviewPanel.hidden = true;
      elements.runPanel.hidden = false;
      elements.activityLog.replaceChildren();
      addActivity("候选集已确认，正在直接进入论文精读");
      await api(`/api/projects/${encodeURIComponent(state.projectId)}/continue`, {
        method: "POST",
        body: "{}",
      });
      addActivity("后续研究执行结束，正在刷新项目状态");
      await loadProjects();
      await loadProject(state.projectId, true, true);
      notify("候选集已确认，并已完成本轮后续研究");
    } else {
      await loadProjects();
      await loadProject(state.projectId, true, true);
      notify(action === "accept" ? "候选集已确认，可以继续研究" : "项目已结束");
    }
  } catch (error) {
    notify(`提交失败：${error.message}`, true);
    await loadProject(state.projectId, true, true);
  } finally {
    setBusy(false);
  }
}

function addActivity(message) {
  const item = document.createElement("li");
  item.textContent = message;
  elements.activityLog.append(item);
  elements.activityLog.scrollTop = elements.activityLog.scrollHeight;
  elements.runStatusText.textContent = message;
}

function findProject(value, depth = 0) {
  if (!value || typeof value !== "object" || depth > 9) return null;
  if (typeof value.project_id === "string" && typeof value.stage === "string") {
    return value;
  }
  for (const item of Object.values(value)) {
    const found = findProject(item, depth + 1);
    if (found) return found;
  }
  return null;
}

function streamUpdateLabel(eventName, payload) {
  if (eventName === "awaiting_input") return "初次检索完成，等待人工审核";
  if (eventName === "done") return "本轮 Agent 执行结束";
  if (eventName === "fallback") return "模型不可用，已进入降级流程";
  if (eventName === "error") return payload?.message || "Agent 执行失败";
  const keys = payload && typeof payload === "object" ? Object.keys(payload) : [];
  const name = keys[0] || "Agent";
  if (name.includes("tool")) return "正在执行工具并整理结果";
  return `收到 ${name} 阶段更新`;
}

async function handleStreamEvent(eventName, payload) {
  addActivity(streamUpdateLabel(eventName, payload));
  const project = findProject(payload);
  if (project) {
    state.projectId = project.project_id;
    renderProjectHeader(project);
    renderProjectSummary({
      project,
      artifacts: state.snapshot?.artifacts || [],
      events: state.snapshot?.events || [],
    });
  }
  if (eventName === "awaiting_input" && payload?.data) {
    renderReview(payload.data);
    await loadProjects();
  }
  if (eventName === "error") {
    throw new Error(payload?.message || "Agent 流式执行失败");
  }
}

async function startResearch(topic, question, reviewLimits = {}) {
  state.projectId = null;
  state.snapshot = null;
  state.review = null;
  showWorkspace("project");
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = true;
  elements.projectDetails.hidden = true;
  elements.runPanel.hidden = false;
  elements.activityLog.replaceChildren();
  const pendingProject = {
    project_id: "正在创建项目…",
    topic,
    research_question: question,
    stage: "CREATED",
  };
  renderProjectHeader(pendingProject);
  renderProjectSummary({ project: pendingProject, artifacts: [], events: [] });
  addActivity("正在创建项目并准备检索");
  if (reviewLimits.max_search_rounds) {
    addActivity(
      `系统将自动执行最多 ${reviewLimits.max_search_rounds} 轮检索-筛选，再交给你最终手筛`,
    );
  }
  setBusy(true);

  try {
    const response = await fetch("/api/research/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, research_question: question, ...reviewLimits }),
    });
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      throw new Error(errorMessage(payload, `启动失败（HTTP ${response.status}）`));
    }
    if (!response.body) throw new Error("浏览器无法读取流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        if (!block.trim()) continue;
        let eventName = "message";
        const dataLines = [];
        block.split(/\r?\n/).forEach((line) => {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        });
        let payload = {};
        try {
          payload = JSON.parse(dataLines.join("\n") || "{}");
        } catch {
          payload = { message: dataLines.join("\n") };
        }
        await handleStreamEvent(eventName, payload);
      }
      if (done) break;
    }

    await loadProjects();
    if (state.projectId && !state.projectId.includes("正在")) {
      await loadProject(state.projectId, true, true);
    }
    notify("本轮 Agent 执行结束");
  } catch (error) {
    notify(`研究执行失败：${error.message}`, true);
    if (state.projectId && !state.projectId.includes("正在")) {
      await loadProject(state.projectId, true, true);
    }
  } finally {
    elements.runPanel.hidden = true;
    setBusy(false);
  }
}

async function continueResearch() {
  if (!state.projectId || state.busy) return;
  if (!state.agentAvailable) {
    notify("Agent 当前不可用，请先检查模型配置", true);
    return;
  }
  const mode = continuationMode(state.snapshot);
  const confirmation = mode === "screening"
    ? "继续后将开始逐篇读取论文，这可能需要几分钟。确认开始吗？"
    : recoverableOperationalFailure(state.snapshot)
      ? "将从已保存的章节草稿恢复综述整合与事实核查，不会重新检索、重读论文或重写章节。确认开始吗？"
      : "将复用已保存的证据继续生成综述，不会重新检索或重读论文。确认开始吗？";
  if (!window.confirm(confirmation)) {
    return;
  }
  setBusy(true);
  elements.runPanel.hidden = false;
  elements.activityLog.replaceChildren();
  addActivity(
    mode === "screening"
      ? "正在恢复项目并启动论文精读"
      : "正在恢复写作阶段并生成缺失的综述产物",
  );
  try {
    await api(`/api/projects/${encodeURIComponent(state.projectId)}/continue`, {
      method: "POST",
      body: "{}",
    });
    addActivity("后续研究执行结束，正在刷新项目状态");
    await loadProjects();
    await loadProject(state.projectId, true, true);
    notify("项目已完成本轮后续研究");
  } catch (error) {
    notify(`继续执行失败：${error.message}`, true);
    await loadProject(state.projectId, true, true);
  } finally {
    elements.runPanel.hidden = true;
    setBusy(false);
  }
}

function toggleNewProject(show) {
  if (show) {
    closeMenus();
    showWorkspace("create");
    window.setTimeout(() => byId("topicInput").focus(), 0);
    return;
  }
  showWorkspace(state.project ? "project" : "empty");
}

elements.newProjectToggle.addEventListener("click", () => {
  toggleNewProject(true);
});
elements.emptyNewProject.addEventListener("click", () => toggleNewProject(true));
elements.cancelNewProject.addEventListener("click", () => toggleNewProject(false));
elements.cancelNewProjectSecondary.addEventListener("click", () => toggleNewProject(false));
elements.newProjectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const topic = byId("topicInput").value.trim();
  const question = byId("questionInput").value.trim();
  if (!topic || !question) return;
  const reviewLimits = {
    min_papers: numberInputValue(elements.initialMinPapers, 2),
    max_papers: numberInputValue(elements.initialMaxPapers, 6),
    max_search_rounds: numberInputValue(elements.initialMaxSearchRounds, 3),
  };
  if (reviewLimits.min_papers > reviewLimits.max_papers) {
    notify("精读篇数下限不能大于上限", true);
    return;
  }
  toggleNewProject(false);
  await startResearch(topic, question, reviewLimits);
});
elements.refreshProjects.addEventListener("click", loadProjects);
elements.projectLookupForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const projectId = elements.projectIdInput.value.trim();
  if (!projectId) return;
  closeMenus();
  loadProject(projectId);
});
elements.reloadProject.addEventListener("click", () => loadProject(state.projectId));
elements.deleteProject.addEventListener("click", deleteCurrentProject);
elements.copyProjectId.addEventListener("click", async () => {
  if (!state.projectId) return;
  try {
    await navigator.clipboard.writeText(state.projectId);
    notify("项目 ID 已复制");
  } catch {
    notify("复制失败，请手动选择项目 ID", true);
  }
});
elements.candidateFilter.addEventListener("input", renderCandidateCards);
elements.selectAll.addEventListener("click", () => {
  state.selectedIds = new Set(state.candidates.map(candidateId));
  renderCandidateCards();
  updateReviewStats();
});
elements.clearAll.addEventListener("click", () => {
  state.selectedIds.clear();
  renderCandidateCards();
  updateReviewStats();
});
elements.refineReview.addEventListener("click", () => submitFeedback("refine"));
elements.acceptReview.addEventListener("click", () => submitFeedback("accept"));
elements.stopReview.addEventListener("click", () => submitFeedback("stop"));
elements.continueResearch.addEventListener("click", continueResearch);

elements.sidebarToggle.addEventListener("click", () => {
  const next = elements.appShell.dataset.sidebar === "expanded" ? "collapsed" : "expanded";
  applySidebarState(next, true);
});
elements.toolsMenuToggle.addEventListener("click", () => {
  const open = elements.toolsMenu.hidden;
  setPopover(elements.projectMenuToggle, elements.projectMenu, false);
  setPopover(elements.toolsMenuToggle, elements.toolsMenu, open);
  if (open) elements.toolsMenu.querySelector("a, button, input")?.focus();
});
elements.projectMenuToggle.addEventListener("click", () => {
  const open = elements.projectMenu.hidden;
  setPopover(elements.toolsMenuToggle, elements.toolsMenu, false);
  setPopover(elements.projectMenuToggle, elements.projectMenu, open);
  if (open) elements.deleteProject.focus();
});
elements.inspectorToggle.addEventListener("click", () => {
  if (state.inspectorOpen) closeInspector();
  else openInspector("process");
});
elements.closeInspector.addEventListener("click", () => closeInspector());
elements.inspectorBackdrop.addEventListener("click", () => closeInspector());
elements.processTab.addEventListener("click", () => setInspectorTab("process"));
elements.artifactsTab.addEventListener("click", () => setInspectorTab("artifacts"));

document.addEventListener("click", (event) => {
  if (!elements.toolsMenuToggle.closest(".menu-anchor").contains(event.target)) {
    setPopover(elements.toolsMenuToggle, elements.toolsMenu, false);
  }
  if (!elements.projectMenuToggle.closest(".menu-anchor").contains(event.target)) {
    setPopover(elements.projectMenuToggle, elements.projectMenu, false);
  }
});

document.addEventListener("keydown", (event) => {
  if (state.inspectorOpen && event.key === "Tab") {
    const focusable = [...elements.projectInspector.querySelectorAll(
      'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
    )].filter((element) => element.getClientRects().length > 0);
    if (focusable.length) {
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    return;
  }
  if (event.key !== "Escape") return;
  if (state.inspectorOpen) {
    event.preventDefault();
    closeInspector();
    return;
  }
  const toolsWereOpen = !elements.toolsMenu.hidden;
  const projectMenuWasOpen = !elements.projectMenu.hidden;
  closeMenus();
  if (projectMenuWasOpen) elements.projectMenuToggle.focus();
  else if (toolsWereOpen) elements.toolsMenuToggle.focus();
});

async function initialize() {
  initializeSidebar();
  refreshIcons();
  await Promise.all([checkHealth(), loadProjects()]);
  const params = new URLSearchParams(window.location.search);
  const requestedProject = params.get("project");
  if (requestedProject) await loadProject(requestedProject, true);
}

initialize();
