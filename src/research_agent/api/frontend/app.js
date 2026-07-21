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
  ["REVISION_PENDING", "事实修订"],
  ["COMPLETED", "完成"],
];

const STAGE_LABELS = Object.fromEntries(STAGES);
STAGE_LABELS.INCONCLUSIVE = "证据不足";
STAGE_LABELS.OUTLINED = "提纲设计";
STAGE_LABELS.NARRATED = "综述已生成";
STAGE_LABELS.REVISION_PENDING = "事实修订";

const RUN_PHASES = {
  thinking: {
    title: "正在理解研究问题",
    detail: "分析研究目标、边界与下一步行动。",
    icon: "brain",
  },
  searching: {
    title: "正在检索研究文献",
    detail: "组合检索词并比对候选论文。",
    icon: "search",
  },
  reading: {
    title: "正在精读入选论文",
    detail: "读取方法、数据、发现与研究限制。",
    icon: "book-open",
  },
  synthesizing: {
    title: "正在综合证据",
    detail: "连接跨论文发现并识别一致与冲突之处。",
    icon: "network",
  },
  reviewing: {
    title: "正在审查证据链",
    detail: "核对结论是否由可追踪证据支持。",
    icon: "shield-check",
  },
  outlining: {
    title: "正在设计综述结构",
    detail: "组织章节顺序、论证路径与引用范围。",
    icon: "list-tree",
  },
  writing: {
    title: "正在撰写文献综述",
    detail: "依据提纲逐节整合正文与引用。",
    icon: "pen-line",
  },
  verifying: {
    title: "正在核查最终综述",
    detail: "逐节检查事实、证据引用与结论边界。",
    icon: "file-check-2",
  },
  done: {
    title: "研究执行已完成",
    detail: "研究产物已经保存，正在整理最终界面。",
    icon: "check-circle-2",
  },
  stopped: {
    title: "研究流程已停止",
    detail: "系统正在保存停止原因与已有研究产物。",
    icon: "circle-alert",
  },
};

const STAGE_RUN_PHASES = {
  CREATED: "searching",
  SEARCHED: "searching",
  SEARCH_REVIEW_PENDING: "reviewing",
  SCREENED: "reading",
  EXTRACTED: "synthesizing",
  SYNTHESIZED: "reviewing",
  REVIEW_PENDING: "reviewing",
  REVIEWED: "outlining",
  OUTLINED: "writing",
  NARRATED: "verifying",
  REVISION_PENDING: "writing",
  COMPLETED: "done",
  INCONCLUSIVE: "stopped",
};

const ACTOR_LABELS = {
  "literature-scout": "文献检索 Agent",
  "human-search-review": "人工检索审核",
  "research-supervisor": "研究调度器",
  "paper-reader": "论文精读 Agent",
  "research-synthesizer": "证据综合 Agent",
  "evidence-reviewer": "证据审查 Agent",
  "research-outliner": "提纲设计 Agent",
  "narrative-writer": "综述写作 Agent",
  "chief-editor": "综述主编 Agent",
  "chief-editor-fallback": "综述主编恢复流程",
  "fact-checker": "事实核查 Agent",
  "workflow-recovery": "工作流恢复器",
};

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
  projectsLoading: false,
  projectId: null,
  project: null,
  snapshot: null,
  conversation: null,
  activeRun: null,
  activeRunId: null,
  review: null,
  candidates: [],
  manualCandidates: new Map(),
  selectedIds: new Set(),
  busy: false,
  agentAvailable: false,
  toastTimer: null,
  artifactViewMode: "html",
  sidebarPreference: null,
  inspectorOpen: false,
  inspectorPreviousFocus: null,
  usageGuideOpen: false,
  usageGuidePreviousFocus: null,
  libraryPapers: [],
  libraryOverview: { counts: {}, collections: [] },
  projectLibrary: new Map(),
  selectedLibraryId: null,
  selectedLibraryIds: new Set(),
  libraryView: "all",
  libraryCollectionId: null,
  libraryAssistantScope: "all",
  paperWorkspace: null,
  paperPdf: null,
  paperPdfLoadingTask: null,
  paperPdfJs: null,
  paperZoom: 1,
  paperSelection: null,
  paperLastAnswer: null,
  paperRenderSession: 0,
  runStartedAt: null,
  runClockTimer: null,
  runPollTimer: null,
  runPollInFlight: false,
  runKnownEvents: new Set(),
  runKnownArtifacts: new Set(),
  runSnapshotSignature: "",
  runLastActivity: "",
  runPhase: "thinking",
  runSessionId: 0,
};

const byId = (id) => document.getElementById(id);

const elements = {
  appShell: byId("appShell"),
  sidebarToggle: byId("sidebarToggle"),
  healthBadge: byId("healthBadge"),
  toolsMenuToggle: byId("toolsMenuToggle"),
  toolsMenu: byId("toolsMenu"),
  usageGuideOpen: byId("usageGuideOpen"),
  newProjectToggle: byId("newProjectToggle"),
  libraryToggle: byId("libraryToggle"),
  newProjectForm: byId("newProjectForm"),
  createView: byId("createView"),
  libraryView: byId("libraryView"),
  paperWorkspaceView: byId("paperWorkspaceView"),
  paperWorkspaceBack: byId("paperWorkspaceBack"),
  paperWorkspaceTitle: byId("paperWorkspaceTitle"),
  paperWorkspaceMeta: byId("paperWorkspaceMeta"),
  generateReadingCard: byId("generateReadingCard"),
  exportReadingReport: byId("exportReadingReport"),
  paperPageStatus: byId("paperPageStatus"),
  paperZoomOut: byId("paperZoomOut"),
  paperZoomIn: byId("paperZoomIn"),
  paperZoomLabel: byId("paperZoomLabel"),
  paperSelectionBar: byId("paperSelectionBar"),
  paperSelectionPage: byId("paperSelectionPage"),
  paperSelectionPreview: byId("paperSelectionPreview"),
  highlightSelection: byId("highlightSelection"),
  noteSelection: byId("noteSelection"),
  askSelection: byId("askSelection"),
  paperPdfPages: byId("paperPdfPages"),
  paperAskContext: byId("paperAskContext"),
  paperQuestionForm: byId("paperQuestionForm"),
  paperQuestionInput: byId("paperQuestionInput"),
  clearPaperSelection: byId("clearPaperSelection"),
  paperAnswer: byId("paperAnswer"),
  paperAskPanel: byId("paperAskPanel"),
  paperAnnotationsPanel: byId("paperAnnotationsPanel"),
  paperCardPanel: byId("paperCardPanel"),
  paperNoteForm: byId("paperNoteForm"),
  paperNoteInput: byId("paperNoteInput"),
  cancelPaperNote: byId("cancelPaperNote"),
  paperAnnotationsList: byId("paperAnnotationsList"),
  paperReadingCard: byId("paperReadingCard"),
  librarySearch: byId("librarySearch"),
  refreshLibrary: byId("refreshLibrary"),
  askLibrary: byId("askLibrary"),
  libraryList: byId("libraryList"),
  libraryCount: byId("libraryCount"),
  libraryDetail: byId("libraryDetail"),
  librarySmartViews: byId("librarySmartViews"),
  libraryCollections: byId("libraryCollections"),
  newCollection: byId("newCollection"),
  findDuplicates: byId("findDuplicates"),
  libraryListTitle: byId("libraryListTitle"),
  libraryBulkBar: byId("libraryBulkBar"),
  librarySelectedCount: byId("librarySelectedCount"),
  libraryBulkAction: byId("libraryBulkAction"),
  applyLibraryBulk: byId("applyLibraryBulk"),
  compareLibrarySelection: byId("compareLibrarySelection"),
  clearLibrarySelection: byId("clearLibrarySelection"),
  libraryComparePanel: byId("libraryComparePanel"),
  closeLibraryCompare: byId("closeLibraryCompare"),
  libraryCompareTable: byId("libraryCompareTable"),
  libraryAssistantForm: byId("libraryAssistantForm"),
  libraryAssistantQuestion: byId("libraryAssistantQuestion"),
  libraryAssistantScopeLabel: byId("libraryAssistantScopeLabel"),
  libraryAssistantAnswer: byId("libraryAssistantAnswer"),
  libraryImportFormat: byId("libraryImportFormat"),
  libraryImportContent: byId("libraryImportContent"),
  libraryImportTags: byId("libraryImportTags"),
  importLibrary: byId("importLibrary"),
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
  initialYearFrom: byId("initialYearFrom"),
  initialYearTo: byId("initialYearTo"),
  initialQualityVenuesOnly: byId("initialQualityVenuesOnly"),
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
  runVisualizer: byId("runVisualizer"),
  runPhaseIcon: byId("runPhaseIcon"),
  runPhaseTitle: byId("runPhaseTitle"),
  runStatusText: byId("runStatusText"),
  runElapsed: byId("runElapsed"),
  activityLog: byId("activityLog"),
  reviewPanel: byId("reviewPanel"),
  candidateCount: byId("candidateCount"),
  selectedCount: byId("selectedCount"),
  roundCount: byId("roundCount"),
  reviewConstraints: byId("reviewConstraints"),
  reviewNotice: byId("reviewNotice"),
  candidateFilter: byId("candidateFilter"),
  candidateGrid: byId("candidateGrid"),
  filteredCandidatesPanel: byId("filteredCandidatesPanel"),
  filteredCandidateCount: byId("filteredCandidateCount"),
  filteredCandidateGrid: byId("filteredCandidateGrid"),
  selectAll: byId("selectAll"),
  clearAll: byId("clearAll"),
  minPapers: byId("minPapers"),
  maxPapers: byId("maxPapers"),
  maxSearchRounds: byId("maxSearchRounds"),
  querySuggestions: byId("querySuggestions"),
  manualDois: byId("manualDois"),
  feedbackComment: byId("feedbackComment"),
  refineReview: byId("refineReview"),
  undoReview: byId("undoReview"),
  acceptReview: byId("acceptReview"),
  stopReview: byId("stopReview"),
  continuePanel: byId("continuePanel"),
  continueEyebrow: byId("continueEyebrow"),
  continueTitle: byId("continueTitle"),
  continueText: byId("continueText"),
  continueButtonLabel: byId("continueButtonLabel"),
  continueResearch: byId("continueResearch"),
  undoDecision: byId("undoDecision"),
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
  usageGuide: byId("usageGuide"),
  usageGuideBackdrop: byId("usageGuideBackdrop"),
  usageGuideClose: byId("usageGuideClose"),
  usageGuideDismiss: byId("usageGuideDismiss"),
  toast: byId("toast"),
};

const SIDEBAR_STORAGE_KEY = "research-agent.sidebar-state";
const USAGE_GUIDE_STORAGE_KEY = "research-agent.usage-guide-dismissed.v1";

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
  elements.libraryView.hidden = view !== "library";
  elements.paperWorkspaceView.hidden = view !== "paper";
  elements.projectView.hidden = view !== "project";
  elements.libraryToggle.classList.toggle("is-active", ["library", "paper"].includes(view));
}

function setPopover(toggle, popover, open) {
  popover.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
}

function closeMenus() {
  setPopover(elements.toolsMenuToggle, elements.toolsMenu, false);
  setPopover(elements.projectMenuToggle, elements.projectMenu, false);
}

function openUsageGuide() {
  if (state.usageGuideOpen) return;
  const previousFocus = document.activeElement === elements.usageGuideOpen
    ? elements.toolsMenuToggle
    : document.activeElement;
  closeMenus();
  if (state.inspectorOpen) closeInspector({ restoreFocus: false });
  state.usageGuidePreviousFocus = previousFocus;
  state.usageGuideOpen = true;
  elements.usageGuide.hidden = false;
  elements.usageGuideBackdrop.hidden = false;
  window.setTimeout(() => elements.usageGuideDismiss.focus(), 0);
}

function closeUsageGuide({ remember = true, restoreFocus = true } = {}) {
  if (!state.usageGuideOpen) return;
  state.usageGuideOpen = false;
  elements.usageGuide.hidden = true;
  elements.usageGuideBackdrop.hidden = true;
  if (remember) {
    try {
      window.localStorage.setItem(USAGE_GUIDE_STORAGE_KEY, "true");
    } catch {
      // The guide can still be closed when browser storage is unavailable.
    }
  }
  if (restoreFocus && state.usageGuidePreviousFocus instanceof HTMLElement) {
    state.usageGuidePreviousFocus.focus();
  }
  state.usageGuidePreviousFocus = null;
}

function maybeOpenUsageGuide() {
  let dismissed = false;
  try {
    dismissed = window.localStorage.getItem(USAGE_GUIDE_STORAGE_KEY) === "true";
  } catch {
    // Show the guide when the browser does not expose persistent storage.
  }
  if (!dismissed) window.setTimeout(openUsageGuide, 0);
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
  elements.runPanel.setAttribute("aria-busy", String(busy));
  elements.stageStepper.classList.toggle("is-running", busy);
  [
    elements.refineReview,
    elements.undoReview,
    elements.acceptReview,
    elements.stopReview,
    elements.undoDecision,
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

function safeLibraryResourceUrl(value) {
  const text = String(value || "");
  if (text.startsWith("/api/library/attachments/") && text.endsWith("/content")) {
    return text;
  }
  return safeHttpUrl(text);
}

function libraryIdentity(value) {
  return normalizePaperId(value).toLocaleLowerCase();
}

function indexProjectLibrary(items) {
  state.projectLibrary = new Map();
  (items || []).forEach((item) => {
    const relation = item.relation || {};
    const paper = item.paper || {};
    [relation.source_paper_id, paper.paper_id, paper.doi]
      .filter(Boolean)
      .forEach((value) => state.projectLibrary.set(libraryIdentity(value), item));
  });
}

function libraryEntryForCandidate(candidate) {
  return [candidate.paper_id, candidate.doi]
    .filter(Boolean)
    .map((value) => state.projectLibrary.get(libraryIdentity(value)))
    .find(Boolean) || null;
}

async function loadProjectLibrary(projectId) {
  if (!projectId) {
    indexProjectLibrary([]);
    return [];
  }
  const payload = await api(`/api/projects/${encodeURIComponent(projectId)}/library`);
  const items = payload.data || [];
  indexProjectLibrary(items);
  return items;
}

function libraryPaperMeta(paper) {
  const parts = [];
  if ((paper.authors || []).length) parts.push(paper.authors.slice(0, 3).join("、"));
  if (paper.year) parts.push(String(paper.year));
  if (paper.doi) parts.push(`DOI ${paper.doi}`);
  return parts.join(" · ") || "元数据待补充";
}

const LIBRARY_SMART_VIEWS = [
  ["all", "library", "全部文献"],
  ["starred", "star", "重点文献"],
  ["unfiled", "inbox", "未加入文件夹"],
  ["trash", "trash-2", "回收站"],
];

function renderLibraryNavigation() {
  elements.librarySmartViews.replaceChildren();
  LIBRARY_SMART_VIEWS.forEach(([view, icon, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-nav-item";
    button.classList.toggle("is-active", !state.libraryCollectionId && state.libraryView === view);
    button.append(iconNode(icon));
    const copy = document.createElement("span");
    copy.textContent = label;
    const count = document.createElement("small");
    count.textContent = String(state.libraryOverview.counts?.[view] || 0);
    button.append(copy, count);
    button.addEventListener("click", async () => {
      state.libraryView = view;
      state.libraryCollectionId = null;
      state.selectedLibraryIds.clear();
      await loadLibrary();
    });
    elements.librarySmartViews.append(button);
  });

  elements.libraryCollections.replaceChildren();
  const collections = state.libraryOverview.collections || [];
  const collectionIds = new Set(collections.map((item) => item.collection_id));
  const childrenByParent = new Map();
  collections.forEach((collection) => {
    const parentId = collection.parent_id && collectionIds.has(collection.parent_id)
      ? collection.parent_id
      : null;
    const siblings = childrenByParent.get(parentId) || [];
    siblings.push(collection);
    childrenByParent.set(parentId, siblings);
  });

  const renderCollectionNode = (collection, depth) => {
    const row = document.createElement("div");
    row.className = "library-collection-row";
    row.dataset.depth = String(depth);
    row.style.setProperty("--tree-depth", String(depth));

    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-nav-item library-collection-main";
    button.classList.toggle("is-active", state.libraryCollectionId === collection.collection_id);
    button.append(iconNode((childrenByParent.get(collection.collection_id) || []).length ? "folder-tree" : "folder"));
    const copy = document.createElement("span");
    copy.textContent = collection.name;
    const count = document.createElement("small");
    count.textContent = String(collection.paper_count || 0);
    button.append(copy, count);
    button.addEventListener("click", async () => {
      state.libraryView = "all";
      state.libraryCollectionId = collection.collection_id;
      state.selectedLibraryIds.clear();
      await loadLibrary();
    });

    const actions = document.createElement("span");
    actions.className = "library-collection-actions";
    if (depth < 2) {
      const addChild = document.createElement("button");
      addChild.type = "button";
      addChild.className = "icon-button";
      addChild.title = `在“${collection.name}”中新建子文件夹`;
      addChild.setAttribute("aria-label", `在“${collection.name}”中新建子文件夹`);
      addChild.append(iconNode("folder-plus"));
      addChild.addEventListener("click", () => createLibraryCollection(
        collection.collection_id,
        collection.name,
      ));
      actions.append(addChild);
    }
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "icon-button";
    edit.title = `编辑文件夹“${collection.name}”`;
    edit.setAttribute("aria-label", `编辑文件夹“${collection.name}”`);
    edit.append(iconNode("pencil"));
    edit.addEventListener("click", () => editLibraryCollection(collection));
    actions.append(edit);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button";
    remove.title = `删除文件夹“${collection.name}”`;
    remove.setAttribute("aria-label", `删除文件夹“${collection.name}”`);
    remove.append(iconNode("trash-2"));
    remove.addEventListener("click", async () => {
      if (!window.confirm(`删除文件夹“${collection.name}”？文献会保留在库中。`)) return;
      await api(`/api/library/collections/${encodeURIComponent(collection.collection_id)}`, { method: "DELETE" });
      if (state.libraryCollectionId === collection.collection_id) state.libraryCollectionId = null;
      await loadLibrary();
    });
    actions.append(remove);
    row.append(button, actions);
    elements.libraryCollections.append(row);

    (childrenByParent.get(collection.collection_id) || []).forEach((child) => {
      renderCollectionNode(child, depth + 1);
    });
  };

  (childrenByParent.get(null) || []).forEach((collection) => {
    renderCollectionNode(collection, 0);
  });
  refreshIcons();
}

function updateLibraryBulkBar() {
  const count = state.selectedLibraryIds.size;
  elements.libraryBulkBar.hidden = count === 0;
  elements.librarySelectedCount.textContent = `已选 ${count} 篇`;
  elements.compareLibrarySelection.disabled = count < 2 || count > 8;
}

function renderLibraryList() {
  elements.libraryList.replaceChildren();
  elements.libraryCount.textContent = `${state.libraryPapers.length} 篇`;
  const selectedCollection = (state.libraryOverview.collections || []).find(
    (item) => item.collection_id === state.libraryCollectionId,
  );
  elements.libraryListTitle.textContent = selectedCollection?.name
    || LIBRARY_SMART_VIEWS.find(([view]) => view === state.libraryView)?.[2]
    || "全部文献";
  updateLibraryBulkBar();
  if (!state.libraryPapers.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = elements.librarySearch.value.trim()
      ? "没有匹配的文献"
      : "文献库还是空的，可从候选论文收藏或导入 BibTeX / RIS";
    elements.libraryList.append(empty);
    return;
  }

  state.libraryPapers.forEach((paper) => {
    const row = document.createElement("div");
    row.className = "library-paper-row";
    row.classList.toggle("is-active", paper.library_id === state.selectedLibraryId);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedLibraryIds.has(paper.library_id);
    checkbox.setAttribute("aria-label", `选择文献：${paper.title}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedLibraryIds.add(paper.library_id);
      else state.selectedLibraryIds.delete(paper.library_id);
      updateLibraryBulkBar();
    });

    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-paper-main";
    button.setAttribute("aria-label", `查看文献：${paper.title}`);

    const title = document.createElement("strong");
    title.textContent = paper.title;
    const meta = document.createElement("span");
    meta.textContent = libraryPaperMeta(paper);
    const badges = document.createElement("span");
    badges.className = "library-paper-badges";
    if (paper.starred) {
      const starred = document.createElement("span");
      starred.textContent = "已收藏";
      badges.append(starred);
    }
    const projects = document.createElement("span");
    projects.textContent = `${paper.project_count || 0} 个项目`;
    badges.append(projects);
    (paper.tags || []).slice(0, 3).forEach((tag) => {
      const badge = document.createElement("span");
      badge.textContent = tag;
      badges.append(badge);
    });
    if (paper.note_count) {
      const notes = document.createElement("span");
      notes.textContent = `${paper.note_count} 条笔记`;
      badges.append(notes);
    }
    button.append(title, meta, badges);
    button.addEventListener("click", () => selectLibraryPaper(paper.library_id));
    row.append(checkbox, button);
    elements.libraryList.append(row);
  });
}

function renderLibraryDetail(detail) {
  const paper = detail.paper;
  elements.libraryDetail.replaceChildren();

  const header = document.createElement("header");
  header.className = "library-detail-header";
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Paper detail";
  const title = document.createElement("h3");
  title.textContent = paper.title;
  const meta = document.createElement("p");
  meta.className = "library-detail-meta";
  meta.textContent = libraryPaperMeta(paper);
  header.append(eyebrow, title, meta);

  const controls = document.createElement("div");
  controls.className = "library-detail-controls";
  const star = document.createElement("button");
  star.type = "button";
  star.className = "secondary";
  star.append(iconNode(paper.starred ? "star-off" : "star"));
  star.append(document.createTextNode(paper.starred ? "取消重点" : "标为重点"));
  star.addEventListener("click", () => updateLibraryPaper(paper.library_id, {
    starred: !paper.starred,
  }));
  controls.append(star);
  if (!paper.archived_at) {
    const indexedPdf = (detail.attachments || []).some((attachment) => (
      String(attachment.url || "").startsWith("/api/library/attachments/")
      && attachment.full_text_status === "indexed"
    ));
    const onlineWorkspace = document.createElement("button");
    onlineWorkspace.type = "button";
    onlineWorkspace.className = indexedPdf ? "secondary" : "primary";
    onlineWorkspace.append(iconNode(indexedPdf ? "book-open-text" : "cloud-download"));
    onlineWorkspace.append(document.createTextNode(
      indexedPdf ? "打开论文工作台" : "在线获取并打开全文",
    ));
    onlineWorkspace.addEventListener("click", () => openPaperWorkspace(
      paper.library_id,
      null,
      !indexedPdf,
    ));
    controls.append(onlineWorkspace);
  }

  const metadata = document.createElement("details");
  metadata.className = "library-detail-section library-metadata-editor";
  const metadataSummary = document.createElement("summary");
  metadataSummary.textContent = "编辑元数据";
  const metadataForm = document.createElement("form");
  metadataForm.className = "library-metadata-form";
  const metadataFields = [
    ["title", "标题", paper.title, "text"],
    ["authors", "作者（逗号分隔）", (paper.authors || []).join(", "), "text"],
    ["year", "年份", paper.year || "", "number"],
    ["doi", "DOI", paper.doi || "", "text"],
    ["url", "来源链接", paper.url || "", "url"],
    ["source", "来源", paper.source || "", "text"],
    ["tags", "标签（逗号分隔）", (paper.tags || []).join(", "), "text"],
  ];
  metadataFields.forEach(([name, labelText, value, type]) => {
    const label = document.createElement("label");
    label.className = "field";
    const caption = document.createElement("span");
    caption.textContent = labelText;
    const input = document.createElement("input");
    input.name = name;
    input.type = type;
    input.value = value;
    label.append(caption, input);
    metadataForm.append(label);
  });
  const abstractLabel = document.createElement("label");
  abstractLabel.className = "field library-metadata-wide";
  const abstractCaption = document.createElement("span");
  abstractCaption.textContent = "摘要";
  const abstractInput = document.createElement("textarea");
  abstractInput.name = "abstract";
  abstractInput.rows = 6;
  abstractInput.value = paper.abstract || "";
  abstractLabel.append(abstractCaption, abstractInput);
  const saveMetadata = document.createElement("button");
  saveMetadata.type = "submit";
  saveMetadata.className = "primary";
  saveMetadata.textContent = "保存元数据";
  metadataForm.append(abstractLabel, saveMetadata);
  metadataForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(metadataForm);
    await updateLibraryPaper(paper.library_id, {
      title: String(data.get("title") || ""),
      authors: parseList(String(data.get("authors") || ""), true),
      year: data.get("year") ? Number(data.get("year")) : null,
      doi: String(data.get("doi") || ""),
      url: String(data.get("url") || "") || null,
      source: String(data.get("source") || "user"),
      tags: parseList(String(data.get("tags") || ""), true),
      abstract: String(data.get("abstract") || ""),
    });
  });
  metadata.append(metadataSummary, metadataForm);

  const abstract = document.createElement("section");
  abstract.className = "library-detail-section";
  const abstractTitle = document.createElement("h4");
  abstractTitle.textContent = "摘要";
  const abstractText = document.createElement("p");
  abstractText.textContent = paper.abstract || "暂无摘要。";
  abstract.append(abstractTitle, abstractText);

  const collections = document.createElement("section");
  collections.className = "library-detail-section";
  const collectionsTitle = document.createElement("h4");
  collectionsTitle.textContent = "文件夹";
  collections.append(collectionsTitle);
  if (!(state.libraryOverview.collections || []).length) {
    const empty = document.createElement("p");
    empty.textContent = "尚未创建文件夹。";
    collections.append(empty);
  }
  (state.libraryOverview.collections || []).forEach((collection) => {
    const label = document.createElement("label");
    label.className = "library-collection-check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = (detail.collection_ids || []).includes(collection.collection_id);
    input.addEventListener("change", async () => {
      await api("/api/library/bulk", {
        method: "POST",
        body: JSON.stringify({
          library_ids: [paper.library_id],
          action: input.checked ? "add_collection" : "remove_collection",
          value: collection.collection_id,
        }),
      });
      await loadLibrary();
    });
    label.append(input, document.createTextNode(collection.name));
    collections.append(label);
  });

  const notes = document.createElement("section");
  notes.className = "library-detail-section";
  const notesTitle = document.createElement("h4");
  notesTitle.textContent = `阅读笔记（${(detail.notes || []).length}）`;
  notes.append(notesTitle);
  (detail.notes || []).forEach((note) => {
    const item = document.createElement("article");
    item.className = "library-note";
    const text = document.createElement("p");
    text.textContent = note.content;
    const noteActions = document.createElement("div");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "quiet-button";
    edit.textContent = "编辑";
    edit.addEventListener("click", async () => {
      const content = window.prompt("编辑笔记", note.content);
      if (!content || content.trim() === note.content) return;
      await api(`/api/library/papers/${encodeURIComponent(paper.library_id)}/notes/${encodeURIComponent(note.note_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ content }),
      });
      await selectLibraryPaper(paper.library_id);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "quiet-button";
    remove.textContent = "删除";
    remove.addEventListener("click", async () => {
      await api(`/api/library/notes/${encodeURIComponent(note.note_id)}`, { method: "DELETE" });
      await selectLibraryPaper(paper.library_id);
    });
    noteActions.append(edit, remove);
    item.append(text, noteActions);
    notes.append(item);
  });
  const noteComposer = document.createElement("form");
  noteComposer.className = "library-note-composer";
  const noteInput = document.createElement("textarea");
  noteInput.rows = 3;
  noteInput.placeholder = "记录方法、结论、质疑或可复用的写作线索";
  const addNote = document.createElement("button");
  addNote.type = "submit";
  addNote.className = "secondary";
  addNote.textContent = "添加笔记";
  noteComposer.append(noteInput, addNote);
  noteComposer.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!noteInput.value.trim()) return;
    await api(`/api/library/papers/${encodeURIComponent(paper.library_id)}/notes`, {
      method: "POST",
      body: JSON.stringify({ content: noteInput.value.trim() }),
    });
    await selectLibraryPaper(paper.library_id);
  });
  notes.append(noteComposer);

  const evidence = document.createElement("section");
  evidence.className = "library-detail-section";
  const evidenceTitle = document.createElement("h4");
  evidenceTitle.textContent = `项目证据（${(detail.evidence || []).length}）`;
  evidence.append(evidenceTitle);
  if (!(detail.evidence || []).length) {
    const empty = document.createElement("p");
    empty.textContent = "还没有从研究项目提取出 PaperCard。";
    evidence.append(empty);
  }
  (detail.evidence || []).forEach((card) => {
    const cardNode = document.createElement("article");
    cardNode.className = "library-evidence-card";
    const methods = document.createElement("strong");
    methods.textContent = (card.methods || []).join("、") || "研究方法待补充";
    const findings = document.createElement("ul");
    (card.findings || []).slice(0, 4).forEach((finding) => {
      const item = document.createElement("li");
      item.textContent = finding.claim || finding.quote || "";
      findings.append(item);
    });
    cardNode.append(methods, findings);
    evidence.append(cardNode);
  });

  const analyses = document.createElement("section");
  analyses.className = "library-detail-section";
  const analysisArtifacts = detail.analyses || [];
  const analysesTitle = document.createElement("h4");
  analysesTitle.textContent = `AI 精读卡（${analysisArtifacts.length}）`;
  analyses.append(analysesTitle);
  if (!analysisArtifacts.length) {
    const empty = document.createElement("p");
    empty.textContent = "上传可提取文本的 PDF 后，将在这里生成方法、数据集、结论、局限和带页码原文。";
    analyses.append(empty);
  } else {
    const latest = analysisArtifacts[0];
    const payload = latest.payload || {};
    const card = document.createElement("article");
    card.className = "library-analysis-card";
    const mode = document.createElement("span");
    mode.className = "library-analysis-mode";
    mode.textContent = latest.mode === "agent" ? "AI 精读" : "本地索引";
    const summary = document.createElement("p");
    summary.textContent = payload.summary || "尚未生成摘要。";
    card.append(mode, summary);
    const dimensions = [
      ["方法", payload.methods || []],
      ["数据集", payload.datasets || []],
      ["局限", payload.limitations || []],
    ];
    dimensions.forEach(([label, values]) => {
      if (!values.length) return;
      const row = document.createElement("div");
      row.className = "library-analysis-row";
      const strong = document.createElement("strong");
      strong.textContent = label;
      const text = document.createElement("span");
      text.textContent = values.join("；");
      row.append(strong, text);
      card.append(row);
    });
    if ((payload.findings || []).length) {
      const findingsTitle = document.createElement("strong");
      findingsTitle.textContent = "可审核结论";
      const findings = document.createElement("ol");
      findings.className = "library-analysis-findings";
      payload.findings.forEach((finding) => {
        const item = document.createElement("li");
        const claim = document.createElement("p");
        claim.textContent = finding.claim || "结论";
        const quote = document.createElement("blockquote");
        quote.textContent = `${finding.quote || ""}${finding.page ? `（第 ${finding.page} 页）` : ""}`;
        item.append(claim, quote);
        findings.append(item);
      });
      card.append(findingsTitle, findings);
    }
    analyses.append(card);
  }

  const projects = document.createElement("section");
  projects.className = "library-detail-section";
  const projectsTitle = document.createElement("h4");
  projectsTitle.textContent = `参与项目（${(detail.projects || []).length}）`;
  projects.append(projectsTitle);
  if (!(detail.projects || []).length) {
    const empty = document.createElement("p");
    empty.textContent = "尚未关联研究项目。";
    projects.append(empty);
  }
  (detail.projects || []).forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-project-link";
    button.textContent = `${item.project.topic} · ${item.relation.status}`;
    button.addEventListener("click", () => loadProject(item.project.project_id));
    projects.append(button);
  });
  const addProject = document.createElement("button");
  addProject.type = "button";
  addProject.className = "secondary library-add-project";
  addProject.textContent = "加入现有项目";
  addProject.addEventListener("click", async () => {
    const available = state.projects.filter(
      (project) => !(detail.projects || []).some((item) => item.project.project_id === project.project_id),
    );
    if (!available.length) {
      notify("没有可加入的其它项目", true);
      return;
    }
    const menu = available.map((project, index) => `${index + 1}. ${project.topic}`).join("\n");
    const choice = Number(window.prompt(`输入项目序号：\n${menu}`, "1"));
    const project = available[choice - 1];
    if (!project) return;
    await api("/api/library/bulk", {
      method: "POST",
      body: JSON.stringify({ library_ids: [paper.library_id], action: "add_project", value: project.project_id }),
    });
    await selectLibraryPaper(paper.library_id);
  });
  projects.append(addProject);

  const attachments = document.createElement("section");
  attachments.className = "library-detail-section";
  const attachmentsTitle = document.createElement("h4");
  attachmentsTitle.textContent = `附件与全文（${(detail.attachments || []).length}）`;
  attachments.append(attachmentsTitle);
  (detail.attachments || []).forEach((attachment) => {
    const row = document.createElement("div");
    row.className = "library-attachment";
    const link = document.createElement("a");
    link.href = safeLibraryResourceUrl(attachment.url) || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = attachment.name;
    const statusText = document.createElement("span");
    const statusLabels = {
      linked: "已链接",
      uploaded: "等待解析",
      extracting: "正在解析与精读",
      indexed: `已索引 · ${attachment.page_count || 0} 页 / ${attachment.chunk_count || 0} 段`,
      failed: "解析失败",
      ready: "待升级索引",
      unavailable: "不可用",
    };
    statusText.textContent = statusLabels[attachment.full_text_status] || attachment.full_text_status;
    if (attachment.error) statusText.title = attachment.error;
    const rowActions = document.createElement("div");
    rowActions.className = "library-attachment-row-actions";
    const isUploaded = String(attachment.url || "").startsWith("/api/library/attachments/");
    const isPdf = String(attachment.media_type || "").toLowerCase() === "application/pdf"
      || String(attachment.name || "").toLowerCase().endsWith(".pdf");
    if (isUploaded && isPdf) {
      const openWorkspace = document.createElement("button");
      openWorkspace.type = "button";
      openWorkspace.className = "primary compact-button";
      openWorkspace.append(iconNode("book-open-text"));
      openWorkspace.append(document.createTextNode("打开论文工作台"));
      openWorkspace.addEventListener("click", () => openPaperWorkspace(
        paper.library_id,
        attachment.attachment_id,
      ));
      rowActions.append(openWorkspace);
    }
    if (isUploaded && ["uploaded", "failed", "ready"].includes(attachment.full_text_status)) {
      const ingest = document.createElement("button");
      ingest.type = "button";
      ingest.className = "quiet-button";
      ingest.append(iconNode("scan-text"));
      ingest.append(document.createTextNode(attachment.full_text_status === "failed" ? "重试解析" : "解析全文"));
      ingest.addEventListener("click", async () => {
        ingest.disabled = true;
        try {
          const payload = await api(`/api/library/attachments/${encodeURIComponent(attachment.attachment_id)}/ingest`, {
            method: "POST",
          });
          const status = payload.data?.attachment?.full_text_status;
          notify(status === "indexed" ? "PDF 已完成索引与精读" : "PDF 解析未成功，请查看状态详情", status !== "indexed");
          await selectLibraryPaper(paper.library_id);
        } catch (error) {
          notify(`解析失败：${error.message}`, true);
        } finally {
          ingest.disabled = false;
        }
      });
      rowActions.append(ingest);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "quiet-button";
    remove.textContent = "移除";
    remove.addEventListener("click", async () => {
      await api(`/api/library/attachments/${encodeURIComponent(attachment.attachment_id)}`, { method: "DELETE" });
      await selectLibraryPaper(paper.library_id);
    });
    rowActions.append(remove);
    row.append(link, statusText, rowActions);
    attachments.append(row);
  });
  const addAttachment = document.createElement("button");
  addAttachment.type = "button";
  addAttachment.className = "secondary";
  addAttachment.textContent = "添加附件链接";
  addAttachment.addEventListener("click", async () => {
    const url = window.prompt("输入 PDF 或资料链接");
    if (!url) return;
    const name = window.prompt("附件名称", url.split("/").pop() || "全文链接");
    if (!name) return;
    await api(`/api/library/papers/${encodeURIComponent(paper.library_id)}/attachments`, {
      method: "POST",
      body: JSON.stringify({ name, url }),
    });
    await selectLibraryPaper(paper.library_id);
  });
  const uploadInput = document.createElement("input");
  uploadInput.type = "file";
  uploadInput.accept = "application/pdf,.pdf";
  uploadInput.hidden = true;
  const uploadAttachment = document.createElement("button");
  uploadAttachment.type = "button";
  uploadAttachment.className = "primary";
  uploadAttachment.textContent = "上传 PDF";
  uploadAttachment.addEventListener("click", () => uploadInput.click());
  uploadInput.addEventListener("change", async () => {
    const file = uploadInput.files?.[0];
    if (!file) return;
    try {
      const params = new URLSearchParams({
        filename: file.name,
        media_type: file.type || "application/pdf",
      });
      const response = await fetch(
        `/api/library/papers/${encodeURIComponent(paper.library_id)}/attachments/upload?${params.toString()}`,
        {
          method: "POST",
          headers: { "Content-Type": file.type || "application/octet-stream" },
          body: file,
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(errorMessage(payload, `上传失败（HTTP ${response.status}）`));
      await selectLibraryPaper(paper.library_id);
      const status = payload.data?.full_text_status;
      notify(
        status === "indexed"
          ? "PDF 已上传，并完成分页索引与精读"
          : "PDF 已上传，但自动解析未成功；可查看错误后重试",
        status !== "indexed",
      );
    } catch (error) {
      notify(`上传失败：${error.message}`, true);
    } finally {
      uploadInput.value = "";
    }
  });
  const attachmentActions = document.createElement("div");
  attachmentActions.className = "library-attachment-actions";
  attachmentActions.append(uploadAttachment, addAttachment, uploadInput);
  attachments.append(attachmentActions);

  const actions = document.createElement("div");
  actions.className = "library-detail-actions";
  if (safeHttpUrl(paper.url)) {
    const source = document.createElement("a");
    source.className = "secondary";
    source.href = safeHttpUrl(paper.url);
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.textContent = "查看来源";
    actions.append(source);
  }
  if (paper.archived_at) {
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "secondary";
    restore.textContent = "恢复文献";
    restore.addEventListener("click", async () => {
      await api(`/api/library/papers/${encodeURIComponent(paper.library_id)}/restore`, { method: "POST" });
      state.selectedLibraryId = null;
      await loadLibrary();
    });
    const permanent = document.createElement("button");
    permanent.type = "button";
    permanent.className = "danger-link";
    permanent.textContent = "永久删除";
    permanent.addEventListener("click", async () => {
      if (!window.confirm(`永久删除“${paper.title}”及其笔记和附件？`)) return;
      await api(`/api/library/papers/${encodeURIComponent(paper.library_id)}/permanent`, { method: "DELETE" });
      state.selectedLibraryId = null;
      await loadLibrary();
    });
    actions.append(restore, permanent);
  } else {
    const archive = document.createElement("button");
    archive.type = "button";
    archive.className = "danger-link";
    archive.textContent = "移入回收站";
    archive.addEventListener("click", () => archiveLibraryPaper(paper.library_id, paper.title));
    actions.append(archive);
  }

  elements.libraryDetail.append(
    header,
    controls,
    metadata,
    abstract,
    collections,
    notes,
    evidence,
    analyses,
    projects,
    attachments,
    actions,
  );
  refreshIcons();
}

function setPaperTab(tab) {
  document.querySelectorAll("[data-paper-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.paperTab === tab);
  });
  elements.paperAskPanel.hidden = tab !== "ask";
  elements.paperAnnotationsPanel.hidden = tab !== "annotations";
  elements.paperCardPanel.hidden = tab !== "card";
}

function clearPaperSelection() {
  state.paperSelection = null;
  elements.paperSelectionBar.hidden = true;
  elements.clearPaperSelection.hidden = true;
  elements.paperAskContext.querySelector("strong").textContent = "论文全文";
  elements.paperAskContext.querySelector("p").textContent = "回答将基于当前论文，并给出可点击的引用页码。";
  window.getSelection()?.removeAllRanges();
}

function setPaperSelection(selection) {
  state.paperSelection = selection;
  elements.paperSelectionPage.textContent = String(selection.page);
  elements.paperSelectionPreview.textContent = selection.text;
  elements.paperSelectionBar.hidden = false;
}

function capturePaperSelection() {
  const selection = window.getSelection();
  const text = selection?.toString().replace(/\s+/g, " ").trim() || "";
  if (!text || !selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  const start = range.startContainer.parentElement?.closest(".pdf-page");
  const end = range.endContainer.parentElement?.closest(".pdf-page");
  if (!start || start !== end) {
    if (start || end) notify("第一版暂支持在同一页内选择文本", true);
    return;
  }
  const pageRect = start.getBoundingClientRect();
  const rects = [...range.getClientRects()]
    .filter((rect) => rect.width > 1 && rect.height > 1)
    .map((rect) => ({
      x: Math.max(0, (rect.left - pageRect.left) / pageRect.width),
      y: Math.max(0, (rect.top - pageRect.top) / pageRect.height),
      width: Math.min(1, rect.width / pageRect.width),
      height: Math.min(1, rect.height / pageRect.height),
    }));
  if (!rects.length) return;
  const pageText = start.querySelector(".pdf-text-layer")?.textContent || "";
  const textIndex = pageText.indexOf(text);
  setPaperSelection({
    page: Number(start.dataset.page),
    text: text.slice(0, 12000),
    prefix: textIndex >= 0 ? pageText.slice(Math.max(0, textIndex - 180), textIndex) : "",
    suffix: textIndex >= 0 ? pageText.slice(textIndex + text.length, textIndex + text.length + 180) : "",
    rects,
  });
}

function renderPaperReadingCard() {
  const analyses = (state.paperWorkspace?.analyses || [])
    .filter((item) => item.kind === "PaperCard");
  elements.paperReadingCard.replaceChildren();
  if (!analyses.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "尚未生成精读卡。点击页面顶部的“生成精读卡”开始分析。";
    elements.paperReadingCard.append(empty);
    return;
  }
  const card = analyses[0].payload || {};
  const level = document.createElement("span");
  level.className = "library-analysis-mode";
  level.textContent = card.evidence_level === "abstract" ? "摘要级精读卡" : "全文级精读卡";
  const summaryTitle = document.createElement("h3");
  summaryTitle.textContent = "研究摘要";
  const summary = document.createElement("p");
  summary.textContent = card.summary || "尚未提取摘要";
  elements.paperReadingCard.append(level, summaryTitle, summary);
  [["方法", card.methods], ["数据集", card.datasets], ["局限", card.limitations], ["关键词", card.keywords]]
    .forEach(([label, values]) => {
      const heading = document.createElement("h4");
      heading.textContent = label;
      const list = document.createElement("ul");
      (values || []).forEach((value) => {
        const item = document.createElement("li");
        item.textContent = value;
        list.append(item);
      });
      if (!list.children.length) {
        const item = document.createElement("li");
        item.textContent = "尚未提取";
        list.append(item);
      }
      elements.paperReadingCard.append(heading, list);
    });
  const findingsTitle = document.createElement("h4");
  findingsTitle.textContent = "主要发现";
  elements.paperReadingCard.append(findingsTitle);
  (card.findings || []).forEach((finding) => {
    const article = document.createElement("article");
    article.className = "paper-card-finding";
    const claim = document.createElement("strong");
    claim.textContent = finding.claim || "研究发现";
    const quote = document.createElement("blockquote");
    quote.textContent = finding.quote || "";
    article.append(claim, quote);
    if (finding.page) {
      const page = document.createElement("button");
      page.type = "button";
      page.className = "paper-page-link";
      page.textContent = `第 ${finding.page} 页`;
      page.addEventListener("click", () => scrollToPaperPage(finding.page));
      article.append(page);
    } else if (finding.source_scope === "abstract") {
      const source = document.createElement("span");
      source.className = "library-analysis-mode";
      source.textContent = "来源：论文摘要";
      article.append(source);
    }
    elements.paperReadingCard.append(article);
  });
  refreshIcons();
}

function renderPaperAnnotations() {
  const annotations = state.paperWorkspace?.annotations || [];
  elements.paperAnnotationsList.replaceChildren();
  if (!annotations.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "选择 PDF 文本后，可以添加高亮、普通批注，或把问答保存到这里。";
    elements.paperAnnotationsList.append(empty);
  }
  annotations.forEach((annotation) => {
    const item = document.createElement("article");
    item.className = `paper-annotation paper-annotation-${annotation.kind}`;
    const header = document.createElement("header");
    const kind = document.createElement("strong");
    kind.textContent = { highlight: "高亮", note: "批注", qa: "问答" }[annotation.kind] || "批注";
    const actions = document.createElement("div");
    if (annotation.page) {
      const page = document.createElement("button");
      page.type = "button";
      page.className = "paper-page-link";
      page.textContent = `第 ${annotation.page} 页`;
      page.addEventListener("click", () => scrollToPaperPage(annotation.page));
      actions.append(page);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button";
    remove.setAttribute("aria-label", "删除批注");
    remove.append(iconNode("trash-2"));
    remove.addEventListener("click", () => deletePaperAnnotation(annotation.annotation_id));
    actions.append(remove);
    header.append(kind, actions);
    item.append(header);
    if (annotation.selected_text) {
      const quote = document.createElement("blockquote");
      quote.textContent = annotation.selected_text;
      item.append(quote);
    }
    if (annotation.content) {
      const content = document.createElement("p");
      content.textContent = annotation.content;
      item.append(content);
    }
    if (annotation.question) {
      const question = document.createElement("p");
      question.className = "paper-annotation-question";
      question.textContent = `问：${annotation.question}`;
      item.append(question);
    }
    if (annotation.answer) {
      const answer = document.createElement("p");
      answer.className = "paper-annotation-answer";
      answer.textContent = annotation.answer;
      item.append(answer);
    }
    elements.paperAnnotationsList.append(item);
  });
  drawPaperHighlights();
  refreshIcons();
}

function drawPaperHighlights() {
  elements.paperPdfPages.querySelectorAll(".paper-highlight-overlay").forEach((node) => node.remove());
  (state.paperWorkspace?.annotations || []).forEach((annotation) => {
    if (!annotation.page || !(annotation.rects || []).length) return;
    const page = elements.paperPdfPages.querySelector(`.pdf-page[data-page="${annotation.page}"]`);
    if (!page) return;
    annotation.rects.forEach((rect) => {
      const mark = document.createElement("button");
      mark.type = "button";
      mark.className = `paper-highlight-overlay paper-highlight-${annotation.kind}`;
      mark.style.left = `${rect.x * 100}%`;
      mark.style.top = `${rect.y * 100}%`;
      mark.style.width = `${rect.width * 100}%`;
      mark.style.height = `${rect.height * 100}%`;
      mark.title = annotation.content || annotation.question || annotation.selected_text || "论文批注";
      mark.addEventListener("click", () => {
        setPaperTab("annotations");
        elements.paperAnnotationsList.querySelectorAll(".paper-annotation").forEach((item) => item.classList.remove("is-target"));
        const index = (state.paperWorkspace.annotations || []).findIndex((item) => item.annotation_id === annotation.annotation_id);
        const target = elements.paperAnnotationsList.children[index];
        target?.classList.add("is-target");
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      page.append(mark);
    });
  });
}

async function savePaperAnnotation(kind, extra = {}) {
  const workspace = state.paperWorkspace;
  if (!workspace) return null;
  const selection = state.paperSelection || {};
  const payload = await api(`/api/library/papers/${encodeURIComponent(workspace.paper.library_id)}/annotations`, {
    method: "POST",
    body: JSON.stringify({
      kind,
      attachment_id: workspace.workspace_attachment?.attachment_id || null,
      page: selection.page || null,
      selected_text: selection.text || "",
      prefix: selection.prefix || "",
      suffix: selection.suffix || "",
      rects: selection.rects || [],
      color: "yellow",
      content: "",
      question: "",
      answer: "",
      citations: [],
      ...extra,
    }),
  });
  workspace.annotations = [...(workspace.annotations || []), payload.data];
  renderPaperAnnotations();
  return payload.data;
}

async function deletePaperAnnotation(annotationId) {
  if (!window.confirm("删除这条批注？")) return;
  await api(`/api/library/annotations/${encodeURIComponent(annotationId)}`, { method: "DELETE" });
  state.paperWorkspace.annotations = (state.paperWorkspace.annotations || [])
    .filter((item) => item.annotation_id !== annotationId);
  renderPaperAnnotations();
  notify("批注已删除");
}

function scrollToPaperPage(pageNumber) {
  const page = elements.paperPdfPages.querySelector(`.pdf-page[data-page="${pageNumber}"]`);
  if (!page) return;
  page.scrollIntoView({ behavior: "smooth", block: "start" });
  page.classList.add("is-cited");
  window.setTimeout(() => page.classList.remove("is-cited"), 1600);
}

function renderPaperAnswer(answer) {
  elements.paperAnswer.replaceChildren();
  elements.paperAnswer.hidden = false;
  const heading = document.createElement("h3");
  heading.textContent = answer.scope === "selection" ? "选文回答" : "全文回答";
  const mode = document.createElement("p");
  mode.className = "muted";
  mode.textContent = answer.mode === "agent" && answer.context_scope === "full_text"
    ? `LLM 全文分析 · 已发送 ${answer.pages_sent || 0} 页`
    : "本地证据检索（LLM 全文分析当前不可用）";
  const text = document.createElement("p");
  text.className = "paper-answer-text";
  text.textContent = answer.answer || "未获得可追溯回答。";
  elements.paperAnswer.append(heading, mode, text);
  if ((answer.citations || []).length) {
    const citations = document.createElement("ol");
    citations.className = "paper-citations";
    answer.citations.forEach((citation) => {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `${citation.citation || ""} ${citation.title || "论文证据"}`.trim();
      const quote = document.createElement("blockquote");
      quote.textContent = citation.quote || "";
      item.append(title, quote);
      if (citation.page) {
        const page = document.createElement("button");
        page.type = "button";
        page.className = "paper-page-link";
        page.textContent = `跳转第 ${citation.page} 页`;
        page.addEventListener("click", () => scrollToPaperPage(citation.page));
        item.append(page);
      }
      citations.append(item);
    });
    elements.paperAnswer.append(citations);
  }
  const save = document.createElement("button");
  save.type = "button";
  save.className = "secondary paper-save-answer";
  save.append(iconNode("message-square-plus"), document.createTextNode("将问答保存为批注"));
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      await savePaperAnnotation("qa", {
        page: answer.selection?.page || null,
        selected_text: answer.selection?.text || "",
        prefix: answer.selection?.prefix || "",
        suffix: answer.selection?.suffix || "",
        rects: answer.selection_rects || [],
        question: answer.question,
        answer: answer.answer,
        citations: answer.citations || [],
      });
      save.textContent = "已保存为批注";
      notify("问答已加入论文批注");
    } catch (error) {
      save.disabled = false;
      notify(`保存失败：${error.message}`, true);
    }
  });
  elements.paperAnswer.append(save);
  refreshIcons();
}

async function renderPdfPage(pageNumber, sessionId) {
  const page = await state.paperPdf.getPage(pageNumber);
  if (sessionId !== state.paperRenderSession) return;
  const viewport = page.getViewport({ scale: 1.25 * state.paperZoom });
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  const wrapper = document.createElement("article");
  wrapper.className = "pdf-page";
  wrapper.dataset.page = String(pageNumber);
  wrapper.style.width = `${viewport.width}px`;
  wrapper.style.height = `${viewport.height}px`;
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(viewport.width * pixelRatio);
  canvas.height = Math.floor(viewport.height * pixelRatio);
  canvas.style.width = `${viewport.width}px`;
  canvas.style.height = `${viewport.height}px`;
  const context = canvas.getContext("2d", { alpha: false });
  const textLayer = document.createElement("div");
  textLayer.className = "pdf-text-layer";
  const pageLabel = document.createElement("span");
  pageLabel.className = "pdf-page-number";
  pageLabel.textContent = String(pageNumber);
  wrapper.append(canvas, textLayer, pageLabel);
  elements.paperPdfPages.append(wrapper);
  await page.render({
    canvasContext: context,
    viewport,
    transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
  }).promise;
  const textContent = await page.getTextContent();
  const styles = textContent.styles || {};
  textContent.items.forEach((item) => {
    if (!item.str) return;
    const transform = state.paperPdfJs.Util.transform(viewport.transform, item.transform);
    const angle = Math.atan2(transform[1], transform[0]);
    const fontHeight = Math.hypot(transform[2], transform[3]);
    const fontStyle = styles[item.fontName] || {};
    const ascent = fontStyle.ascent ?? (fontStyle.descent ? 1 + fontStyle.descent : 0.8);
    const span = document.createElement("span");
    span.textContent = item.str;
    span.style.left = `${transform[4]}px`;
    span.style.top = `${transform[5] - fontHeight * ascent}px`;
    span.style.fontSize = `${fontHeight}px`;
    span.style.fontFamily = fontStyle.fontFamily || "sans-serif";
    span.style.transform = angle ? `rotate(${angle}rad)` : "none";
    textLayer.append(span);
    const measured = span.getBoundingClientRect().width;
    const expected = Math.abs(item.width * viewport.scale);
    if (measured > 0 && expected > 0) {
      const rotation = angle ? `rotate(${angle}rad) ` : "";
      span.style.transform = `${rotation}scaleX(${expected / measured})`;
    }
  });
}

async function renderPaperPdf() {
  if (!state.paperPdf) return;
  const sessionId = ++state.paperRenderSession;
  elements.paperPdfPages.replaceChildren();
  elements.paperZoomLabel.textContent = `${Math.round(state.paperZoom * 100)}%`;
  const pageCount = state.paperPdf.numPages;
  for (let page = 1; page <= pageCount; page += 1) {
    if (sessionId !== state.paperRenderSession) return;
    elements.paperPageStatus.textContent = `正在渲染第 ${page} / ${pageCount} 页`;
    await renderPdfPage(page, sessionId);
  }
  if (sessionId !== state.paperRenderSession) return;
  elements.paperPageStatus.textContent = `全文共 ${pageCount} 页 · 可选择文本提问或批注`;
  drawPaperHighlights();
}

async function loadPaperPdf(attachment) {
  elements.paperPdfPages.innerHTML = '<div class="paper-pdf-placeholder"><p>正在通过 PDF.js 加载全文…</p></div>';
  try {
    if (!state.paperPdfJs) {
      state.paperPdfJs = await import("/ui-assets/vendor/pdfjs/pdf.mjs?v=6.1.200");
      state.paperPdfJs.GlobalWorkerOptions.workerSrc = "/ui-assets/vendor/pdfjs/pdf.worker.mjs?v=6.1.200";
    }
    if (state.paperPdfLoadingTask) {
      await state.paperPdfLoadingTask.destroy();
      state.paperPdfLoadingTask = null;
    } else if (state.paperPdf?.cleanup) {
      await state.paperPdf.cleanup();
    }
    state.paperPdf = null;
    const loadingTask = state.paperPdfJs.getDocument({
      url: attachment.url,
      cMapUrl: "/ui-assets/vendor/pdfjs/cmaps/",
      cMapPacked: true,
      wasmUrl: "/ui-assets/vendor/pdfjs/wasm/",
      standardFontDataUrl: "/ui-assets/vendor/pdfjs/standard_fonts/",
    });
    state.paperPdfLoadingTask = loadingTask;
    const documentProxy = await loadingTask.promise;
    if (state.paperPdfLoadingTask !== loadingTask) {
      await loadingTask.destroy();
      return;
    }
    state.paperPdf = documentProxy;
    await renderPaperPdf();
  } catch (error) {
    elements.paperPageStatus.textContent = "PDF 加载失败";
    elements.paperPdfPages.replaceChildren();
    const message = document.createElement("div");
    message.className = "paper-pdf-placeholder is-error";
    message.textContent = `无法加载 PDF：${error.message}`;
    elements.paperPdfPages.append(message);
  }
}

async function openPaperWorkspace(libraryId, attachmentId = null, forceAcquire = false) {
  showWorkspace("paper");
  elements.paperWorkspaceTitle.textContent = "正在加载论文…";
  elements.paperPdfPages.innerHTML = '<div class="paper-pdf-placeholder"><p>正在准备论文全文…</p></div>';
  clearPaperSelection();
  elements.paperAnswer.hidden = true;
  try {
    const payload = await api(`/api/library/papers/${encodeURIComponent(libraryId)}/workspace`);
    let workspace = payload.data;
    if (attachmentId) {
      workspace.workspace_attachment = (workspace.attachments || [])
        .find((item) => item.attachment_id === attachmentId) || workspace.workspace_attachment;
    }
    const hasInternalPdf = (workspace.attachments || []).some((item) => (
      String(item.url || "").startsWith("/api/library/attachments/")
      && (String(item.media_type || "").toLowerCase() === "application/pdf"
        || String(item.name || "").toLowerCase().endsWith(".pdf"))
    ));
    if (!attachmentId && (forceAcquire || !hasInternalPdf)) {
      elements.paperWorkspaceTitle.textContent = workspace.paper.title;
      elements.paperPageStatus.textContent = "正在查找可公开获取的论文全文…";
      const acquisition = await api(
        `/api/library/papers/${encodeURIComponent(libraryId)}/workspace/acquire-full-text`,
        { method: "POST" },
      );
      const result = acquisition.data || {};
      if (!["acquired", "existing", "failed"].includes(result.status)) {
        const attempted = (result.attempted_urls || []).length;
        const firstError = result.errors?.[0]?.error || "";
        const reason = firstError.includes("403")
          ? "来源站点拒绝后端下载（HTTP 403）"
          : firstError === "response_is_not_pdf"
            ? "来源地址返回了网页而非 PDF"
            : firstError;
        notify(
          attempted
            ? `尝试了 ${attempted} 个开放获取地址，均未取得有效 PDF${reason ? `：${reason}` : ""}`
            : "未发现可公开获取的全文，可继续使用摘要或手动上传 PDF",
          true,
        );
      } else {
        const refreshed = await api(`/api/library/papers/${encodeURIComponent(libraryId)}/workspace`);
        workspace = refreshed.data;
      }
      if (result.status === "acquired") notify("已在线获取全文并完成索引");
      if (result.status === "failed") {
        notify(result.message || result.attachment?.error || "全文已获取，但文本解析失败", true);
      }
    }
    state.paperWorkspace = workspace;
    const paper = workspace.paper;
    elements.paperWorkspaceTitle.textContent = paper.title;
    elements.paperWorkspaceMeta.textContent = [
      (paper.authors || []).join(", "),
      paper.year,
      workspace.workspace_attachment?.name,
    ].filter(Boolean).join(" · ");
    elements.exportReadingReport.href = `/api/library/papers/${encodeURIComponent(libraryId)}/workspace/report.md`;
    renderPaperAnnotations();
    renderPaperReadingCard();
    setPaperTab("ask");
    const attachment = workspace.workspace_attachment;
    if (!attachment || !String(attachment.url || "").startsWith("/api/library/attachments/")) {
      elements.paperPageStatus.textContent = "当前未加载 PDF";
      elements.paperPdfPages.innerHTML = '<div class="paper-pdf-placeholder"><p>可直接生成摘要级精读卡，或手动上传 PDF 后升级为全文级精读卡。</p></div>';
      refreshIcons();
      return;
    }
    await loadPaperPdf(attachment);
    refreshIcons();
  } catch (error) {
    elements.paperWorkspaceTitle.textContent = "单论文工作台";
    elements.paperPdfPages.replaceChildren();
    const message = document.createElement("div");
    message.className = "paper-pdf-placeholder is-error";
    message.textContent = error.message;
    elements.paperPdfPages.append(message);
    notify(`工作台加载失败：${error.message}`, true);
  }
}

async function selectLibraryPaper(libraryId) {
  state.selectedLibraryId = libraryId;
  renderLibraryList();
  try {
    const payload = await api(`/api/library/papers/${encodeURIComponent(libraryId)}`);
    renderLibraryDetail(payload.data);
  } catch (error) {
    notify(`文献详情载入失败：${error.message}`, true);
  }
}

async function loadLibrary() {
  const query = elements.librarySearch.value.trim();
  try {
    const params = new URLSearchParams({
      limit: "500",
      query,
      view: state.libraryView,
    });
    if (state.libraryCollectionId) params.set("collection_id", state.libraryCollectionId);
    const [payload, overview] = await Promise.all([
      api(`/api/library?${params.toString()}`),
      api("/api/library/overview"),
    ]);
    state.libraryPapers = payload.data || [];
    state.libraryOverview = overview.data || { counts: {}, collections: [] };
    if (
      state.selectedLibraryId &&
      !state.libraryPapers.some((paper) => paper.library_id === state.selectedLibraryId)
    ) {
      state.selectedLibraryId = null;
    }
    renderLibraryNavigation();
    renderLibraryList();
    if (state.selectedLibraryId) await selectLibraryPaper(state.selectedLibraryId);
  } catch (error) {
    notify(`文献库载入失败：${error.message}`, true);
  }
}

async function openLibrary() {
  closeMenus();
  closeInspector({ restoreFocus: false });
  showWorkspace("library");
  window.history.replaceState({}, "", `${window.location.pathname}?view=library`);
  await loadLibrary();
}

async function updateLibraryPaper(libraryId, changes) {
  try {
    await api(`/api/library/papers/${encodeURIComponent(libraryId)}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    });
    await loadLibrary();
    notify("文献状态已更新");
  } catch (error) {
    notify(`更新失败：${error.message}`, true);
  }
}

async function createLibraryCollection(parentId = null, parentName = "") {
  const promptText = parentId
    ? `在“${parentName}”中新建子文件夹`
    : "新建根文件夹名称";
  const name = window.prompt(promptText);
  if (!name?.trim()) return;
  try {
    await api("/api/library/collections", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), parent_id: parentId }),
    });
    await loadLibrary();
    notify("文件夹已创建");
  } catch (error) {
    notify(`创建失败：${error.message}`, true);
  }
}

async function editLibraryCollection(collection) {
  const name = window.prompt("修改文件夹名称", collection.name);
  if (!name?.trim()) return;
  const collections = state.libraryOverview.collections || [];
  const byId = new Map(collections.map((item) => [item.collection_id, item]));
  const isDescendant = (candidateId) => {
    let currentId = candidateId;
    const visited = new Set();
    while (currentId && !visited.has(currentId)) {
      if (currentId === collection.collection_id) return true;
      visited.add(currentId);
      currentId = byId.get(currentId)?.parent_id || null;
    }
    return false;
  };
  const parentOptions = collections.filter(
    (item) => item.collection_id !== collection.collection_id && !isDescendant(item.collection_id),
  );
  const choices = ["0. 根目录", ...parentOptions.map((item, index) => `${index + 1}. ${item.name}`)];
  const currentIndex = collection.parent_id
    ? parentOptions.findIndex((item) => item.collection_id === collection.parent_id) + 1
    : 0;
  const selectedIndex = Number(window.prompt(
    `选择父文件夹序号：\n${choices.join("\n")}`,
    String(Math.max(0, currentIndex)),
  ));
  if (!Number.isInteger(selectedIndex) || selectedIndex < 0 || selectedIndex > parentOptions.length) return;
  const parentId = selectedIndex === 0 ? null : parentOptions[selectedIndex - 1].collection_id;
  try {
    await api(`/api/library/collections/${encodeURIComponent(collection.collection_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ name: name.trim(), parent_id: parentId }),
    });
    await loadLibrary();
    notify("文件夹设置已更新");
  } catch (error) {
    notify(`更新失败：${error.message}`, true);
  }
}

async function applyLibraryBulkAction() {
  const selected = [...state.selectedLibraryIds];
  const raw = elements.libraryBulkAction.value;
  if (!selected.length || !raw) return;
  let [action, value] = raw.split(":");
  if (action === "export_bibtex" || action === "export_ris") {
    const format = action === "export_ris" ? "ris" : "bibtex";
    const link = document.createElement("a");
    link.href = `/api/library/export?format=${format}&ids=${encodeURIComponent(selected.join(","))}`;
    link.click();
    elements.libraryBulkAction.value = "";
    return;
  }
  if (action === "add_tags") {
    const input = window.prompt("输入要添加的标签（逗号分隔）");
    if (!input) return;
    value = parseList(input, true);
  } else if (action === "add_collection") {
    const choices = state.libraryOverview.collections || [];
    const menu = choices.map((item, index) => `${index + 1}. ${item.name}`).join("\n");
    const choice = Number(window.prompt(`输入文件夹序号：\n${menu}`, "1"));
    if (!choices[choice - 1]) return;
    value = choices[choice - 1].collection_id;
  } else if (action === "add_project") {
    const menu = state.projects.map((item, index) => `${index + 1}. ${item.topic}`).join("\n");
    const choice = Number(window.prompt(`输入项目序号：\n${menu}`, "1"));
    if (!state.projects[choice - 1]) return;
    value = state.projects[choice - 1].project_id;
  } else if (action === "delete") {
    if (!window.confirm(`永久删除选中的 ${selected.length} 篇文献？`)) return;
  }
  try {
    await api("/api/library/bulk", {
      method: "POST",
      body: JSON.stringify({ library_ids: selected, action, value }),
    });
    state.selectedLibraryIds.clear();
    elements.libraryBulkAction.value = "";
    await loadLibrary();
    notify("批量操作已完成");
  } catch (error) {
    notify(`批量操作失败：${error.message}`, true);
  }
}

async function findLibraryDuplicates() {
  try {
    const payload = await api("/api/library/duplicates");
    const groups = payload.data || [];
    if (!groups.length) {
      notify("没有发现疑似重复项");
      return;
    }
    const group = groups[0];
    const [primary, ...duplicates] = group.papers;
    const list = group.papers.map((paper, index) => `${index + 1}. ${paper.title}`).join("\n");
    if (!window.confirm(`发现 ${groups.length} 组疑似重复项。\n\n${list}\n\n将其合并并保留第 1 条记录？`)) return;
    for (const duplicate of duplicates) {
      await api("/api/library/merge", {
        method: "POST",
        body: JSON.stringify({ primary_id: primary.library_id, duplicate_id: duplicate.library_id }),
      });
    }
    state.selectedLibraryId = primary.library_id;
    await loadLibrary();
    notify("重复项已合并，项目、笔记和附件关联已迁移");
  } catch (error) {
    notify(`重复项检查失败：${error.message}`, true);
  }
}

function renderLibraryComparison(data) {
  elements.libraryCompareTable.replaceChildren();
  const summarize = (items, limit = 5) => {
    const values = (items || [])
      .map((item) => String(item || "").trim())
      .filter(Boolean)
      .slice(0, limit)
      .map((item) => item.length > 180 ? `${item.slice(0, 177)}…` : item);
    return values.join("；") || "—";
  };
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["文献", "年份", "方法", "数据集", "核心发现", "局限", "笔记"].forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  (data.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    const values = [
      row.paper.title,
      row.paper.year || "—",
      summarize(row.methods, 5),
      summarize(row.datasets, 5),
      summarize((row.findings || []).map((item) => item.claim || item.quote), 5),
      summarize(row.limitations, 5),
      summarize((row.notes || []).map((item) => item.content), 5),
    ];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      tr.append(cell);
    });
    body.append(tr);
  });
  table.append(head, body);
  elements.libraryCompareTable.append(table);
}

function openLibraryAssistant() {
  state.libraryAssistantScope = "all";
  elements.libraryAssistantScopeLabel.textContent = "向整个文献库提问";
  elements.libraryCompareTable.replaceChildren();
  elements.libraryAssistantAnswer.replaceChildren();
  elements.libraryAssistantAnswer.hidden = true;
  elements.libraryComparePanel.hidden = false;
  elements.libraryComparePanel.scrollIntoView({ behavior: "smooth", block: "start" });
  window.setTimeout(() => elements.libraryAssistantQuestion.focus(), 250);
}

async function compareSelectedLibraryPapers() {
  const libraryIds = [...state.selectedLibraryIds];
  if (libraryIds.length < 2 || libraryIds.length > 8) return;
  try {
    const payload = await api("/api/library/compare", {
      method: "POST",
      body: JSON.stringify({ library_ids: libraryIds }),
    });
    renderLibraryComparison(payload.data);
    state.libraryAssistantScope = "selected";
    elements.libraryAssistantScopeLabel.textContent = `向选中的 ${libraryIds.length} 篇文献提问`;
    elements.libraryComparePanel.hidden = false;
    elements.libraryComparePanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    notify(`对照载入失败：${error.message}`, true);
  }
}

async function askLibraryAssistant(event) {
  event.preventDefault();
  const question = elements.libraryAssistantQuestion.value.trim();
  if (!question) return;
  const submit = elements.libraryAssistantForm.querySelector('button[type="submit"]');
  const libraryIds = state.libraryAssistantScope === "selected"
    ? [...state.selectedLibraryIds]
    : [];
  try {
    submit.disabled = true;
    elements.libraryAssistantAnswer.hidden = false;
    elements.libraryAssistantAnswer.replaceChildren();
    const working = document.createElement("p");
    working.className = "library-assistant-working";
    working.append(iconNode("loader-circle"), document.createTextNode(" Agent 正在拆解问题并迭代取证…"));
    elements.libraryAssistantAnswer.append(working);
    refreshIcons();
    const payload = await api("/api/library/assistant", {
      method: "POST",
      body: JSON.stringify({ library_ids: libraryIds, question }),
    });
    const data = payload.data || {};
    elements.libraryAssistantAnswer.replaceChildren();
    const resultHeader = document.createElement("div");
    resultHeader.className = "library-assistant-result-header";
    const badge = document.createElement("span");
    badge.textContent = data.mode === "agent" ? "Agent 取证" : "本地检索";
    const coverage = document.createElement("p");
    coverage.textContent = data.coverage_note || "";
    resultHeader.append(badge, coverage);
    elements.libraryAssistantAnswer.append(
      resultHeader,
      renderMarkdown(data.answer || "没有形成可引用的回答。", "aw-markdown library-agent-answer"),
    );
    if ((data.citations || []).length) {
      const sourcesTitle = document.createElement("h4");
      sourcesTitle.textContent = `引用来源（${data.citations.length}）`;
      const sources = document.createElement("div");
      sources.className = "library-assistant-sources";
      data.citations.forEach((citation) => {
        const source = document.createElement("article");
        const title = document.createElement("button");
        title.type = "button";
        title.className = "quiet-button";
        title.textContent = `${citation.citation || ""} ${citation.title || "未命名文献"}${citation.page ? ` · 第 ${citation.page} 页` : ""}`;
        title.addEventListener("click", () => citation.library_id && selectLibraryPaper(citation.library_id));
        const quote = document.createElement("blockquote");
        quote.textContent = citation.quote || "";
        source.append(title, quote);
        sources.append(source);
      });
      elements.libraryAssistantAnswer.append(sourcesTitle, sources);
    }
  } catch (error) {
    notify(`整理回答失败：${error.message}`, true);
  } finally {
    submit.disabled = false;
  }
}

async function archiveLibraryPaper(libraryId, title) {
  if (!window.confirm(`将“${title}”移入回收站？已有项目关联和历史研究结果会保留。`)) return;
  try {
    await api(`/api/library/papers/${encodeURIComponent(libraryId)}`, { method: "DELETE" });
    state.selectedLibraryId = null;
    elements.libraryDetail.innerHTML = '<div class="library-detail-empty"><p>文献已移入回收站</p></div>';
    await loadLibrary();
    notify("文献已移入回收站");
  } catch (error) {
    notify(`操作失败：${error.message}`, true);
  }
}

async function importLibraryRecords() {
  const content = elements.libraryImportContent.value.trim();
  if (!content) {
    notify("请先粘贴 BibTeX 或 RIS 内容", true);
    return;
  }
  try {
    const payload = await api("/api/library/import", {
      method: "POST",
      body: JSON.stringify({
        format: elements.libraryImportFormat.value,
        content,
        tags: parseList(elements.libraryImportTags.value, true),
      }),
    });
    elements.libraryImportContent.value = "";
    await loadLibrary();
    notify(`已导入 ${payload.data?.length || 0} 篇文献`);
  } catch (error) {
    notify(`导入失败：${error.message}`, true);
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
    const isRunning = ["queued", "running"].includes(project.active_run?.status);
    const displayStage =
      isRunning
        ? "调研中"
        : project.project_id === state.projectId && continuationMode(state.snapshot) === "recovery"
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
    icon.append(
      iconNode(
        isRunning
          ? "loader-circle"
          : project.project_id === state.projectId
            ? "folder-open"
            : "folder",
      ),
    );
    icon.classList.toggle("is-running", isRunning);

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
  if (state.projectsLoading) return;
  state.projectsLoading = true;
  try {
    const payload = await api("/api/projects?limit=30");
    state.projects = payload.data || [];
    renderProjectList();
  } catch (error) {
    const message = document.createElement("p");
    message.className = "muted small sidebar-label";
    message.textContent = `项目载入失败：${error.message}`;
    elements.projectList.replaceChildren(message);
  } finally {
    state.projectsLoading = false;
  }
}

function clearProjectView() {
  stopRunTimers();
  state.projectId = null;
  state.project = null;
  state.snapshot = null;
  state.conversation = null;
  state.activeRun = null;
  state.activeRunId = null;
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

function orderedWorkflowEvents(events = []) {
  return [...events].sort((left, right) => {
    const leftId = Number(left?.event_id);
    const rightId = Number(right?.event_id);
    if (Number.isFinite(leftId) && Number.isFinite(rightId)) return leftId - rightId;
    return String(left?.created_at || "").localeCompare(String(right?.created_at || ""));
  });
}

function eventIdentity(event, index = 0) {
  return event?.event_id != null
    ? `event:${event.event_id}`
    : `event:${event?.from_stage || ""}:${event?.to_stage || ""}:${event?.actor || ""}:${event?.created_at || index}`;
}

function artifactIdentity(artifact, index = 0) {
  return artifact?.artifact_id != null
    ? `artifact:${artifact.artifact_id}`
    : `artifact:${artifact?.kind || ""}:${artifact?.created_at || index}`;
}

function deriveStepperState(stage, events = [], actualStage = stage) {
  const ordered = orderedWorkflowEvents(events);
  const latestEvent = ordered.at(-1) || null;
  const stopEvent = [...ordered].reverse().find((event) => event.to_stage === "INCONCLUSIVE");
  const terminal = stage === "INCONCLUSIVE";
  const activeStage = terminal
    ? stopEvent?.from_stage || latestEvent?.from_stage || "CREATED"
    : stage;
  const visited = new Set();

  if (ordered.length) {
    visited.add("CREATED");
    ordered.forEach((event) => {
      if (stageIndex(event.from_stage) >= 0) visited.add(event.from_stage);
      if (stageIndex(event.to_stage) >= 0) visited.add(event.to_stage);
    });
  } else {
    const current = stageIndex(activeStage);
    for (let index = 0; index <= current; index += 1) {
      visited.add(STAGES[index][0]);
    }
  }
  if (stageIndex(activeStage) >= 0) visited.add(activeStage);

  return {
    activeStage,
    terminal,
    latestEvent,
    visitedStages: [...visited],
    aligned: !latestEvent || latestEvent.to_stage === actualStage,
  };
}

function renderStepper(stage, events = [], actualStage = stage) {
  elements.stageStepper.replaceChildren();
  const progress = deriveStepperState(stage, events, actualStage);
  const current = stageIndex(progress.activeStage);
  const visited = new Set(progress.visitedStages);
  const visitCounts = new Map();
  orderedWorkflowEvents(events).forEach((event) => {
    visitCounts.set(event.to_stage, (visitCounts.get(event.to_stage) || 0) + 1);
  });
  elements.stageStepper.classList.toggle("is-running", state.busy);
  elements.stageStepper.classList.toggle("is-syncing", !progress.aligned);
  elements.stageStepper.setAttribute(
    "aria-label",
    progress.aligned ? "研究进度" : "研究进度正在与事件记录同步",
  );
  STAGES.forEach(([key, label], index) => {
    const item = document.createElement("li");
    item.className = "stage-step";
    item.dataset.stage = key;
    if (
      index > 0
      && visited.has(key)
      && visited.has(STAGES[index - 1][0])
    ) {
      item.classList.add("has-complete-connector");
    }
    if (visited.has(key) && index !== current) {
      item.classList.add(current >= 0 && index > current ? "is-revisited" : "is-complete");
    }
    if (index === current) {
      item.classList.add("is-current");
      item.setAttribute("aria-current", "step");
      if (progress.terminal) item.classList.add("is-terminal");
    }
    const visits = visitCounts.get(key) || (key === "CREATED" ? 1 : 0);
    if (visits > 1) item.title = `${label}，已到达 ${visits} 次`;
    if (index === current && progress.terminal) {
      item.title = `流程停止于${label}，项目状态为证据不足`;
    }
    item.textContent = label;
    elements.stageStepper.append(item);
  });
}

function renderProjectHeader(project, events = state.snapshot?.events || []) {
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
  renderStepper(project.stage, events);
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
  REVISION_PENDING: ["需要修订正文", "事实核查发现问题，系统将只重写被标记的章节并再次核查。"],
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
  const narrativeId = Number(latestArtifact(snapshot, "NarrativeReview")?.artifact_id || 0);
  const reports = artifactsOf(snapshot, "FactCheckReport").filter(
    (artifact) => Number(artifact.artifact_id || 0) > narrativeId,
  );
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
  if (snapshot?.project?.stage !== "INCONCLUSIVE") {
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
  if (["EXTRACTED", "SYNTHESIZED", "REVIEW_PENDING"].includes(stage)) return "pipeline";
  if (["REVIEWED", "OUTLINED", "NARRATED", "REVISION_PENDING"].includes(stage) && latestReviewPassed(snapshot)) {
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
  if (latestArtifact(snapshot, "ReviewResult")) return "EXTRACTED";
  if (latestArtifact(snapshot, "SynthesisReport")) return "REVIEW_PENDING";
  if (latestArtifact(snapshot, "PaperCard")) return "EXTRACTED";
  if (latestArtifact(snapshot, "ScreeningDecision")) return "SCREENED";
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
    renderStepper(
      effectiveRecoveryStage(snapshot),
      snapshot?.events || [],
      project.stage,
    );
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
    orderedWorkflowEvents(events).forEach((event) => {
      const item = document.createElement("li");
      item.className = "event-item";
      const fromIndex = stageIndex(event.from_stage);
      const toIndex = stageIndex(event.to_stage);
      if (event.to_stage === "INCONCLUSIVE") item.classList.add("is-terminal");
      if (fromIndex >= 0 && toIndex >= 0 && toIndex < fromIndex) {
        item.classList.add("is-return");
      }
      const transition = document.createElement("strong");
      transition.textContent = `${STAGE_LABELS[event.from_stage] || event.from_stage} 至 ${STAGE_LABELS[event.to_stage] || event.to_stage}`;
      const meta = document.createElement("span");
      const actor = ACTOR_LABELS[event.actor] || event.actor;
      const verdict = event.review_verdict ? ` · ${event.review_verdict}` : "";
      meta.textContent = `${actor}${verdict} · ${formatDate(event.created_at)}`;
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
  const canUndoDecision = Boolean(
    state.review?.can_undo && snapshot?.project?.stage !== "SEARCH_REVIEW_PENDING",
  );
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = !mode && !canUndoDecision;
  elements.undoDecision.hidden = !canUndoDecision;
  elements.continueResearch.hidden = !mode;
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
  } else if (canUndoDecision) {
    const stopped = snapshot?.project?.stage === "INCONCLUSIVE";
    elements.continueEyebrow.textContent = "Reversible decision";
    elements.continueTitle.textContent = stopped ? "项目已按审核意见停止" : "候选集已经确认";
    elements.continueText.textContent = "在后续研究尚未开始前，可以恢复到上一版候选集。";
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

function snapshotSignature(snapshot) {
  const artifacts = snapshot?.artifacts || [];
  const events = snapshot?.events || [];
  const latestArtifact = artifacts.at(-1);
  const latestEvent = events.at(-1);
  return [
    snapshot?.project?.updated_at || "",
    snapshot?.project?.stage || "",
    artifacts.length,
    latestArtifact?.artifact_id || "",
    events.length,
    latestEvent?.event_id || "",
  ].join(":");
}

function activeSnapshotRun(snapshot) {
  const run = snapshot?.active_run;
  return run && ["queued", "running"].includes(run.status) ? run : null;
}

function conversationIdForSnapshot(snapshot = state.snapshot) {
  return snapshot?.conversation?.conversation_id
    || snapshot?.project?.conversation_id
    || state.conversation?.conversation_id
    || null;
}

function applyProjectSnapshot(snapshot, { keepRunPanel = false, renderInspector = true } = {}) {
  state.snapshot = snapshot;
  state.conversation = snapshot.conversation || null;
  const projectIndex = state.projects.findIndex(
    (project) => project.project_id === snapshot.project?.project_id,
  );
  if (projectIndex >= 0) {
    state.projects[projectIndex] = {
      ...state.projects[projectIndex],
      ...snapshot.project,
      conversation: snapshot.conversation || state.projects[projectIndex].conversation,
      active_run: snapshot.active_run || null,
    };
    renderProjectList();
  }
  renderProjectHeader(snapshot.project, snapshot.events || []);
  renderProjectSummary(snapshot);
  renderStagePanels(snapshot);
  if (renderInspector) renderDetails(snapshot);
  const activeRun = activeSnapshotRun(snapshot);
  if (activeRun) {
    const isNewRun = activeRun.run_id !== state.activeRunId;
    state.activeRun = activeRun;
    state.activeRunId = activeRun.run_id;
    if (isNewRun || !state.runStartedAt) {
      beginRunSession({
        stage: snapshot.project?.stage || "CREATED",
        phase: activeRun.phase || "",
        message: activeRun.message || "研究正在后台运行",
        snapshot,
      });
    } else {
      setRunPhase(
        activeRun.phase || STAGE_RUN_PHASES[snapshot.project?.stage] || "thinking",
        activeRun.message || "",
      );
    }
    setBusy(true);
    elements.runPanel.hidden = false;
    startRunPolling();
    return;
  }
  if (state.activeRunId || state.busy) {
    if (state.runStartedAt) finishRunSession();
    state.activeRun = null;
    state.activeRunId = null;
    setBusy(false);
  }
  elements.runPanel.hidden = !keepRunPanel;
}

async function loadProject(projectId, quiet = false, force = false) {
  if (!projectId) return;
  if (projectId !== state.projectId) {
    stopRunTimers();
    state.runSessionId += 1;
    state.runStartedAt = null;
    state.activeRun = null;
    state.activeRunId = null;
    setBusy(false);
  }
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(projectId)}`);
    const snapshot = payload.data;
    await loadProjectLibrary(projectId);
    applyProjectSnapshot(snapshot);
    const hasCandidateSnapshot = (snapshot.artifacts || []).some(
      (artifact) => artifact.kind === "CandidateSetSnapshot",
    );
    if (
      ["SEARCH_REVIEW_PENDING", "SCREENED", "INCONCLUSIVE"].includes(snapshot.project.stage)
      || (snapshot.project.stage === "SEARCHED" && hasCandidateSnapshot)
    ) {
      const reviewPayload = await api(
        `/api/projects/${encodeURIComponent(projectId)}/search-review`,
      );
      if (
        snapshot.project.stage === "SEARCH_REVIEW_PENDING"
        || (snapshot.project.stage === "SEARCHED" && reviewPayload.data.manual_recovery_allowed)
      ) {
        renderReview(reviewPayload.data);
      } else {
        state.review = reviewPayload.data;
        renderStagePanels(snapshot);
      }
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
    candidate.venue,
    candidate.venue_acronym,
    candidate.ccf_rank,
    candidate.sci_quartile,
    ...(candidate.authors || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
  return haystack.includes(query.toLocaleLowerCase());
}

async function saveCandidateToLibrary(candidate) {
  if (!state.projectId || state.busy) return;
  try {
    const payload = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/library`,
      {
        method: "POST",
        body: JSON.stringify({ ...candidate, status: "candidate" }),
      },
    );
    const item = payload.data;
    [item.relation?.source_paper_id, item.paper?.paper_id, item.paper?.doi]
      .filter(Boolean)
      .forEach((value) => state.projectLibrary.set(libraryIdentity(value), item));
    renderCandidateCards();
    notify("论文已收藏到文献库");
  } catch (error) {
    notify(`收藏失败：${error.message}`, true);
  }
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

    const venue = document.createElement("div");
    venue.className = "candidate-venue";
    const venueHeading = document.createElement("div");
    venueHeading.className = "candidate-venue-heading";
    const venueType = document.createElement("span");
    venueType.className = "candidate-venue-type";
    venueType.textContent = candidate.venue_type === "conference"
      ? "会议"
      : candidate.venue_type === "journal"
        ? "期刊"
        : "出版物";
    const venueName = document.createElement("strong");
    venueName.textContent = candidate.venue || "期刊或会议信息未返回";
    venueHeading.append(venueType, venueName);

    const ratingBadges = document.createElement("div");
    ratingBadges.className = "candidate-rating-badges";
    const addRatingBadge = (text, className = "") => {
      if (!text) return;
      const badge = document.createElement("span");
      badge.className = `candidate-rating-badge ${className}`.trim();
      badge.textContent = text;
      ratingBadges.append(badge);
    };
    if (candidate.ccf_rank) {
      addRatingBadge(`CCF-${candidate.ccf_rank} · ${candidate.ccf_year || "年份未知"}版`, "is-ccf");
    }
    if (candidate.sci_quartile) {
      addRatingBadge(
        `JCR ${candidate.sci_quartile}${candidate.index_name ? ` · ${candidate.index_name}` : ""}`,
        candidate.sci_quartile === "Q1" ? "is-q1" : "",
      );
    }
    if (candidate.nature_portfolio) addRatingBadge("Nature Portfolio", "is-nature");
    if (candidate.impact_factor != null) {
      addRatingBadge(`IF ${candidate.impact_factor} · ${candidate.impact_factor_year || "年份未知"}`);
    }

    const venueExplanation = document.createElement("p");
    venueExplanation.className = "candidate-venue-explanation";
    venueExplanation.textContent = candidate.venue_rating_explanation
      || "评级库未提供可靠匹配，不推断分区、影响因子或会议评级。";
    const ratingSourceUrl = safeHttpUrl(candidate.venue_rating_source_url);
    if (ratingSourceUrl) {
      const sourceLink = document.createElement("a");
      sourceLink.href = ratingSourceUrl;
      sourceLink.target = "_blank";
      sourceLink.rel = "noopener noreferrer";
      sourceLink.textContent = candidate.venue_rating_source_label || "查看评级来源";
      venueExplanation.append(document.createTextNode(" · "), sourceLink);
    }
    venue.append(venueHeading);
    if (ratingBadges.childElementCount) venue.append(ratingBadges);
    venue.append(venueExplanation);

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

    const libraryEntry = libraryEntryForCandidate(candidate);
    const libraryButton = document.createElement("button");
    libraryButton.type = "button";
    libraryButton.className = "candidate-library-button";
    const isSaved = Boolean(libraryEntry?.paper?.saved);
    libraryButton.disabled = isSaved;
    libraryButton.append(iconNode(isSaved ? "bookmark-check" : "bookmark-plus"));
    libraryButton.append(document.createTextNode(isSaved ? "已在文献库" : "收藏到文献库"));
    libraryButton.addEventListener("click", () => saveCandidateToLibrary(candidate));
    identifiers.append(libraryButton);

    card.append(head, meta, venue, authors);
    if (agentReason) card.append(reason);
    card.append(abstract, identifiers);
    elements.candidateGrid.append(card);
  });
  refreshIcons();
}

function acceptedPaperCount(selectedIds, addedPapers) {
  const acceptedIds = new Set([...selectedIds].map(normalizePaperId));
  addedPapers.forEach((paper) => {
    // Match the backend's _candidate_id precedence so a paper carrying both
    // paper_id and DOI is counted once.
    const id = paper.paper_id || paper.doi;
    if (id) acceptedIds.add(normalizePaperId(id));
  });
  return acceptedIds.size;
}

function renderFilteredCandidateCards() {
  const snapshot = state.review?.candidate_set || {};
  const filtered = (snapshot.filtered_candidates || []).filter(
    (candidate) => !state.manualCandidates.has(candidateId(candidate)),
  );
  elements.filteredCandidateCount.textContent = String(filtered.length);
  elements.filteredCandidatesPanel.hidden = !(snapshot.filtered_candidates || []).length;
  elements.filteredCandidateGrid.replaceChildren();

  filtered.forEach((candidate) => {
    const id = candidateId(candidate);
    const card = document.createElement("article");
    card.className = "filtered-candidate-card";
    const title = document.createElement("h4");
    title.textContent = candidate.title || "未命名论文";
    const reasons = document.createElement("p");
    const reasonList = snapshot.filtered_candidate_reasons?.[id] || [];
    reasons.textContent = [
      candidate.year ? `年份 ${candidate.year}` : "年份未知",
      ...reasonList,
    ].join(" · ");
    const addButton = document.createElement("button");
    addButton.type = "button";
    addButton.className = "secondary";
    addButton.textContent = "手动加入候选";
    addButton.addEventListener("click", () => {
      state.manualCandidates.set(id, {
        paper_id: candidate.paper_id || id,
        title: candidate.title || "手动加入的论文",
        authors: candidate.authors || [],
        year: candidate.year || null,
        doi: candidate.doi || "",
        url: candidate.url || null,
        source: "user",
      });
      if (!state.candidates.some((item) => candidateId(item) === id)) {
        state.candidates.push(candidate);
      }
      state.selectedIds.add(id);
      renderCandidateCards();
      renderFilteredCandidateCards();
      updateReviewStats();
      notify("论文已手动加入候选集，提交后保存");
    });
    card.append(title, reasons, addButton);
    elements.filteredCandidateGrid.append(card);
  });

  if (!filtered.length && (snapshot.filtered_candidates || []).length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "所有未达要求的论文均已手动加入";
    elements.filteredCandidateGrid.append(empty);
  }
}

function updateReviewStats() {
  elements.candidateCount.textContent = String(state.candidates.length);
  elements.selectedCount.textContent = String(state.selectedIds.size);
  const snapshot = state.review?.candidate_set;
  elements.roundCount.textContent = `${snapshot?.search_round || 0} / ${snapshot?.max_search_rounds ?? 3}`;
}

function renderReview(review) {
  state.review = review;
  state.manualCandidates = new Map();
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
  const yearConstraint = snapshot.year_from != null && snapshot.year_to != null
    ? `年份 ${snapshot.year_from}-${snapshot.year_to}`
    : snapshot.year_from != null
      ? `年份 ${snapshot.year_from} 起`
      : snapshot.year_to != null
        ? `年份截至 ${snapshot.year_to}`
        : "历史候选 · 年份范围未记录";
  elements.reviewConstraints.textContent = [
    yearConstraint,
    snapshot.quality_venues_only
      ? "仅 CCF-A、JCR Q1 或 Nature Portfolio"
      : "出版物等级不限",
  ].join(" · ");
  elements.reviewNotice.hidden = !review.message;
  elements.reviewNotice.textContent = review.message || "";
  renderProjectHeader(review.project);
  renderProjectSummary({
    project: review.project,
    artifacts: [...(state.snapshot?.artifacts || []), { kind: "CandidateSetSnapshot", payload: snapshot }],
    events: state.snapshot?.events || [],
  });
  elements.reviewPanel.hidden = false;
  elements.continuePanel.hidden = true;
  elements.undoReview.hidden = !review.can_undo;
  renderCandidateCards();
  renderFilteredCandidateCards();
  updateReviewStats();
}

function feedbackBody(action) {
  const exclusions = state.candidates
    .map(candidateId)
    .filter((id) => !state.selectedIds.has(id));
  const queries = parseList(elements.querySuggestions.value);
  const dois = parseList(elements.manualDois.value, true);
  const manualCandidates = [...state.manualCandidates.values()];
  const knownManualIds = new Set(
    manualCandidates.flatMap((paper) => [paper.doi, paper.paper_id].filter(Boolean).map(normalizePaperId)),
  );
  return {
    action,
    suggested_queries: queries,
    added_papers: [
      ...manualCandidates,
      ...dois
        .filter((doi) => !knownManualIds.has(normalizePaperId(doi)))
        .map((doi) => ({ doi })),
    ],
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
    const acceptedCount = acceptedPaperCount(state.selectedIds, body.added_papers);
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
  let backgroundStarted = false;
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
      await loadProjects();
      await loadProject(state.projectId, true, true);
      notify("候选集已确认；可撤销，或点击继续研究开始精读");
    } else {
      await loadProjects();
      await loadProject(state.projectId, true, true);
      notify(action === "accept" ? "候选集已确认，可以继续研究" : "项目已结束");
    }
  } catch (error) {
    notify(`提交失败：${error.message}`, true);
    await loadProject(state.projectId, true, true);
  } finally {
    if (!backgroundStarted) {
      if (state.runStartedAt) finishRunSession();
      elements.runPanel.hidden = true;
      setBusy(false);
    }
  }
}

async function undoSearchFeedback() {
  if (!state.projectId || state.busy) return;
  if (!window.confirm("撤销最近一次人工审核操作并恢复上一版候选集？")) return;
  setBusy(true);
  try {
    const payload = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/search-feedback/undo`,
      { method: "POST", body: "{}" },
    );
    renderReview(payload.data);
    await loadProjects();
    notify("已恢复上一版候选集");
  } catch (error) {
    notify(`撤销失败：${error.message}`, true);
    await loadProject(state.projectId, true, true);
  } finally {
    setBusy(false);
  }
}

function formatRunElapsed(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function updateRunClock() {
  if (!state.runStartedAt) return;
  elements.runElapsed.textContent = formatRunElapsed(Date.now() - state.runStartedAt);
}

function setRunPhase(phaseName, detail = "") {
  const phase = RUN_PHASES[phaseName] || RUN_PHASES.thinking;
  const changed = state.runPhase !== phaseName;
  state.runPhase = phaseName;
  elements.runVisualizer.dataset.phase = phaseName;
  elements.runPhaseTitle.textContent = phase.title;
  elements.runStatusText.textContent = detail || phase.detail;
  elements.runPhaseIcon.replaceChildren(iconNode(phase.icon));
  refreshIcons();
  return changed;
}

function markCurrentActivityComplete() {
  const current = elements.activityLog.querySelector("li.is-current");
  if (!current) return;
  current.classList.remove("is-current");
  current.classList.add("is-complete");
  const marker = current.querySelector(".activity-marker");
  if (marker) marker.replaceChildren(iconNode("check"));
}

function addActivity(message, { updateStatus = true, kind = "progress" } = {}) {
  if (!message) return;
  if (message === state.runLastActivity) {
    if (updateStatus) elements.runStatusText.textContent = message;
    return;
  }
  markCurrentActivityComplete();
  const item = document.createElement("li");
  item.className = kind === "complete" ? "is-complete" : `is-current is-${kind}`;
  const marker = document.createElement("span");
  marker.className = "activity-marker";
  marker.append(iconNode(
    kind === "complete" ? "check" : kind === "error" ? "circle-alert" : "circle",
  ));
  const text = document.createElement("span");
  text.className = "activity-text";
  text.textContent = message;
  item.append(marker, text);
  elements.activityLog.append(item);
  while (elements.activityLog.children.length > 7) {
    elements.activityLog.firstElementChild?.remove();
  }
  elements.activityLog.scrollTop = elements.activityLog.scrollHeight;
  state.runLastActivity = message;
  if (updateStatus) elements.runStatusText.textContent = message;
  refreshIcons();
}

function stopRunTimers() {
  if (state.runClockTimer) window.clearInterval(state.runClockTimer);
  if (state.runPollTimer) window.clearInterval(state.runPollTimer);
  state.runClockTimer = null;
  state.runPollTimer = null;
}

function beginRunSession({ stage = "CREATED", message = "", snapshot = null, phase = "" } = {}) {
  stopRunTimers();
  state.runSessionId += 1;
  state.runStartedAt = Date.now();
  state.runKnownEvents = new Set(
    (snapshot?.events || []).map((event, index) => eventIdentity(event, index)),
  );
  state.runKnownArtifacts = new Set(
    (snapshot?.artifacts || []).map((artifact, index) => artifactIdentity(artifact, index)),
  );
  state.runSnapshotSignature = snapshot ? snapshotSignature(snapshot) : "";
  state.runLastActivity = "";
  elements.activityLog.replaceChildren();
  elements.runPanel.hidden = false;
  elements.runElapsed.textContent = "00:00";
  const activePhase = phase || STAGE_RUN_PHASES[stage] || "thinking";
  setRunPhase(activePhase, message);
  addActivity(message || RUN_PHASES[activePhase].detail);
  state.runClockTimer = window.setInterval(updateRunClock, 1000);
}

function finishRunSession() {
  stopRunTimers();
  state.runSessionId += 1;
  markCurrentActivityComplete();
  refreshIcons();
  state.runStartedAt = null;
}

function transitionActivity(event) {
  const labels = {
    SEARCHED: "初步文献检索已完成",
    SEARCH_REVIEW_PENDING: "候选论文已提交人工审核",
    SCREENED: "候选论文已确认",
    EXTRACTED: "论文精读与证据提取已完成",
    SYNTHESIZED: "跨论文证据综合已完成",
    REVIEW_PENDING: "综合结论已提交证据审查",
    REVIEWED: "证据审查已完成",
    OUTLINED: "综述提纲已生成",
    NARRATED: "综述正文已生成",
    COMPLETED: "事实核查完成，研究已结束",
    INCONCLUSIVE: "研究因证据不足停止",
  };
  return labels[event?.to_stage]
    || `${STAGE_LABELS[event?.from_stage] || event?.from_stage}进入${STAGE_LABELS[event?.to_stage] || event?.to_stage}`;
}

function artifactActivity(artifact) {
  const payload = artifact?.payload || {};
  if (artifact?.kind === "PaperCard") {
    return payload.title ? `论文精读完成：${payload.title}` : "一篇论文已完成精读";
  }
  if (artifact?.kind === "SectionDraft") {
    const title = payload.title || payload.section_title || payload.section_id;
    return title ? `章节草稿已生成：${title}` : "一个章节草稿已生成";
  }
  if (artifact?.kind === "FactCheckReport") {
    return "一个综述章节已完成事实核查";
  }
  return "";
}

function syncRunningSnapshot(snapshot) {
  const signature = snapshotSignature(snapshot);
  if (signature === state.runSnapshotSignature) return;

  (snapshot?.artifacts || []).forEach((artifact, index) => {
    const identity = artifactIdentity(artifact, index);
    if (state.runKnownArtifacts.has(identity)) return;
    state.runKnownArtifacts.add(identity);
    const message = artifactActivity(artifact);
    if (message) addActivity(message, { updateStatus: false, kind: "complete" });
  });

  orderedWorkflowEvents(snapshot?.events || []).forEach((event, index) => {
    const identity = eventIdentity(event, index);
    if (state.runKnownEvents.has(identity)) return;
    state.runKnownEvents.add(identity);
    addActivity(transitionActivity(event), { updateStatus: false, kind: "complete" });
  });

  state.runSnapshotSignature = signature;
  applyProjectSnapshot(snapshot, {
    keepRunPanel: true,
    renderInspector: state.inspectorOpen,
  });
  if (!state.busy) {
    elements.runPanel.hidden = true;
    void loadProjects();
    return;
  }
  const stage = snapshot?.project?.stage;
  setRunPhase(STAGE_RUN_PHASES[stage] || "thinking");
}

async function pollRunningProject() {
  if (
    state.runPollInFlight
    || !state.projectId
    || state.projectId.includes("正在")
    || !state.busy
  ) {
    return;
  }
  const sessionId = state.runSessionId;
  state.runPollInFlight = true;
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
    if (sessionId !== state.runSessionId || !state.runStartedAt || !state.busy) return;
    syncRunningSnapshot(payload.data);
  } catch {
    // The main request owns error reporting; polling remains best-effort.
  } finally {
    state.runPollInFlight = false;
  }
}

function startRunPolling() {
  if (state.runPollTimer || !state.projectId || state.projectId.includes("正在")) return;
  void pollRunningProject();
  state.runPollTimer = window.setInterval(() => {
    void pollRunningProject();
  }, 1200);
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

function streamPhase(eventName, payload) {
  if (eventName === "done") return "done";
  if (eventName === "error" || eventName === "fallback") return "stopped";
  if (eventName === "awaiting_input") return "reviewing";
  const serialized = JSON.stringify(payload || {}).toLowerCase();
  if (serialized.includes("fact-checker")) return "verifying";
  if (serialized.includes("narrative-writer") || serialized.includes("chief-editor")) return "writing";
  if (serialized.includes("research-outliner")) return "outlining";
  if (serialized.includes("evidence-reviewer")) return "reviewing";
  if (serialized.includes("research-synthesizer")) return "synthesizing";
  if (serialized.includes("paper-reader")) return "reading";
  if (
    serialized.includes("literature-scout")
    || serialized.includes("openalex")
    || serialized.includes("crossref")
  ) {
    return "searching";
  }
  const keys = payload && typeof payload === "object" ? Object.keys(payload) : [];
  if (keys.some((key) => key.toLowerCase().includes("model"))) return "thinking";
  return state.runPhase || "thinking";
}

function streamUpdateLabel(eventName, payload, phaseName = "thinking") {
  if (eventName === "awaiting_input") return "初次检索完成，等待人工审核";
  if (eventName === "done") return "本轮 Agent 执行结束";
  if (eventName === "fallback") return "模型不可用，已进入降级流程";
  if (eventName === "error") return payload?.message || "Agent 执行失败";
  const labels = {
    thinking: "正在分析当前材料并规划下一步",
    searching: "正在扩展检索词并查找候选论文",
    reading: "正在读取论文并提取结构化信息",
    synthesizing: "正在比较研究发现并组织证据",
    reviewing: "正在核对结论与证据引用",
    outlining: "正在规划综述章节与论证顺序",
    writing: "正在整合章节正文与参考文献",
    verifying: "正在逐节执行事实核查",
  };
  return labels[phaseName] || "正在执行研究任务";
}

async function handleStreamEvent(eventName, payload) {
  const project = findProject(payload);
  let phaseName = streamPhase(eventName, payload);
  if (project) {
    state.projectId = project.project_id;
    renderProjectHeader(project, state.snapshot?.events || []);
    renderProjectSummary({
      project,
      artifacts: state.snapshot?.artifacts || [],
      events: state.snapshot?.events || [],
    });
    phaseName = STAGE_RUN_PHASES[project.stage] || phaseName;
    startRunPolling();
  }
  const label = streamUpdateLabel(eventName, payload, phaseName);
  const phaseChanged = setRunPhase(phaseName, label);
  if (phaseChanged || eventName !== "update") {
    addActivity(label, {
      updateStatus: false,
      kind: eventName === "error" ? "error" : "progress",
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

async function startResearchLegacy(topic, question, reviewLimits = {}) {
  state.projectId = null;
  state.snapshot = null;
  state.review = null;
  showWorkspace("project");
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = true;
  elements.projectDetails.hidden = true;
  const pendingProject = {
    project_id: "正在创建项目…",
    topic,
    research_question: question,
    stage: "CREATED",
  };
  renderProjectHeader(pendingProject);
  renderProjectSummary({ project: pendingProject, artifacts: [], events: [] });
  beginRunSession({
    stage: "CREATED",
    phase: "thinking",
    message: "正在创建项目并分析研究问题",
  });
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
    finishRunSession();
    elements.runPanel.hidden = true;
    setBusy(false);
  }
}

async function continueResearchLegacy() {
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
  beginRunSession({
    stage: state.snapshot?.project?.stage || "SCREENED",
    message: mode === "screening"
      ? "正在恢复项目并启动论文精读"
      : "正在恢复写作阶段并生成缺失的综述产物",
    snapshot: state.snapshot,
  });
  startRunPolling();
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
    finishRunSession();
    elements.runPanel.hidden = true;
    setBusy(false);
  }
}

async function startResearch(topic, question, reviewLimits = {}) {
  stopRunTimers();
  state.runSessionId += 1;
  state.projectId = null;
  state.project = null;
  state.snapshot = null;
  state.conversation = null;
  state.activeRun = null;
  state.activeRunId = null;
  state.review = null;
  showWorkspace("project");
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = true;
  elements.projectDetails.hidden = true;
  const pendingProject = {
    project_id: "正在创建对话…",
    topic,
    research_question: question,
    stage: "CREATED",
  };
  renderProjectHeader(pendingProject);
  renderProjectSummary({ project: pendingProject, artifacts: [], events: [] });
  beginRunSession({
    stage: "CREATED",
    phase: "thinking",
    message: "正在创建独立对话并启动后台调研",
  });
  setBusy(true);

  try {
    const payload = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({
        topic,
        research_question: question,
        ...reviewLimits,
      }),
    });
    const snapshot = payload.data;
    applyProjectSnapshot(snapshot, { keepRunPanel: true });
    await loadProjects();
    startRunPolling();
    notify("调研已在后台启动；你可以新建或切换到其他对话");
  } catch (error) {
    if (state.runStartedAt) finishRunSession();
    elements.runPanel.hidden = true;
    setBusy(false);
    notify(`研究启动失败：${error.message}`, true);
  }
}

async function continueResearch() {
  if (!state.projectId || state.busy) return;
  if (!state.agentAvailable) {
    notify("Agent 当前不可用，请先检查模型配置", true);
    return;
  }
  const conversationId = conversationIdForSnapshot();
  if (!conversationId) {
    notify("旧项目没有独立对话记录，请重新创建一个研究对话", true);
    return;
  }
  if (!window.confirm("将从已保存进度继续，并在后台运行。确认开始吗？")) return;

  setBusy(true);
  beginRunSession({
    stage: state.snapshot?.project?.stage || "SCREENED",
    message: "正在从已保存进度恢复研究",
    snapshot: state.snapshot,
  });
  try {
    const payload = await api(
      `/api/conversations/${encodeURIComponent(conversationId)}/continue`,
      { method: "POST", body: "{}" },
    );
    state.activeRun = payload.data;
    state.activeRunId = payload.data.run_id;
    if (state.snapshot) state.snapshot.active_run = payload.data;
    await loadProjects();
    startRunPolling();
    notify("后续研究已在后台启动；可以切换到其他对话");
  } catch (error) {
    if (state.runStartedAt) finishRunSession();
    elements.runPanel.hidden = true;
    setBusy(false);
    notify(`继续执行失败：${error.message}`, true);
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
elements.libraryToggle.addEventListener("click", openLibrary);
elements.refreshLibrary.addEventListener("click", loadLibrary);
elements.askLibrary.addEventListener("click", openLibraryAssistant);
elements.importLibrary.addEventListener("click", importLibraryRecords);
elements.newCollection.addEventListener("click", () => createLibraryCollection());
elements.findDuplicates.addEventListener("click", findLibraryDuplicates);
elements.applyLibraryBulk.addEventListener("click", applyLibraryBulkAction);
elements.compareLibrarySelection.addEventListener("click", compareSelectedLibraryPapers);
elements.clearLibrarySelection.addEventListener("click", () => {
  state.selectedLibraryIds.clear();
  renderLibraryList();
});
elements.closeLibraryCompare.addEventListener("click", () => {
  elements.libraryComparePanel.hidden = true;
});
elements.libraryAssistantForm.addEventListener("submit", askLibraryAssistant);
elements.paperWorkspaceBack.addEventListener("click", async () => {
  showWorkspace("library");
  if (state.selectedLibraryId) await selectLibraryPaper(state.selectedLibraryId);
});
document.querySelectorAll("[data-paper-tab]").forEach((button) => {
  button.addEventListener("click", () => setPaperTab(button.dataset.paperTab));
});
elements.paperPdfPages.addEventListener("mouseup", () => window.setTimeout(capturePaperSelection, 0));
elements.paperPdfPages.addEventListener("pointerup", () => window.setTimeout(capturePaperSelection, 0));
let paperSelectionFrame = null;
document.addEventListener("selectionchange", () => {
  if (elements.paperWorkspaceView.hidden || paperSelectionFrame !== null) return;
  paperSelectionFrame = window.requestAnimationFrame(() => {
    paperSelectionFrame = null;
    capturePaperSelection();
  });
});
elements.highlightSelection.addEventListener("click", async () => {
  if (!state.paperSelection) return;
  try {
    await savePaperAnnotation("highlight");
    notify("高亮已保存");
  } catch (error) {
    notify(`高亮保存失败：${error.message}`, true);
  }
});
elements.noteSelection.addEventListener("click", () => {
  if (!state.paperSelection) return;
  setPaperTab("annotations");
  elements.paperNoteForm.hidden = false;
  elements.paperNoteInput.focus();
});
elements.cancelPaperNote.addEventListener("click", () => {
  elements.paperNoteForm.hidden = true;
  elements.paperNoteInput.value = "";
});
elements.paperNoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = elements.paperNoteInput.value.trim();
  if (!content || !state.paperSelection) return;
  try {
    await savePaperAnnotation("note", { content });
    elements.paperNoteInput.value = "";
    elements.paperNoteForm.hidden = true;
    notify("批注已保存");
  } catch (error) {
    notify(`批注保存失败：${error.message}`, true);
  }
});
elements.askSelection.addEventListener("click", () => {
  if (!state.paperSelection) return;
  setPaperTab("ask");
  elements.paperAskContext.querySelector("strong").textContent = `第 ${state.paperSelection.page} 页选中文本`;
  elements.paperAskContext.querySelector("p").textContent = state.paperSelection.text;
  elements.clearPaperSelection.hidden = false;
  elements.paperQuestionInput.focus();
});
elements.clearPaperSelection.addEventListener("click", clearPaperSelection);
elements.paperQuestionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = elements.paperQuestionInput.value.trim();
  const workspace = state.paperWorkspace;
  if (!question || !workspace) return;
  const selection = state.paperSelection;
  const submit = elements.paperQuestionForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  elements.paperAnswer.hidden = false;
  elements.paperAnswer.textContent = "正在检索论文证据并组织回答…";
  try {
    const payload = await api(
      `/api/library/papers/${encodeURIComponent(workspace.paper.library_id)}/workspace/question`,
      {
        method: "POST",
        body: JSON.stringify({
          scope: selection ? "selection" : "paper",
          attachment_id: workspace.workspace_attachment?.attachment_id || null,
          question,
          page: selection?.page || null,
          selected_text: selection?.text || "",
          prefix: selection?.prefix || "",
          suffix: selection?.suffix || "",
        }),
      },
    );
    state.paperLastAnswer = {
      ...payload.data,
      selection_rects: selection?.rects || [],
    };
    renderPaperAnswer(state.paperLastAnswer);
  } catch (error) {
    elements.paperAnswer.textContent = `提问失败：${error.message}`;
    notify(`论文提问失败：${error.message}`, true);
  } finally {
    submit.disabled = false;
  }
});
elements.generateReadingCard.addEventListener("click", async () => {
  const workspace = state.paperWorkspace;
  if (!workspace?.paper?.library_id) return;
  elements.generateReadingCard.disabled = true;
  const original = elements.generateReadingCard.textContent;
  elements.generateReadingCard.textContent = "正在生成精读卡…";
  try {
    const attachmentQuery = workspace.workspace_attachment?.attachment_id
      ? `?attachment_id=${encodeURIComponent(workspace.workspace_attachment.attachment_id)}`
      : "";
    const generated = await api(
      `/api/library/papers/${encodeURIComponent(workspace.paper.library_id)}/workspace/reading-card${attachmentQuery}`,
      { method: "POST" },
    );
    const refreshed = await api(`/api/library/papers/${encodeURIComponent(workspace.paper.library_id)}/workspace`);
    state.paperWorkspace.analyses = refreshed.data.analyses || [];
    renderPaperReadingCard();
    setPaperTab("card");
    notify(generated.data?.evidence_level === "abstract" ? "摘要级精读卡已生成" : "全文级精读卡已生成");
  } catch (error) {
    notify(`精读卡生成失败：${error.message}`, true);
  } finally {
    elements.generateReadingCard.disabled = false;
    elements.generateReadingCard.textContent = original;
    refreshIcons();
  }
});
elements.paperZoomOut.addEventListener("click", async () => {
  state.paperZoom = Math.max(0.6, Number((state.paperZoom - 0.15).toFixed(2)));
  await renderPaperPdf();
});
elements.paperZoomIn.addEventListener("click", async () => {
  state.paperZoom = Math.min(2, Number((state.paperZoom + 0.15).toFixed(2)));
  await renderPaperPdf();
});
let librarySearchTimer = null;
elements.librarySearch.addEventListener("input", () => {
  window.clearTimeout(librarySearchTimer);
  librarySearchTimer = window.setTimeout(loadLibrary, 220);
});
elements.emptyNewProject.addEventListener("click", () => toggleNewProject(true));
elements.cancelNewProject.addEventListener("click", () => toggleNewProject(false));
elements.cancelNewProjectSecondary.addEventListener("click", () => toggleNewProject(false));
elements.newProjectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const topic = byId("topicInput").value.trim();
  const question = byId("questionInput").value.trim();
  if (!topic || !question) return;
  const reviewLimits = {
    min_papers: numberInputValue(elements.initialMinPapers, 2),
    max_papers: numberInputValue(elements.initialMaxPapers, 6),
    max_search_rounds: numberInputValue(elements.initialMaxSearchRounds, 3),
    year_from: numberInputValue(elements.initialYearFrom, 2024),
    year_to: numberInputValue(elements.initialYearTo, 2026),
    quality_venues_only: elements.initialQualityVenuesOnly.checked,
  };
  if (reviewLimits.min_papers > reviewLimits.max_papers) {
    notify("精读篇数下限不能大于上限", true);
    return;
  }
  if (
    reviewLimits.year_from < 2000
    || reviewLimits.year_to > 2026
    || reviewLimits.year_from > reviewLimits.year_to
  ) {
    notify("论文年份需在 2000-2026 之间，且起始年份不能晚于结束年份", true);
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
elements.undoReview.addEventListener("click", undoSearchFeedback);
elements.acceptReview.addEventListener("click", () => submitFeedback("accept"));
elements.stopReview.addEventListener("click", () => submitFeedback("stop"));
elements.continueResearch.addEventListener("click", continueResearch);
elements.undoDecision.addEventListener("click", undoSearchFeedback);

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
elements.usageGuideOpen.addEventListener("click", openUsageGuide);
elements.usageGuideClose.addEventListener("click", () => closeUsageGuide());
elements.usageGuideDismiss.addEventListener("click", () => closeUsageGuide());
elements.usageGuideBackdrop.addEventListener("click", () => closeUsageGuide());
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
  if (state.usageGuideOpen && event.key === "Tab") {
    const focusable = [...elements.usageGuide.querySelectorAll(
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
  if (state.usageGuideOpen) {
    event.preventDefault();
    closeUsageGuide();
    return;
  }
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
  maybeOpenUsageGuide();
  await Promise.all([checkHealth(), loadProjects()]);
  const params = new URLSearchParams(window.location.search);
  if (params.get("view") === "library") {
    await openLibrary();
    return;
  }
  const requestedProject = params.get("project");
  if (requestedProject) await loadProject(requestedProject, true);
}

initialize();
window.setInterval(() => {
  if (document.visibilityState === "visible") void loadProjects();
}, 4000);
