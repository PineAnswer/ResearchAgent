"use strict";

const STAGES = [
  ["CREATED", "创建"],
  ["SEARCHED", "检索"],
  ["SEARCH_REVIEW_PENDING", "人工审核"],
  ["SCREENED", "候选确认"],
  ["EXTRACTED", "论文精读"],
  ["SYNTHESIZED", "结果整理"],
  ["REVIEW_PENDING", "整理中"],
  ["REVIEWED", "整理完成"],
  ["OUTLINED", "提纲设计"],
  ["NARRATED", "综述已生成"],
  ["COMPLETED", "完成"],
];

const PROGRESS_STAGES = [
  { key: "create", label: "创建项目", stages: ["CREATED"] },
  {
    key: "search",
    label: "检索与筛选",
    stages: ["SEARCHED", "SEARCH_REVIEW_PENDING", "SCREENED"],
  },
  { key: "extract", label: "精读论文", stages: ["EXTRACTED"] },
  {
    key: "synthesize",
    label: "整理结果",
    stages: ["SYNTHESIZED", "REVIEW_PENDING", "REVIEWED"],
  },
  { key: "write", label: "生成综述", stages: ["OUTLINED", "NARRATED"] },
  { key: "complete", label: "完成", stages: ["COMPLETED"] },
];

const STAGE_LABELS = Object.fromEntries(STAGES);
STAGE_LABELS.INCONCLUSIVE = "等待补充文献";
STAGE_LABELS.OUTLINED = "提纲设计";
STAGE_LABELS.NARRATED = "综述已生成";

const RUN_PHASES = {
  thinking: {
    title: "正在准备文献检索",
    detail: "分析研究问题并生成检索方案。",
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
    title: "正在整理研究结果",
    detail: "汇总各论文的主要发现、差异与局限。",
    icon: "network",
  },
  reviewing: {
    title: "正在整理研究结果",
    detail: "检查内容是否完整并准备综述结构。",
    icon: "list-checks",
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
  SEARCH_REVIEW_PENDING: "searching",
  SCREENED: "reading",
  EXTRACTED: "synthesizing",
  SYNTHESIZED: "reviewing",
  REVIEW_PENDING: "reviewing",
  REVIEWED: "outlining",
  OUTLINED: "writing",
  NARRATED: "done",
  COMPLETED: "done",
  INCONCLUSIVE: "stopped",
};

const ACTOR_LABELS = {
  "literature-scout": "文献检索助手",
  "human-search-review": "人工检索审核",
  "research-supervisor": "研究调度器",
  "paper-reader": "论文精读 Agent",
  "research-synthesizer": "证据综合 Agent",
  "evidence-reviewer": "证据审查 Agent",
  "review-revision": "审查修订流程",
  "research-outliner": "提纲设计 Agent",
  "narrative-writer": "综述写作 Agent",
  "chief-editor": "综述主编 Agent",
  "chief-editor-fallback": "综述主编恢复流程",
  "workflow-recovery": "工作流恢复器",
};

const ARTIFACT_LABELS = {
  SearchReport: "检索结果",
  SupplementalSearchReport: "检索结果",
  CandidateSetSnapshot: "候选集快照",
  SearchFeedback: "人工审核反馈",
  ScreeningDecision: "入选论文",
  PaperCard: "论文精读卡",
  SynthesisReport: "研究结果",
  ReviewResult: "内容校对",
  ReviewOutline: "综述提纲",
  SectionDraft: "章节草稿",
  NarrativeReview: "最终综述",
  InsufficientEvidence: "停止原因",
};

function artifactLabel(kind) {
  return ARTIFACT_LABELS[kind] || kind;
}

function compactArtifactText(value, maxLength = 18) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1).trim()}…` : text;
}

function artifactDisplayLabel(artifact) {
  if (artifact?.kind !== "SectionDraft") return artifactLabel(artifact?.kind || "");
  const payload = artifact.payload || {};
  const sectionId = compactArtifactText(payload.section_id || "", 5);
  const heading = String(
    payload.heading || payload.section_title || payload.title || "章节",
  )
    .replace(/^\s*(?:第\s*)?\d+\s*[.、:：-]?\s*/, "")
    .trim();
  const conciseHeading = compactArtifactText(heading.split(/[:：]/, 1)[0], 20);
  return [sectionId, conciseHeading].filter(Boolean).join(" · ") || "章节草稿";
}

const state = {
  projects: [],
  projectsLoading: false,
  projectSelectionMode: false,
  selectedProjectIds: new Set(),
  projectBulkDeleting: false,
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
  reviewPage: 1,
  reviewPageSize: 20,
  reviewQuery: "",
  reviewFilteredPage: 1,
  reviewSelectionDirty: false,
  reviewSearchTimer: null,
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
  citationIndex: new Map(),
  citationEvidenceIds: [],
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
  activePaperAnnotationId: null,
  paperScrollSyncing: false,
  paperCurrentPage: null,
  paperProgressTimer: null,
  paperLastAnswer: null,
  researchSelection: "",
  researchNotes: [],
  researchNotesProjectId: null,
  researchLastAnswer: null,
  researchChatHistory: [],
  researchWorkspaceTab: "ask",
  researchWorkspaceWidth: 430,
  paperRenderSession: 0,
  runStartedAt: null,
  runClockTimer: null,
  runPollTimer: null,
  runPollInFlight: false,
  runKnownEvents: new Set(),
  runKnownArtifacts: new Set(),
  runKnownRuntimeEvents: new Set(),
  runSnapshotSignature: "",
  runLastActivity: "",
  runPhase: "thinking",
  runSessionId: 0,
  projectLoadSession: 0,
  projectLoadController: null,
  completedProjectCache: new Map(),
  openConversationMenuId: null,
  projectListRenderSignature: "",
};

const byId = (id) => document.getElementById(id);

const elements = {
  appShell: byId("appShell"),
  sidebarToggle: byId("sidebarToggle"),
  brandHome: byId("brandHome"),
  homeToggle: byId("homeToggle"),
  healthBadge: byId("healthBadge"),
  toolsMenuToggle: byId("toolsMenuToggle"),
  toolsMenu: byId("toolsMenu"),
  usageGuideOpen: byId("usageGuideOpen"),
  newProjectToggle: byId("newProjectToggle"),
  libraryToggle: byId("libraryToggle"),
  recentHistoryToggle: byId("recentHistoryToggle"),
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
  paperHorizontalScroller: byId("paperHorizontalScroller"),
  paperHorizontalScrollContent: byId("paperHorizontalScrollContent"),
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
  emptyReadPaper: byId("emptyReadPaper"),
  emptyOpenLibrary: byId("emptyOpenLibrary"),
  projectList: byId("projectList"),
  projectSearch: byId("projectSearch"),
  toggleProjectSelection: byId("toggleProjectSelection"),
  projectBulkBar: byId("projectBulkBar"),
  projectSelectedCount: byId("projectSelectedCount"),
  selectAllProjects: byId("selectAllProjects"),
  cancelProjectSelection: byId("cancelProjectSelection"),
  deleteSelectedProjects: byId("deleteSelectedProjects"),
  refreshProjects: byId("refreshProjects"),
  projectLookupForm: byId("projectLookupForm"),
  projectIdInput: byId("projectIdInput"),
  initialMaxSearchRounds: byId("initialMaxSearchRounds"),
  initialYearFrom: byId("initialYearFrom"),
  initialYearTo: byId("initialYearTo"),
  initialQualityVenuesOnly: byId("initialQualityVenuesOnly"),
  initialPreferLibrarySearch: byId("initialPreferLibrarySearch"),
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
  currentTask: byId("currentTask"),
  runVisualizer: byId("runVisualizer"),
  runPhaseIcon: byId("runPhaseIcon"),
  runPhaseTitle: byId("runPhaseTitle"),
  runStatusText: byId("runStatusText"),
  runElapsed: byId("runElapsed"),
  activityLog: byId("activityLog"),
  reviewPanel: byId("reviewPanel"),
  candidateCount: byId("candidateCount"),
  selectedCount: byId("selectedCount"),
  reviewConstraints: byId("reviewConstraints"),
  reviewQueryRounds: byId("reviewQueryRounds"),
  supplementalQueries: byId("supplementalQueries"),
  reviewNotice: byId("reviewNotice"),
  candidateFilter: byId("candidateFilter"),
  candidateGrid: byId("candidateGrid"),
  candidatePagination: byId("candidatePagination"),
  candidatePrevPage: byId("candidatePrevPage"),
  candidateNextPage: byId("candidateNextPage"),
  candidatePageStatus: byId("candidatePageStatus"),
  candidatePageSize: byId("candidatePageSize"),
  filteredCandidatesPanel: byId("filteredCandidatesPanel"),
  filteredCandidateCount: byId("filteredCandidateCount"),
  filteredCandidateGrid: byId("filteredCandidateGrid"),
  filteredCandidatePagination: byId("filteredCandidatePagination"),
  filteredCandidatePrevPage: byId("filteredCandidatePrevPage"),
  filteredCandidateNextPage: byId("filteredCandidateNextPage"),
  filteredCandidatePageStatus: byId("filteredCandidatePageStatus"),
  selectAll: byId("selectAll"),
  clearAll: byId("clearAll"),
  paperCapacity: byId("paperCapacity"),
  manualDois: byId("manualDois"),
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
let projectPreviewAnchor = null;

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
  const navigationStates = [
    [elements.homeToggle, view === "empty"],
    [elements.newProjectToggle, view === "create"],
    [elements.libraryToggle, ["library", "paper"].includes(view)],
  ];
  navigationStates.forEach(([button, active]) => {
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function setPopover(toggle, popover, open) {
  popover.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
}

function closeMenus() {
  setPopover(elements.toolsMenuToggle, elements.toolsMenu, false);
  setPopover(elements.projectMenuToggle, elements.projectMenu, false);
  closeConversationMenus();
  closeRecentHistoryPopover();
}

function closeConversationMenus(except = null) {
  document.querySelectorAll(".project-list-menu").forEach((menu) => {
    if (menu !== except) menu.hidden = true;
  });
  document.querySelectorAll(".project-list-menu-toggle").forEach((toggle) => {
    if (toggle.closest(".project-list-entry")?.querySelector(".project-list-menu") !== except) {
      toggle.setAttribute("aria-expanded", "false");
    }
  });
  state.openConversationMenuId = except
    ? except.closest(".project-list-entry")?.dataset.conversationId || null
    : null;
}

function ensureProjectPreview() {
  let preview = document.getElementById("projectHoverPreview");
  if (preview) return preview;
  preview = document.createElement("div");
  preview.id = "projectHoverPreview";
  preview.className = "project-hover-preview";
  preview.setAttribute("role", "tooltip");
  preview.hidden = true;
  document.body.append(preview);
  return preview;
}

function hideProjectPreview() {
  const preview = document.getElementById("projectHoverPreview");
  if (preview) preview.hidden = true;
  projectPreviewAnchor?.removeAttribute("aria-describedby");
  projectPreviewAnchor = null;
}

function showProjectPreview(project, anchor) {
  if (elements.appShell.dataset.sidebar !== "collapsed") return;
  const preview = ensureProjectPreview();
  const title = document.createElement("strong");
  title.className = "project-hover-preview-title";
  title.textContent = projectDisplayTitle(project);
  const question = document.createElement("span");
  question.className = "project-hover-preview-question";
  question.textContent = project.research_question || "未填写研究问题";
  preview.replaceChildren(title, question);

  projectPreviewAnchor?.removeAttribute("aria-describedby");
  projectPreviewAnchor = anchor;
  anchor.setAttribute("aria-describedby", preview.id);
  preview.hidden = false;
  preview.style.visibility = "hidden";
  const anchorRect = anchor.getBoundingClientRect();
  preview.style.left = `${Math.round(anchorRect.right + 10)}px`;
  preview.style.top = "8px";
  const previewRect = preview.getBoundingClientRect();
  const desiredTop = anchorRect.top + (anchorRect.height - previewRect.height) / 2;
  const top = Math.max(8, Math.min(desiredTop, window.innerHeight - previewRect.height - 8));
  preview.style.top = `${Math.round(top)}px`;
  preview.style.visibility = "visible";
}

function ensureRecentHistoryPopover() {
  let popover = document.getElementById("recentHistoryPopover");
  if (popover) return popover;
  popover = document.createElement("aside");
  popover.id = "recentHistoryPopover";
  popover.className = "recent-history-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", "最近研究");
  popover.hidden = true;
  document.body.append(popover);
  return popover;
}

function closeRecentHistoryPopover({ restoreFocus = false } = {}) {
  const popover = document.getElementById("recentHistoryPopover");
  if (!popover || popover.hidden) return;
  popover.hidden = true;
  elements.recentHistoryToggle.setAttribute("aria-expanded", "false");
  if (restoreFocus) elements.recentHistoryToggle.focus();
}

function renderRecentHistoryPopover() {
  const popover = ensureRecentHistoryPopover();
  const header = h("div", { cls: "recent-history-head" }, [
    h("strong", {}, "最近研究"),
    h(
      "button",
      {
        type: "button",
        cls: "icon-button recent-history-close",
        "aria-label": "关闭最近研究",
        onClick: () => closeRecentHistoryPopover({ restoreFocus: true }),
      },
      iconNode("x"),
    ),
  ]);
  const list = h("div", { cls: "recent-history-list" });
  state.projects.slice(0, 12).forEach((project) => {
    const button = h(
      "button",
      {
        type: "button",
        cls: `recent-history-item${project.project_id === state.projectId ? " is-active" : ""}`,
        "aria-label": `打开研究：${projectDisplayTitle(project)}`,
        onClick: async () => {
          closeRecentHistoryPopover();
          await loadProject(project.project_id);
        },
      },
      [
        h("span", { cls: "recent-history-title" }, projectDisplayTitle(project)),
        h("span", { cls: "recent-history-meta" }, projectListDisplayStage(project)),
      ],
    );
    list.append(button);
  });
  if (!state.projects.length) {
    list.append(h("p", { cls: "muted small recent-history-empty" }, "还没有研究记录"));
  }
  popover.replaceChildren(header, list);
  refreshIcons();
  return popover;
}

function openRecentHistoryPopover() {
  if (elements.appShell.dataset.sidebar !== "collapsed") return;
  const popover = renderRecentHistoryPopover();
  const anchorRect = elements.recentHistoryToggle.getBoundingClientRect();
  popover.hidden = false;
  popover.style.visibility = "hidden";
  popover.style.left = `${Math.round(anchorRect.right + 10)}px`;
  popover.style.top = "8px";
  const popoverRect = popover.getBoundingClientRect();
  const desiredTop = anchorRect.top - 8;
  const top = Math.max(8, Math.min(desiredTop, window.innerHeight - popoverRect.height - 8));
  popover.style.top = `${Math.round(top)}px`;
  popover.style.visibility = "visible";
  elements.recentHistoryToggle.setAttribute("aria-expanded", "true");
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
  hideProjectPreview();
  closeRecentHistoryPopover();
  if (!expanded) closeConversationMenus();
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
  if (state.project) {
    renderStageBadge(state.project);
    renderStepper(state.project.stage, state.snapshot?.events || []);
  } else {
    elements.stageStepper.classList.toggle("is-running", busy);
  }
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

function citationIdentity(value) {
  return String(value || "")
    .trim()
    .replace(/^[[（(]|[\]）)]$/g, "")
    .toLocaleLowerCase();
}

function citationValue(target, key, value) {
  if (value == null || value === "") return;
  if (Array.isArray(value)) {
    if (value.length && !(target[key] || []).length) target[key] = value;
    return;
  }
  if (!target[key]) target[key] = value;
}

function mergeCitationMeta(target, source = {}) {
  [
    "paperId",
    "doi",
    "title",
    "abstract",
    "year",
    "venue",
    "venueAcronym",
    "libraryId",
  ].forEach((key) => citationValue(target, key, source[key]));
  citationValue(target, "methods", source.methods || []);
  return target;
}

function citationMetaFromLibraryItem(item) {
  const relation = item?.relation || {};
  const paper = item?.paper || {};
  return {
    paperId: paper.paper_id || relation.source_paper_id || "",
    doi: paper.doi || "",
    title: paper.title || "",
    abstract: paper.abstract || "",
    year: paper.year || null,
    venue: paper.venue || "",
    venueAcronym: paper.venue_acronym || "",
    libraryId: paper.library_id || relation.library_id || "",
    methods: [],
  };
}

function citationMetaFromCandidate(candidate = {}) {
  return {
    paperId: candidate.paper_id || candidate.doi || "",
    doi: candidate.doi || "",
    title: candidate.title || "",
    abstract: candidate.abstract || "",
    year: candidate.year || null,
    venue: candidate.venue || "",
    venueAcronym: candidate.venue_acronym || "",
    methods: [],
  };
}

function registerCitationMeta(index, aliases, source) {
  const keys = [...new Set((aliases || []).filter(Boolean).map(citationIdentity).filter(Boolean))];
  let target = keys.map((key) => index.get(key)).find(Boolean) || {};
  mergeCitationMeta(target, source);
  keys.forEach((key) => index.set(key, target));
  [target.paperId, target.doi, target.libraryId]
    .filter(Boolean)
    .forEach((value) => index.set(citationIdentity(value), target));
  return target;
}

function rebuildCitationIndex(snapshot = state.snapshot) {
  const index = new Map();
  const uniqueLibraryItems = new Map();
  state.projectLibrary.forEach((item) => {
    const libraryId = item?.paper?.library_id || item?.relation?.library_id;
    if (libraryId) uniqueLibraryItems.set(libraryId, item);
  });
  uniqueLibraryItems.forEach((item) => {
    const relation = item.relation || {};
    const paper = item.paper || {};
    registerCitationMeta(
      index,
      [relation.source_paper_id, paper.paper_id, paper.doi, paper.library_id],
      citationMetaFromLibraryItem(item),
    );
  });

  (snapshot?.artifacts || [])
    .filter((artifact) => ["SearchReport", "SupplementalSearchReport", "CandidateSetSnapshot"].includes(artifact.kind))
    .flatMap((artifact) => artifact.payload?.candidates || [])
    .forEach((candidate) => {
      const item = [candidate.paper_id, candidate.doi]
        .filter(Boolean)
        .map((value) => state.projectLibrary.get(libraryIdentity(value)))
        .find(Boolean);
      const meta = citationMetaFromCandidate(candidate);
      if (item) mergeCitationMeta(meta, citationMetaFromLibraryItem(item));
      registerCitationMeta(index, [candidate.paper_id, candidate.doi], meta);
    });

  (snapshot?.artifacts || [])
    .filter((artifact) => artifact.kind === "PaperCard")
    .forEach((artifact) => {
      const card = artifact.payload || {};
      const item = [card.paper_id]
        .filter(Boolean)
        .map((value) => state.projectLibrary.get(libraryIdentity(value)))
        .find(Boolean);
      const meta = {
        paperId: card.paper_id || "",
        title: card.title || "",
        methods: card.methods || [],
      };
      if (item) mergeCitationMeta(meta, citationMetaFromLibraryItem(item));
      const target = registerCitationMeta(index, [card.paper_id], meta);
      (card.findings || []).forEach((finding) => {
        if (finding.evidence_id) index.set(citationIdentity(finding.evidence_id), target);
      });
    });

  state.citationIndex = index;
  state.citationEvidenceIds = [...index.keys()].filter((key) => /:e\d+$/i.test(key));
}

function resolveCitationMeta(reference) {
  const identity = citationIdentity(reference);
  if (!identity) return null;
  const exact = state.citationIndex.get(identity);
  if (exact) return exact;
  const paperReference = identity.replace(/:e\d+$/i, "");
  return state.citationIndex.get(paperReference) || null;
}

function citationModelName(meta) {
  const title = String(meta?.title || "").trim();
  const firstClause = title.split(/[:：]/, 1)[0].trim();
  if (firstClause.length >= 3 && firstClause.length <= 28 && /[A-Z]{2}|\+|\d/.test(firstClause)) {
    return firstClause;
  }
  const tokens = title.match(/[A-Za-z][A-Za-z0-9+.-]{2,24}/g) || [];
  const titleToken = tokens.find((token) => (
    (token.match(/[A-Z]/g) || []).length >= 2
    && /(VPR|Geo[A-Z0-9]|[a-z][A-Z]{2}|\+\+)/.test(token)
    && !["IEEE", "CVPR", "TPAMI"].includes(token.toUpperCase())
  ));
  if (titleToken) return titleToken.replace(/[.,;:]$/, "");

  const methodText = (meta?.methods || []).join(" ");
  const methodTokens = methodText.match(/[A-Za-z][A-Za-z0-9+.-]{2,24}/g) || [];
  const methodToken = methodTokens.find((token) => (
    /(Geo|VPR)/.test(token)
    && !["GEO", "VPR", "CVGL", "GEOGRAPHY", "GEO-LOCALIZATION"].includes(token.toUpperCase())
  ));
  if (methodToken) return methodToken.replace(/[.,;:]$/, "");

  if (!title) return "论文";
  return title.length > 24 ? `${title.slice(0, 23).trim()}…` : title;
}

function citationVenueLabel(meta) {
  const venue = meta?.venueAcronym || meta?.venue || "";
  const conciseVenue = venue.length > 18 ? `${venue.slice(0, 17).trim()}…` : venue;
  return [conciseVenue, meta?.year].filter(Boolean).join(" ");
}

let citationPreviewElement = null;

function hideCitationPreview() {
  if (citationPreviewElement) citationPreviewElement.hidden = true;
}

function showCitationPreview(meta, anchor) {
  if (!meta || !anchor) return;
  if (!citationPreviewElement) {
    citationPreviewElement = h("aside", {
      cls: "citation-hover-preview",
      role: "tooltip",
    });
    citationPreviewElement.hidden = true;
    document.body.append(citationPreviewElement);
  }
  const venue = citationVenueLabel(meta);
  citationPreviewElement.replaceChildren(
    h("strong", { cls: "citation-preview-title" }, meta.title || "论文信息待补充"),
    venue ? h("span", { cls: "citation-preview-meta" }, venue) : null,
    h(
      "p",
      { cls: `citation-preview-abstract${meta.abstract ? "" : " is-empty"}` },
      meta.abstract || "暂无摘要；点击后可进入文献研读工作台查看已有全文与精读信息。",
    ),
  );
  citationPreviewElement.hidden = false;
  citationPreviewElement.style.left = "0px";
  citationPreviewElement.style.top = "0px";
  const anchorRect = anchor.getBoundingClientRect();
  const previewRect = citationPreviewElement.getBoundingClientRect();
  const left = Math.min(
    Math.max(12, anchorRect.left),
    Math.max(12, window.innerWidth - previewRect.width - 12),
  );
  const below = anchorRect.bottom + 8;
  const top = below + previewRect.height <= window.innerHeight - 12
    ? below
    : Math.max(12, anchorRect.top - previewRect.height - 8);
  citationPreviewElement.style.left = `${left}px`;
  citationPreviewElement.style.top = `${top}px`;
}

async function openCitationPaper(meta) {
  if (!meta?.libraryId) {
    notify("这篇论文尚未关联到文献库，暂时无法打开研读工作台", true);
    return;
  }
  hideCitationPreview();
  closeInspector({ restoreFocus: false });
  state.selectedLibraryId = meta.libraryId;
  await openPaperWorkspace(meta.libraryId, null, true);
}

function renderEvidenceCitation(reference, extraClass = "") {
  const meta = resolveCitationMeta(reference);
  if (!meta) return h("code", { cls: `aw-ev-ref ${extraClass}`.trim() }, reference);
  const evidenceMatch = String(reference || "").match(/:E(\d+)$/i);
  const venue = citationVenueLabel(meta);
  const label = [citationModelName(meta), venue, evidenceMatch ? `E${evidenceMatch[1]}` : ""]
    .filter(Boolean)
    .join(" · ");
  const button = h(
    "button",
    {
      type: "button",
      cls: `evidence-citation ${extraClass}`.trim(),
      "aria-label": `打开论文：${meta.title || label}`,
      onMouseenter: () => showCitationPreview(meta, button),
      onMouseleave: hideCitationPreview,
      onFocus: () => showCitationPreview(meta, button),
      onBlur: hideCitationPreview,
      onClick: () => openCitationPaper(meta),
    },
    [h("span", { cls: "evidence-citation-label" }, label), iconNode("arrow-up-right")],
  );
  return button;
}

function libraryEntryForCandidate(candidate) {
  return [candidate.paper_id, candidate.doi]
    .filter(Boolean)
    .map((value) => state.projectLibrary.get(libraryIdentity(value)))
    .find(Boolean) || null;
}

async function loadProjectLibrary(projectId, signal = undefined) {
  if (!projectId) {
    indexProjectLibrary([]);
    return [];
  }
  const payload = await api(
    `/api/projects/${encodeURIComponent(projectId)}/library`,
    { signal },
  );
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

function libraryVenueBadges(paper) {
  const badges = [];
  const venueName = paper.venue_acronym || paper.venue;
  if (venueName) {
    badges.push(`${paper.venue_type === "conference" ? "会议" : "期刊"} · ${venueName}`);
  }
  if (paper.ccf_rank) badges.push(`CCF-${paper.ccf_rank}`);
  if (paper.sci_quartile) badges.push(`SCI ${paper.sci_quartile}`);
  if (paper.nature_portfolio) badges.push("Nature 子刊");
  if (paper.impact_factor != null) {
    badges.push(`IF ${Number(paper.impact_factor).toFixed(2).replace(/\.00$/, "")}`);
  }
  return badges;
}

function libraryResearchOrigin(paper) {
  const source = paper.research_sources?.[0];
  return source ? `来自调研：${source.topic}` : (paper.origin_label || "手动添加");
}

function collectionDisplayPath(collectionId) {
  const collections = state.libraryOverview.collections || [];
  const byId = new Map(collections.map((item) => [item.collection_id, item]));
  const parts = [];
  const visited = new Set();
  let current = byId.get(collectionId);
  while (current && !visited.has(current.collection_id)) {
    visited.add(current.collection_id);
    parts.unshift(current.name);
    current = current.parent_id ? byId.get(current.parent_id) : null;
  }
  return parts.join(" / ");
}

function recentReadingActionLabel(action) {
  const kind = { highlight: "高亮", note: "批注", qa: "问答" }[action.kind] || "阅读";
  const page = action.page ? `第 ${action.page} 页` : "论文原文";
  const text = action.selected_text || action.content || action.question || "已保存阅读位置";
  return `${kind} · ${page} · ${text}`;
}

const LIBRARY_SMART_VIEWS = [
  ["all", "library", "全部文献"],
  ["recent", "history", "最近研究"],
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
    row.classList.toggle("has-folder-action", Boolean(state.libraryCollectionId));

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
    libraryVenueBadges(paper).forEach((label) => {
      const venue = document.createElement("span");
      venue.className = "library-venue-badge";
      venue.textContent = label;
      badges.append(venue);
    });
    const origin = document.createElement("span");
    origin.className = "library-origin-badge";
    origin.textContent = libraryResearchOrigin(paper);
    origin.title = paper.research_sources?.[0]?.research_question || origin.textContent;
    badges.append(origin);
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
    if (paper.recent_reading) {
      const recent = document.createElement("span");
      recent.className = "library-recent-reading";
      const recentMeta = document.createElement("span");
      recentMeta.className = "library-recent-meta";
      recentMeta.textContent = `${formatDate(paper.recent_reading.updated_at)} · 继续第 ${paper.recent_reading.last_page || 1} 页`;
      recent.append(recentMeta);
      (paper.recent_reading.actions || []).slice(0, 3).forEach((action) => {
        const actionRow = document.createElement("span");
        actionRow.className = "library-recent-action";
        actionRow.textContent = recentReadingActionLabel(action);
        actionRow.title = actionRow.textContent;
        recent.append(actionRow);
      });
      button.append(recent);
    }
    button.addEventListener("click", () => {
      if (state.libraryView === "recent" && paper.recent_reading) {
        openPaperWorkspace(
          paper.library_id,
          paper.recent_reading.attachment_id || null,
          false,
          paper.recent_reading.last_page || 1,
        );
        return;
      }
      selectLibraryPaper(paper.library_id);
    });
    row.append(checkbox, button);
    if (state.libraryCollectionId) {
      const membership = paper.collection_membership || {};
      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = `icon-button library-folder-pin${membership.pinned ? " is-active" : ""}`;
      pin.setAttribute("aria-label", membership.pinned ? "取消文件夹内置顶" : "在文件夹内置顶");
      pin.title = membership.pinned ? "取消文件夹内置顶" : "在当前文件夹置顶";
      pin.append(iconNode(membership.pinned ? "pin-off" : "pin"));
      pin.addEventListener("click", async () => {
        await api(
          `/api/library/collections/${encodeURIComponent(state.libraryCollectionId)}/papers/${encodeURIComponent(paper.library_id)}`,
          {
            method: "PATCH",
            body: JSON.stringify({ pinned: !membership.pinned }),
          },
        );
        await loadLibrary();
      });
      row.append(pin);
    }
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
      indexedPdf ? "打开论文研读工作台" : "在线获取并打开全文",
    ));
    onlineWorkspace.addEventListener("click", () => openPaperWorkspace(
      paper.library_id,
      null,
      !indexedPdf,
    ));
    controls.append(onlineWorkspace);
  }

  const provenance = document.createElement("section");
  provenance.className = "library-detail-section library-provenance";
  const provenanceTitle = document.createElement("h4");
  provenanceTitle.textContent = "来源与评级";
  const venueLine = document.createElement("p");
  const venueBadges = libraryVenueBadges(paper);
  venueLine.textContent = venueBadges.length
    ? venueBadges.join(" · ")
    : "期刊或会议信息未返回";
  provenance.append(provenanceTitle, venueLine);
  const projectSources = detail.projects || [];
  if (projectSources.length) {
    projectSources.forEach(({ project, relation }) => {
      const sourceButton = document.createElement("button");
      sourceButton.type = "button";
      sourceButton.className = "library-source-project";
      sourceButton.append(iconNode("flask-conical"));
      const sourceCopy = document.createElement("span");
      const sourceTitle = document.createElement("strong");
      sourceTitle.textContent = `来自调研：${project.topic}`;
      const sourceQuestion = document.createElement("small");
      sourceQuestion.textContent = project.research_question;
      sourceCopy.append(sourceTitle, sourceQuestion);
      const sourceStatus = document.createElement("small");
      sourceStatus.textContent = relation.status;
      sourceButton.append(sourceCopy, sourceStatus);
      sourceButton.addEventListener("click", () => loadProject(project.project_id));
      provenance.append(sourceButton);
    });
  } else {
    const manual = document.createElement("p");
    manual.className = "library-manual-source";
    manual.textContent = "来源：手动添加或导入文献库";
    provenance.append(manual);
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
    label.append(input, document.createTextNode(collectionDisplayPath(collection.collection_id)));
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
      openWorkspace.append(document.createTextNode("打开论文研读工作台"));
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
    provenance,
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

function captureSelectedTextRects(range, page) {
  const pageRect = page.getBoundingClientRect();
  const textLayer = page.querySelector(".pdf-text-layer");
  if (!textLayer || !pageRect.width || !pageRect.height) return [];
  const seen = new Set();
  const rects = [];
  textLayer.querySelectorAll("span").forEach((span) => {
    if (!range.intersectsNode(span)) return;
    const textNode = span.firstChild;
    if (!textNode || textNode.nodeType !== Node.TEXT_NODE || !textNode.textContent) return;
    let startOffset = 0;
    let endOffset = textNode.textContent.length;
    if (range.startContainer === textNode) startOffset = range.startOffset;
    if (range.endContainer === textNode) endOffset = range.endOffset;
    if (endOffset <= startOffset) return;
    const textRange = document.createRange();
    textRange.setStart(textNode, Math.max(0, Math.min(startOffset, textNode.textContent.length)));
    textRange.setEnd(textNode, Math.max(0, Math.min(endOffset, textNode.textContent.length)));
    [...textRange.getClientRects()].forEach((rect) => {
      const left = Math.max(pageRect.left, rect.left);
      const top = Math.max(pageRect.top, rect.top);
      const right = Math.min(pageRect.right, rect.right);
      const bottom = Math.min(pageRect.bottom, rect.bottom);
      if (right - left <= 1 || bottom - top <= 1) return;
      const normalized = {
        x: (left - pageRect.left) / pageRect.width,
        y: (top - pageRect.top) / pageRect.height,
        width: (right - left) / pageRect.width,
        height: (bottom - top) / pageRect.height,
      };
      if (normalized.height > 0.06) return;
      const key = [normalized.x, normalized.y, normalized.width, normalized.height]
        .map((value) => value.toFixed(4))
        .join(":");
      if (!seen.has(key)) {
        seen.add(key);
        rects.push(normalized);
      }
    });
  });
  return rects;
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
  const rects = captureSelectedTextRects(range, start);
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
  const numberedAnnotations = annotations.filter((annotation) => ["note", "qa"].includes(annotation.kind));
  const annotationNumbers = new Map(
    numberedAnnotations.map((annotation, index) => [annotation.annotation_id, index + 1]),
  );
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
    item.dataset.annotationId = String(annotation.annotation_id);
    const header = document.createElement("header");
    const kind = document.createElement("strong");
    const annotationNumber = annotationNumbers.get(annotation.annotation_id);
    kind.textContent = annotationNumber
      ? `${annotation.kind === "qa" ? "问答批注" : "批注"} ${annotationNumber}`
      : ({ highlight: "高亮", note: "批注", qa: "问答" }[annotation.kind] || "批注");
    const actions = document.createElement("div");
    if (annotation.page) {
      const page = document.createElement("button");
      page.type = "button";
      page.className = "paper-page-link";
      page.textContent = `第 ${annotation.page} 页`;
      page.addEventListener("click", (event) => {
        event.stopPropagation();
        focusPaperAnnotation(annotation, { scrollPage: true, focusCard: false });
      });
      actions.append(page);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button";
    remove.setAttribute("aria-label", "删除批注");
    remove.append(iconNode("trash-2"));
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deletePaperAnnotation(annotation.annotation_id);
    });
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
    if (["note", "qa"].includes(annotation.kind)) {
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-label", `定位批注 ${annotationNumber}`);
      item.addEventListener("click", () => {
        focusPaperAnnotation(annotation, { scrollPage: true, focusCard: false });
      });
      item.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        focusPaperAnnotation(annotation, { scrollPage: true, focusCard: false });
      });
    }
    elements.paperAnnotationsList.append(item);
  });
  drawPaperHighlights();
  refreshIcons();
}

function usableAnnotationRects(annotation) {
  return (annotation.rects || [])
    .map((rect) => ({
      x: Number(rect.x),
      y: Number(rect.y),
      width: Number(rect.width),
      height: Number(rect.height),
    }))
    .filter((rect) => (
      Number.isFinite(rect.x)
      && Number.isFinite(rect.y)
      && Number.isFinite(rect.width)
      && Number.isFinite(rect.height)
      && rect.width > 0
      && rect.height > 0
      && rect.height <= 0.06
      && rect.x < 1
      && rect.y < 1
      && rect.x + rect.width > 0
      && rect.y + rect.height > 0
    ))
    .map((rect) => ({
      x: Math.max(0, rect.x),
      y: Math.max(0, rect.y),
      width: Math.min(1 - Math.max(0, rect.x), rect.width),
      height: Math.min(1 - Math.max(0, rect.y), rect.height),
    }));
}

function focusPaperAnnotation(annotation, options = {}) {
  const { scrollPage = false, focusCard = true } = options;
  state.activePaperAnnotationId = String(annotation.annotation_id);
  setPaperTab("annotations");
  drawPaperHighlights();
  elements.paperAnnotationsList.querySelectorAll(".paper-annotation").forEach((item) => {
    item.classList.toggle("is-target", item.dataset.annotationId === String(annotation.annotation_id));
  });
  const target = [...elements.paperAnnotationsList.querySelectorAll(".paper-annotation")]
    .find((item) => item.dataset.annotationId === String(annotation.annotation_id));
  if (scrollPage && annotation.page) scrollToPaperPage(annotation.page);
  if (focusCard) target?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function drawPaperHighlights() {
  elements.paperPdfPages
    .querySelectorAll(".paper-highlight-overlay, .paper-annotation-marker")
    .forEach((node) => node.remove());
  const annotations = state.paperWorkspace?.annotations || [];
  const numberedAnnotations = annotations.filter((annotation) => ["note", "qa"].includes(annotation.kind));
  const annotationNumbers = new Map(
    numberedAnnotations.map((annotation, index) => [annotation.annotation_id, index + 1]),
  );
  annotations.forEach((annotation) => {
    if (!annotation.page) return;
    const page = elements.paperPdfPages.querySelector(`.pdf-page[data-page="${annotation.page}"]`);
    if (!page) return;
    const rects = usableAnnotationRects(annotation);
    if (!rects.length) return;
    const isComment = ["note", "qa"].includes(annotation.kind);
    const isActive = state.activePaperAnnotationId === String(annotation.annotation_id);
    if (annotation.kind === "highlight" || isActive) rects.forEach((rect) => {
      const mark = document.createElement("button");
      mark.type = "button";
      mark.className = `paper-highlight-overlay paper-highlight-${annotation.kind}`;
      mark.style.left = `${rect.x * 100}%`;
      mark.style.top = `${rect.y * 100}%`;
      mark.style.width = `${rect.width * 100}%`;
      mark.style.height = `${rect.height * 100}%`;
      mark.title = annotation.content || annotation.question || annotation.selected_text || "论文批注";
      page.append(mark);
    });
    if (!isComment) return;
    const anchor = rects[0];
    const marker = document.createElement("button");
    const number = annotationNumbers.get(annotation.annotation_id);
    const afterText = anchor.x + anchor.width + 0.008;
    const markerX = afterText < 0.965 ? afterText : Math.max(0.006, anchor.x - 0.038);
    marker.type = "button";
    marker.className = `paper-annotation-marker${isActive ? " is-active" : ""}`;
    marker.style.left = `${markerX * 100}%`;
    marker.style.top = `${Math.max(0.002, anchor.y) * 100}%`;
    marker.setAttribute("aria-label", `打开批注 ${number}`);
    marker.title = `批注 ${number}：${annotation.content || annotation.question || annotation.selected_text || "查看批注"}`;
    marker.append(iconNode("message-circle"));
    const badge = document.createElement("span");
    badge.textContent = String(number);
    marker.append(badge);
    marker.addEventListener("click", () => focusPaperAnnotation(annotation));
    page.append(marker);
  });
  refreshIcons();
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
  if (state.activePaperAnnotationId === String(annotationId)) state.activePaperAnnotationId = null;
  renderPaperAnnotations();
  notify("批注已删除");
}

function updatePaperHorizontalScroller() {
  const scrollWidth = elements.paperPdfPages.scrollWidth;
  const clientWidth = elements.paperPdfPages.clientWidth;
  const needsHorizontalScroll = scrollWidth - clientWidth > 2;
  elements.paperHorizontalScroller.hidden = !needsHorizontalScroll;
  elements.paperHorizontalScrollContent.style.width = `${Math.max(scrollWidth, clientWidth)}px`;
  if (needsHorizontalScroll) {
    elements.paperHorizontalScroller.scrollLeft = elements.paperPdfPages.scrollLeft;
  }
}

function syncPaperHorizontalScroll(source, target) {
  if (state.paperScrollSyncing || source.scrollLeft === target.scrollLeft) return;
  state.paperScrollSyncing = true;
  target.scrollLeft = source.scrollLeft;
  window.requestAnimationFrame(() => {
    state.paperScrollSyncing = false;
  });
}

function nearestVisiblePaperPage() {
  const pages = [...elements.paperPdfPages.querySelectorAll(".pdf-page")];
  if (!pages.length) return null;
  const targetY = Math.max(110, window.innerHeight * 0.22);
  let nearest = null;
  let distance = Number.POSITIVE_INFINITY;
  pages.forEach((page) => {
    const rect = page.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) return;
    const currentDistance = Math.abs(rect.top - targetY);
    if (currentDistance < distance) {
      distance = currentDistance;
      nearest = Number(page.dataset.page);
    }
  });
  return nearest;
}

function queuePaperReadingProgress(pageNumber, immediate = false) {
  const workspace = state.paperWorkspace;
  const page = Number(pageNumber);
  if (!workspace?.paper?.library_id || !Number.isInteger(page) || page < 1) return;
  if (!immediate && state.paperCurrentPage === page) return;
  state.paperCurrentPage = page;
  window.clearTimeout(state.paperProgressTimer);
  const save = async () => {
    const relatedProjects = workspace.projects || [];
    const activeProject = relatedProjects.find(({ project }) => project.project_id === state.projectId);
    const projectId = activeProject?.project?.project_id
      || workspace.reading_progress?.project_id
      || relatedProjects[0]?.project?.project_id
      || null;
    try {
      const payload = await api(
        `/api/library/papers/${encodeURIComponent(workspace.paper.library_id)}/reading-progress`,
        {
          method: "PUT",
          body: JSON.stringify({
            page,
            attachment_id: workspace.workspace_attachment?.attachment_id || null,
            project_id: projectId,
          }),
        },
      );
      workspace.reading_progress = payload.data;
    } catch (_error) {
      // Reading must remain uninterrupted if a progress heartbeat cannot be saved.
    }
  };
  if (immediate) void save();
  else state.paperProgressTimer = window.setTimeout(save, 700);
}

function scrollToPaperPage(pageNumber) {
  const page = elements.paperPdfPages.querySelector(`.pdf-page[data-page="${pageNumber}"]`);
  if (!page) return;
  page.scrollIntoView({ behavior: "smooth", block: "start" });
  queuePaperReadingProgress(Number(pageNumber), true);
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
  updatePaperHorizontalScroller();
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
  updatePaperHorizontalScroller();
}

async function loadPaperPdf(attachment) {
  elements.paperPdfPages.innerHTML = '<div class="paper-pdf-placeholder"><p>正在通过 PDF.js 加载全文…</p></div>';
  elements.paperHorizontalScroller.hidden = true;
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
    elements.paperHorizontalScroller.hidden = true;
  }
}

async function openPaperWorkspace(
  libraryId,
  attachmentId = null,
  forceAcquire = false,
  resumePage = null,
) {
  showWorkspace("paper");
  elements.paperWorkspaceTitle.textContent = "正在加载论文…";
  elements.paperPdfPages.innerHTML = '<div class="paper-pdf-placeholder"><p>正在准备论文全文…</p></div>';
  elements.paperHorizontalScroller.hidden = true;
  state.activePaperAnnotationId = null;
  state.paperCurrentPage = null;
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
      elements.paperHorizontalScroller.hidden = true;
      refreshIcons();
      return;
    }
    await loadPaperPdf(attachment);
    const targetPage = Number(resumePage || workspace.reading_progress?.page || 1);
    window.requestAnimationFrame(() => {
      scrollToPaperPage(Math.max(1, targetPage));
    });
    refreshIcons();
  } catch (error) {
    elements.paperWorkspaceTitle.textContent = "论文研读工作台";
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
    ? `在“${parentName}”中新建二级文件夹，请输入名称`
    : "请输入研究名称，创建研究文件夹";
  const name = window.prompt(promptText);
  if (!name?.trim()) return;
  try {
    await api("/api/library/collections", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), parent_id: parentId }),
    });
    await loadLibrary();
    notify(parentId ? "二级文件夹已创建" : "研究文件夹已创建");
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
    if (!choices.length) {
      notify("请先创建研究文件夹", true);
      return;
    }
    const menu = choices
      .map((item, index) => `${index + 1}. ${collectionDisplayPath(item.collection_id)}`)
      .join("\n");
    const choice = Number(window.prompt(`选择要加入的研究文件夹：\n${menu}`, "1"));
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
    working.append(iconNode("loader-circle"), document.createTextNode(" 研究助手正在拆解问题并迭代取证…"));
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
    badge.textContent = data.mode === "agent" ? "智能取证" : "本地检索";
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

function filteredProjects() {
  const query = elements.projectSearch.value.trim().toLocaleLowerCase();
  return state.projects.filter((project) => {
    if (!query) return true;
    return [projectDisplayTitle(project), project.research_question, project.project_id]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase().includes(query));
  });
}

function projectConversation(project) {
  return project?.conversation || null;
}

function projectDisplayTitle(project) {
  return projectConversation(project)?.title || project?.topic || "未命名研究";
}

function projectListDisplayStage(project) {
  const isRunning = ["queued", "running"].includes(project.active_run?.status);
  const isReviewRevision =
    project.stage === "REVIEWED"
    && project.current_review?.verdict === "REVISE";
  if (isRunning) return "调研中";
  if (isReviewRevision) return "审查待修订";
  if (project.project_id === state.projectId && continuationMode(state.snapshot) === "recovery") {
    return recoverableOperationalFailure(state.snapshot) ? "写作待恢复" : "成果待补全";
  }
  return STAGE_LABELS[project.stage] || project.stage;
}

function projectListSignature(projects) {
  return JSON.stringify({
    current: state.projectId,
    query: elements.projectSearch.value.trim().toLocaleLowerCase(),
    selecting: state.projectSelectionMode,
    selected: [...state.selectedProjectIds].sort(),
    projects: projects.map((project) => ({
      id: project.project_id,
      title: projectDisplayTitle(project),
      stage: projectListDisplayStage(project),
      date: formatDate(project.updated_at),
      pinned: Boolean(projectConversation(project)?.pinned),
    })),
  });
}

function preserveExistingProjectOrder(incoming) {
  if (!state.projects.length) return incoming;
  const incomingById = new Map(incoming.map((project) => [project.project_id, project]));
  const existingIds = new Set(state.projects.map((project) => project.project_id));
  const newlyAdded = incoming.filter((project) => !existingIds.has(project.project_id));
  const existing = state.projects
    .map((project) => incomingById.get(project.project_id))
    .filter(Boolean);
  return [...newlyAdded, ...existing];
}

async function renameConversation(project) {
  const conversation = projectConversation(project);
  if (!conversation?.conversation_id) {
    notify("这个旧项目没有可编辑的对话记录", true);
    return;
  }
  const currentTitle = projectDisplayTitle(project);
  const nextTitle = window.prompt("修改对话名称", currentTitle)?.trim();
  if (!nextTitle || nextTitle === currentTitle) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(conversation.conversation_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title: nextTitle }),
    });
    await loadProjects();
    if (project.project_id === state.projectId) {
      const refreshed = state.projects.find((item) => item.project_id === state.projectId);
      state.conversation = refreshed?.conversation || state.conversation;
      if (state.snapshot && refreshed?.conversation) {
        state.snapshot.conversation = refreshed.conversation;
      }
    }
    notify("对话名称已更新");
  } catch (error) {
    notify(`修改失败：${error.message}`, true);
  }
}

async function toggleConversationPin(project) {
  const conversation = projectConversation(project);
  if (!conversation?.conversation_id) {
    notify("这个旧项目没有可置顶的对话记录", true);
    return;
  }
  try {
    await api(`/api/conversations/${encodeURIComponent(conversation.conversation_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ pinned: !conversation.pinned }),
    });
    await loadProjects();
    notify(conversation.pinned ? "已取消置顶" : "已置顶对话");
  } catch (error) {
    notify(`置顶操作失败：${error.message}`, true);
  }
}

async function deleteConversationFromSidebar(project) {
  const conversation = projectConversation(project);
  if (!conversation?.conversation_id) {
    notify("这个旧项目没有独立对话记录", true);
    return;
  }
  const title = projectDisplayTitle(project);
  if (!window.confirm(`永久删除“${title}”吗？\n\n对话、项目、研究产物和状态记录都会被删除，此操作无法撤销。`)) {
    return;
  }
  try {
    await api(`/api/conversations/${encodeURIComponent(conversation.conversation_id)}`, {
      method: "DELETE",
    });
    state.projects = state.projects.filter((item) => item.project_id !== project.project_id);
    if (project.project_id === state.projectId) clearProjectView();
    else renderProjectList();
    await loadProjects();
    notify("研究对话已删除");
  } catch (error) {
    notify(`删除失败：${error.message}`, true);
  }
}

function conversationMenuItem(label, iconName, action, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `menu-item${danger ? " danger-menu-item" : ""}`;
  button.append(iconNode(iconName), document.createTextNode(label));
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    closeConversationMenus();
    await action();
  });
  return button;
}

function renderRecentProjects() {
  elements.recentProjectList.replaceChildren();
  elements.recentProjects.hidden = !state.projects.length;
  state.projects.slice(0, 3).forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "start-recent-item";
    button.setAttribute("aria-label", `打开研究：${projectDisplayTitle(project)}`);

    const copy = document.createElement("span");
    copy.className = "start-recent-copy";
    const title = document.createElement("strong");
    title.textContent = projectDisplayTitle(project);
    const meta = document.createElement("span");
    meta.textContent = `${STAGE_LABELS[project.stage] || project.stage} · ${formatDate(project.updated_at)}`;
    copy.append(title, meta);

    button.append(copy, iconNode("arrow-right"));
    button.addEventListener("click", () => loadProject(project.project_id));
    elements.recentProjectList.append(button);
  });
}

function updateProjectBulkControls(projects = filteredProjects()) {
  const availableIds = new Set(state.projects.map((project) => project.project_id));
  state.selectedProjectIds = new Set(
    [...state.selectedProjectIds].filter((projectId) => availableIds.has(projectId)),
  );
  const selectedCount = state.selectedProjectIds.size;
  const allVisibleSelected = projects.length > 0
    && projects.every((project) => state.selectedProjectIds.has(project.project_id));
  elements.projectBulkBar.hidden = !state.projectSelectionMode;
  elements.projectSelectedCount.textContent = `已选 ${selectedCount} 项`;
  elements.selectAllProjects.textContent = allVisibleSelected ? "取消全选" : "全选";
  elements.selectAllProjects.disabled = state.projectBulkDeleting || !projects.length;
  elements.deleteSelectedProjects.disabled = state.projectBulkDeleting || !selectedCount;
  elements.deleteSelectedProjects.lastChild.textContent = state.projectBulkDeleting ? "删除中…" : "删除";
  elements.toggleProjectSelection.classList.toggle("is-active", state.projectSelectionMode);
  elements.toggleProjectSelection.setAttribute("aria-pressed", String(state.projectSelectionMode));
  const toggleLabel = state.projectSelectionMode ? "退出批量管理" : "批量管理历史研究";
  elements.toggleProjectSelection.setAttribute("aria-label", toggleLabel);
  elements.toggleProjectSelection.title = toggleLabel;
}

function setProjectSelectionMode(enabled) {
  if (state.projectBulkDeleting) return;
  state.projectSelectionMode = enabled;
  if (!enabled) state.selectedProjectIds.clear();
  renderProjectList();
}

function toggleVisibleProjectSelection() {
  const projects = filteredProjects();
  const allSelected = projects.length > 0
    && projects.every((project) => state.selectedProjectIds.has(project.project_id));
  projects.forEach((project) => {
    if (allSelected) state.selectedProjectIds.delete(project.project_id);
    else state.selectedProjectIds.add(project.project_id);
  });
  renderProjectList();
}

async function deleteSelectedProjectRecords() {
  if (state.projectBulkDeleting || !state.selectedProjectIds.size) return;
  const projectIds = [...state.selectedProjectIds];
  const confirmed = window.confirm(
    `确定永久删除选中的 ${projectIds.length} 项研究吗？\n\n相关研究产物和状态记录都会被删除，此操作无法撤销。`,
  );
  if (!confirmed) return;

  state.projectBulkDeleting = true;
  updateProjectBulkControls();
  const deletedIds = [];
  const failedIds = [];
  for (const projectId of projectIds) {
    try {
      await api(`/api/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
      deletedIds.push(projectId);
      state.completedProjectCache.delete(projectId);
    } catch {
      failedIds.push(projectId);
    }
  }

  state.projectBulkDeleting = false;
  state.selectedProjectIds = new Set(failedIds);
  state.projectSelectionMode = failedIds.length > 0;
  state.projects = state.projects.filter((project) => !deletedIds.includes(project.project_id));
  const currentProjectDeleted = deletedIds.includes(state.projectId);
  if (currentProjectDeleted) clearProjectView();
  else renderProjectList();
  await loadProjects();

  if (failedIds.length) {
    notify(`已删除 ${deletedIds.length} 项，${failedIds.length} 项删除失败，请重试`, true);
  } else {
    notify(`已删除 ${deletedIds.length} 项历史研究`);
  }
}

function renderProjectList() {
  const projects = filteredProjects();
  updateProjectBulkControls(projects);
  const signature = projectListSignature(projects);
  const recentPopover = document.getElementById("recentHistoryPopover");
  if (signature === state.projectListRenderSignature && elements.projectList.childElementCount) {
    if (recentPopover && !recentPopover.hidden) renderRecentHistoryPopover();
    return;
  }
  state.projectListRenderSignature = signature;
  const previousScrollTop = elements.projectList.scrollTop;
  hideProjectPreview();
  elements.projectList.replaceChildren();
  const finishRender = () => {
    elements.projectList.scrollTop = previousScrollTop;
    if (recentPopover && !recentPopover.hidden) renderRecentHistoryPopover();
  };
  if (!state.projects.length) {
    const empty = document.createElement("p");
    empty.className = "muted small sidebar-label";
    empty.textContent = "还没有项目记录";
    elements.projectList.append(empty);
    finishRender();
    return;
  }

  if (!projects.length) {
    const empty = document.createElement("p");
    empty.className = "muted small sidebar-label";
    empty.textContent = "没有找到匹配的研究";
    elements.projectList.append(empty);
    finishRender();
    return;
  }

  projects.forEach((project) => {
    const isRunning = ["queued", "running"].includes(project.active_run?.status);
    const displayStage = projectListDisplayStage(project);
    const conversation = projectConversation(project);
    const displayTitle = projectDisplayTitle(project);
    const entry = document.createElement("div");
    entry.className = "project-list-entry";
    entry.dataset.conversationId = conversation?.conversation_id || "";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-list-item";
    button.classList.toggle("is-active", project.project_id === state.projectId);
    button.classList.toggle("is-selecting", state.projectSelectionMode);
    button.classList.toggle("is-selected", state.selectedProjectIds.has(project.project_id));
    if (state.projectSelectionMode) {
      button.setAttribute(
        "aria-pressed",
        String(state.selectedProjectIds.has(project.project_id)),
      );
    }
    button.setAttribute("aria-current", project.project_id === state.projectId ? "page" : "false");
    button.setAttribute("aria-label", `${displayTitle}，${displayStage}`);

    const icon = document.createElement("span");
    icon.className = "project-list-icon";
    if (state.projectSelectionMode) {
      icon.classList.add("project-selection-box");
    } else {
      icon.append(
        iconNode(
          isRunning
            ? "loader-circle"
            : project.project_id === state.projectId
              ? "folder-open"
              : "folder",
        ),
      );
    }
    icon.classList.toggle("is-running", isRunning);

    const content = document.createElement("span");
    content.className = "project-list-content";

    const title = document.createElement("span");
    title.className = "project-list-title";
    title.textContent = displayTitle;

    const titleRow = document.createElement("span");
    titleRow.className = "project-list-title-row";
    titleRow.append(title);
    if (conversation?.pinned) {
      const pinned = document.createElement("span");
      pinned.className = "project-list-pinned";
      pinned.title = "已置顶";
      pinned.append(iconNode("pin"));
      titleRow.append(pinned);
    }

    const meta = document.createElement("span");
    meta.className = "project-list-meta";
    const stage = document.createElement("span");
    stage.textContent = displayStage;
    const date = document.createElement("span");
    date.textContent = formatDate(project.updated_at);
    meta.append(stage, date);

    content.append(titleRow, meta);
    button.append(icon, content);
    button.addEventListener("click", () => {
      hideProjectPreview();
      closeConversationMenus();
      if (state.projectSelectionMode) {
        if (state.selectedProjectIds.has(project.project_id)) {
          state.selectedProjectIds.delete(project.project_id);
        } else {
          state.selectedProjectIds.add(project.project_id);
        }
        renderProjectList();
        return;
      }
      loadProject(project.project_id);
    });
    button.addEventListener("mouseenter", () => showProjectPreview(project, button));
    button.addEventListener("mouseleave", hideProjectPreview);
    button.addEventListener("focus", () => showProjectPreview(project, button));
    button.addEventListener("blur", hideProjectPreview);

    const menuToggle = document.createElement("button");
    menuToggle.type = "button";
    menuToggle.className = "icon-button project-list-menu-toggle";
    menuToggle.setAttribute("aria-label", `打开“${displayTitle}”的对话操作`);
    menuToggle.setAttribute("aria-haspopup", "menu");
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.hidden = state.projectSelectionMode;
    menuToggle.append(iconNode("ellipsis"));

    const menu = document.createElement("div");
    menu.className = "popover project-list-menu";
    menu.setAttribute("role", "menu");
    const keepMenuOpen = Boolean(
      conversation?.conversation_id
      && state.openConversationMenuId === conversation.conversation_id,
    );
    menu.hidden = !keepMenuOpen;
    menuToggle.setAttribute("aria-expanded", String(keepMenuOpen));
    menu.append(
      conversationMenuItem(
        conversation?.pinned ? "取消置顶" : "置顶",
        conversation?.pinned ? "pin-off" : "pin",
        () => toggleConversationPin(project),
      ),
      conversationMenuItem("修改名称", "pencil", () => renameConversation(project)),
      conversationMenuItem("删除", "trash-2", () => deleteConversationFromSidebar(project), true),
    );
    menuToggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = menu.hidden;
      closeConversationMenus(open ? menu : null);
      menu.hidden = !open;
      menuToggle.setAttribute("aria-expanded", String(open));
      if (open) menu.querySelector("button")?.focus();
    });

    entry.append(button, menuToggle, menu);
    elements.projectList.append(entry);
  });
  finishRender();
  refreshIcons();
}

async function loadProjects(options = {}) {
  if (state.projectsLoading) return;
  state.projectsLoading = true;
  try {
    const payload = await api("/api/projects?limit=30");
    const incoming = payload.data || [];
    state.projects = options?.preserveOrder
      ? preserveExistingProjectOrder(incoming)
      : incoming;
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
  state.projectLoadSession += 1;
  state.projectLoadController?.abort();
  state.projectLoadController = null;
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
  state.researchSelection = "";
  state.researchNotes = [];
  state.researchNotesProjectId = null;
  state.researchLastAnswer = null;
  state.researchChatHistory = [];
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
    state.completedProjectCache.delete(projectId);
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

function progressStageIndex(stage) {
  return PROGRESS_STAGES.findIndex((group) => group.stages.includes(stage));
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
  const displayStage = (
    state.busy
    && progress.activeStage === "CREATED"
    && ["thinking", "searching"].includes(state.runPhase)
  ) ? "SEARCHED" : progress.activeStage;
  const current = progressStageIndex(displayStage);
  const visited = new Set(progress.visitedStages);
  const visitCounts = new Map();
  orderedWorkflowEvents(events).forEach((event) => {
    const groupIndex = progressStageIndex(event.to_stage);
    if (groupIndex < 0) return;
    const groupKey = PROGRESS_STAGES[groupIndex].key;
    visitCounts.set(groupKey, (visitCounts.get(groupKey) || 0) + 1);
  });
  elements.stageStepper.classList.toggle("is-running", state.busy);
  elements.stageStepper.classList.toggle("is-syncing", !progress.aligned);
  elements.stageStepper.setAttribute(
    "aria-label",
    progress.aligned ? "研究进度" : "研究进度正在与事件记录同步",
  );
  PROGRESS_STAGES.forEach((group, index) => {
    const item = document.createElement("li");
    item.className = "stage-step";
    item.dataset.stage = group.key;
    const groupVisited = group.stages.some((internalStage) => visited.has(internalStage));
    if (index > 0 && index <= current) {
      item.classList.add("has-complete-connector");
    }
    if (index < current) {
      item.classList.add("is-complete");
    } else if (groupVisited && index !== current) {
      item.classList.add("is-revisited");
    }
    if (index === current) {
      item.classList.add("is-current");
      item.setAttribute("aria-current", "step");
      if (progress.terminal) item.classList.add("is-terminal");
    }
    const visits = visitCounts.get(group.key) || (group.key === "create" ? 1 : 0);
    if (visits > 1) item.title = `${group.label}，已更新 ${visits} 次`;
    if (index === current && progress.terminal) {
      item.title = `流程停止于${group.label}，项目状态为证据不足`;
    } else if (index === current) {
      item.title = `当前阶段：${group.label}（${STAGE_LABELS[progress.activeStage] || progress.activeStage}）`;
    }
    item.textContent = group.label;
    elements.stageStepper.append(item);
  });
}

function renderStageBadge(project) {
  elements.stageBadge.textContent = state.busy
    ? "研究进行中"
    : STAGE_LABELS[project.stage] || project.stage;
  elements.stageBadge.className = "stage-badge";
  if (["COMPLETED", "REVIEWED", "NARRATED"].includes(project.stage) && !state.busy) {
    elements.stageBadge.classList.add("is-done");
  }
  if (project.stage === "INCONCLUSIVE") {
    elements.stageBadge.classList.add("is-terminal");
  }
}

function renderProjectHeader(project, events = state.snapshot?.events || []) {
  if (state.projectId && state.projectId !== project.project_id) {
    state.researchSelection = "";
    state.researchLastAnswer = null;
    state.researchChatHistory = [];
    state.researchNotes = [];
    state.researchNotesProjectId = null;
  }
  state.project = project;
  state.projectId = project.project_id;
  showWorkspace("project");
  elements.projectIdLabel.textContent = project.project_id;
  elements.projectTopic.textContent = project.topic || "未命名研究";
  elements.projectQuestion.textContent = project.research_question || "";
  const needsReviewRevision =
    project.stage === "REVIEWED"
    && project.current_review?.verdict === "REVISE";
  elements.stageBadge.textContent = needsReviewRevision
    ? "审查待修订"
    : STAGE_LABELS[project.stage] || project.stage;
  elements.stageBadge.className = "stage-badge";
  if (needsReviewRevision) {
    elements.stageBadge.classList.add("is-warning");
  } else if (["COMPLETED", "REVIEWED", "NARRATED"].includes(project.stage)) {
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
  const candidateCount = payload.candidate_count ?? candidates.length;
  parts.push(h('div',{cls:'aw-row'}, [h('span',{cls:'aw-label'},'候选论文'), h('span',{cls:'aw-badge'},`${candidateCount} 篇`)]));
  const decisions = payload.screening_decisions || {};
  const dk = Object.keys(decisions);
  const summary = payload.screening_summary || {};
  if (dk.length || Object.values(summary).some(Boolean)) {
    const inc = summary.include ?? dk.filter(k => decisions[k]==='include').length;
    const exc = summary.exclude ?? dk.filter(k => decisions[k]==='exclude').length;
    const unc = summary.uncertain ?? dk.filter(k => decisions[k]==='uncertain').length;
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
      h('td',{cls:'aw-ev-id'},renderEvidenceCitation(f.evidence_id||'')),
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
        h('div',{cls:'aw-ev-refs'}, (c.evidence_ids||[]).map(eid => renderEvidenceCitation(eid))),
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
          ...(g.supporting_paper_ids||[]).map(pid => renderEvidenceCitation(pid)),
          ...(g.conflicting_paper_ids||[]).length ? [h('span',{cls:'muted'},' 冲突: '), ...(g.conflicting_paper_ids||[]).map(pid => renderEvidenceCitation(pid, 'warn'))] : [],
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
    verified.length ? h('div',{cls:'aw-inline'}, verified.map(eid => renderEvidenceCitation(eid, 'ok'))) : h('span',{cls:'muted'},'无'),
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
      h('div',{cls:'aw-inline'}, included.map(id => renderEvidenceCitation(id)))
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
  const candidateCount = payload.candidate_count ?? candidates.length;
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'候选集'),
    h('div',{cls:'aw-inline'}, [
      h('span',{cls:'aw-badge'},`${candidateCount} 篇候选`),
      h('span',{cls:'aw-badge'},`${(payload.excluded_paper_ids||[]).length} 篇已排除`),
    ])
  ]));
  parts.push(h('div',{cls:'aw-row'}, [
    h('span',{cls:'aw-label'},'系统自动检索'),
    h('span',{},`${payload.search_round||0} / ${payload.max_search_rounds||3} 轮`)
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
      h('span',{cls:'aw-label'},'用户提交查询'),
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

function decorateMarkdownCitations(container) {
  if (!state.citationIndex.size) return;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  textNodes.forEach((textNode) => {
    const parent = textNode.parentElement;
    if (!parent || parent.closest("code, pre, a, button")) return;
    const text = textNode.textContent || "";
    const pattern = /\[([^\]\n]{1,180})\]|\(([^)\n]{1,180})\)/g;
    const matches = [...text.matchAll(pattern)]
      .map((match) => ({ match, reference: match[1] || match[2] }))
      .filter(({ reference }) => resolveCitationMeta(reference));
    if (!matches.length) return;
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    matches.forEach(({ match, reference }) => {
      if (match.index > cursor) fragment.append(document.createTextNode(text.slice(cursor, match.index)));
      fragment.append(renderEvidenceCitation(reference));
      cursor = match.index + match[0].length;
    });
    if (cursor < text.length) fragment.append(document.createTextNode(text.slice(cursor)));
    textNode.replaceWith(fragment);
  });
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
  decorateMarkdownCitations(container);
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
      h('div',{cls:'aw-inline'}, cited.map(eid => renderEvidenceCitation(eid)))
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
        ...cited.map(eid => renderEvidenceCitation(eid)),
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
        r.paper_id ? renderEvidenceCitation(r.paper_id) : null,
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
  CREATED: ["准备检索", "研究助手正在分析研究问题并生成检索方案。"],
  SEARCHED: ["已找到候选论文", "系统已完成初步检索，正在生成可供你审核的候选集。"],
  SEARCH_REVIEW_PENDING: ["请审核候选论文", "勾选需要精读的论文；你也可以补充检索词或手动加入 DOI。"],
  SCREENED: ["可以开始精读", "候选集已经确认，点击继续研究后将逐篇读取论文并提取证据。"],
  EXTRACTED: ["证据已提取", "论文精读已经完成，下一步会综合比较证据。"],
  SYNTHESIZED: ["综合完成", "系统已经形成共识、冲突和研究空白，等待独立审查。"],
  REVIEW_PENDING: ["等待证据审查", "系统将检查综合结论是否都有证据支撑。"],
  REVIEWED: ["证据审查完成", "请根据审查结论继续写作，或先修订综合结论并重新审查。"],
  OUTLINED: ["大纲已生成", "系统已规划章节结构，下一步逐节撰写正文。"],
  NARRATED: ["综述已生成", "完整综述已经生成，可直接结束当前研究。"],
  COMPLETED: ["研究已完成", "最终综述已生成，可查看下方成果。"],
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

function latestReviewPassed(snapshot) {
  return latestArtifact(snapshot, "ReviewResult")?.payload?.verdict === "PASS";
}

function latestReviewVerdict(snapshot) {
  return latestArtifact(snapshot, "ReviewResult")?.payload?.verdict
    || snapshot?.project?.current_review?.verdict
    || null;
}

function narrativeCompletion(snapshot) {
  const narrativeArtifact = latestArtifact(snapshot, "NarrativeReview");
  const sections = narrativeArtifact?.payload?.sections || [];
  if (!sections.length) return { complete: false, missing: [], narrative: null };
  return { complete: true, missing: [], narrative: narrativeArtifact.payload };
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
  const reviewVerdict = latestReviewVerdict(snapshot);
  if (stage === "SCREENED") return "screening";
  if (
    reviewVerdict === "REVISE"
    && ["REVIEWED", "EXTRACTED", "SYNTHESIZED", "REVIEW_PENDING"].includes(stage)
  ) {
    const reviseCount = (snapshot?.artifacts || []).filter(
      (artifact) => artifact.kind === "ReviewResult" && artifact.payload?.verdict === "REVISE",
    ).length;
    return reviseCount >= 2 ? null : "pipeline";
  }
  if (["EXTRACTED", "SYNTHESIZED", "REVIEW_PENDING"].includes(stage)) {
    return "pipeline";
  }
  if (["REVIEWED", "OUTLINED", "NARRATED"].includes(stage) && reviewVerdict === "PASS") {
    return "narrative";
  }
  if (stage === "COMPLETED" && latestReviewPassed(snapshot)) {
    return narrativeCompletion(snapshot).complete ? null : "recovery";
  }
  if (recoverableOperationalFailure(snapshot)) return "recovery";
  if (
    latestFailedRun(snapshot)
    && !snapshot?.active_run
    && !["COMPLETED", "SEARCH_REVIEW_PENDING"].includes(stage)
  ) {
    return "retry";
  }
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

function renderResearchCitations(citations = []) {
  if (!citations.length) return null;
  return h("div", { cls: "research-answer-sources" }, [
    h("h5", {}, `引用来源（${citations.length}）`),
    ...citations.map((citation) => h("article", {}, [
      h("strong", {}, `${citation.citation || ""} ${citation.title || "研究材料"}${citation.page ? ` · 第 ${citation.page} 页` : ""}`),
      h("blockquote", {}, citation.quote || ""),
    ])),
  ]);
}

function researchNoteLabel(kind) {
  return { note: "笔记", annotation: "批注", qa: "问答" }[kind] || "记录";
}

function renderResearchNotesList(container) {
  container.replaceChildren();
  if (!state.researchNotes.length) {
    container.append(h("p", { cls: "muted small" }, "还没有笔记或批注。"));
    return;
  }
  state.researchNotes.forEach((note) => {
    const body = note.kind === "qa"
      ? [
          h("p", { cls: "research-note-question" }, note.question || ""),
          renderMarkdown(note.answer || "", "aw-markdown"),
        ]
      : [h("p", {}, note.content || "")];
    container.append(h("article", { cls: `research-note research-note-${note.kind}` }, [
      h("header", {}, [
        h("span", {}, researchNoteLabel(note.kind)),
        h("button", {
          cls: "icon-button",
          type: "button",
          title: "删除记录",
          onClick: () => deleteResearchNote(note.note_id),
        }, iconNode("trash-2")),
      ]),
      note.selected_text ? h("blockquote", {}, note.selected_text) : null,
      ...body,
      h("small", { cls: "muted" }, formatDate(note.updated_at)),
    ]));
  });
  refreshIcons();
}

async function loadResearchNotes(projectId = state.projectId) {
  if (!projectId) return;
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(projectId)}/notes`);
    if (projectId !== state.projectId) return;
    state.researchNotes = payload.data || [];
    state.researchNotesProjectId = projectId;
    const list = elements.primaryOutcome.querySelector(".research-notes-list");
    if (list) renderResearchNotesList(list);
  } catch (error) {
    notify(`研究笔记载入失败：${error.message}`, true);
  }
}

async function deleteResearchNote(noteId) {
  try {
    await api(`/api/projects/${encodeURIComponent(state.projectId)}/notes/${encodeURIComponent(noteId)}`, {
      method: "DELETE",
    });
    state.researchNotes = state.researchNotes.filter((note) => note.note_id !== noteId);
    const list = elements.primaryOutcome.querySelector(".research-notes-list");
    if (list) renderResearchNotesList(list);
    notify("记录已删除");
  } catch (error) {
    notify(`删除失败：${error.message}`, true);
  }
}

async function saveResearchRecord(record, button = null) {
  try {
    if (button) button.disabled = true;
    const payload = await api(`/api/projects/${encodeURIComponent(state.projectId)}/notes`, {
      method: "POST",
      body: JSON.stringify(record),
    });
    state.researchNotes = [payload.data, ...state.researchNotes];
    const list = elements.primaryOutcome.querySelector(".research-notes-list");
    if (list) renderResearchNotesList(list);
    notify(record.kind === "qa" ? "问答已保存" : record.kind === "annotation" ? "批注已保存" : "笔记已保存");
    return true;
  } catch (error) {
    notify(`保存失败：${error.message}`, true);
    return false;
  } finally {
    if (button) button.disabled = false;
  }
}

async function toggleResearchFavorite(button) {
  const conversation = state.conversation || state.snapshot?.conversation;
  if (!conversation?.conversation_id) {
    notify("该研究缺少可收藏的会话记录", true);
    return;
  }
  try {
    button.disabled = true;
    const payload = await api(`/api/conversations/${encodeURIComponent(conversation.conversation_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ pinned: !conversation.pinned }),
    });
    const updated = payload.data;
    state.conversation = updated;
    if (state.snapshot) state.snapshot.conversation = updated;
    button.classList.toggle("is-active", updated.pinned);
    button.replaceChildren(iconNode(updated.pinned ? "bookmark-check" : "bookmark"), document.createTextNode(updated.pinned ? " 已收藏" : " 收藏研究"));
    await loadProjects();
    refreshIcons();
    notify(updated.pinned ? "研究已收藏并置顶" : "已取消收藏");
  } catch (error) {
    notify(`收藏操作失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function renderResearchWorkspace(narrative, reviewElement) {
  const conversation = state.conversation || state.snapshot?.conversation;
  const panel = h("aside", { cls: "research-workspace", "aria-label": "综述问答与笔记" });
  const favorite = h("button", {
    cls: `research-favorite${conversation?.pinned ? " is-active" : ""}`,
    type: "button",
    onClick: (event) => toggleResearchFavorite(event.currentTarget),
  }, [iconNode(conversation?.pinned ? "bookmark-check" : "bookmark"), ` ${conversation?.pinned ? "已收藏" : "收藏研究"}`]);
  const askTab = h("button", { type: "button", cls: state.researchWorkspaceTab === "ask" ? "is-active" : "" }, "聊天");
  const notesTab = h("button", { type: "button", cls: state.researchWorkspaceTab === "notes" ? "is-active" : "" }, "批注 / 笔记");
  const askPane = h("section", { cls: "research-workspace-pane" });
  const notesPane = h("section", { cls: "research-workspace-pane" });
  const switchTab = (tab) => {
    state.researchWorkspaceTab = tab;
    askTab.classList.toggle("is-active", tab === "ask");
    notesTab.classList.toggle("is-active", tab === "notes");
    askPane.hidden = tab !== "ask";
    notesPane.hidden = tab !== "notes";
  };
  askTab.addEventListener("click", () => switchTab("ask"));
  notesTab.addEventListener("click", () => switchTab("notes"));

  let noteSelection = null;
  let noteSave = null;
  const selectionContext = h("div", { cls: "research-selection-context", "aria-live": "polite" });
  const renderSelectionContext = () => {
    selectionContext.replaceChildren();
    selectionContext.classList.toggle("has-selection", Boolean(state.researchSelection));
    if (!state.researchSelection) {
      selectionContext.append(
        h("div", { cls: "research-selection-empty" }, [
          iconNode("mouse-pointer-2"),
          h("div", {}, [
            h("strong", {}, "引用综述原文"),
            h("small", {}, "在左侧划选文字，聊天时会自动带入"),
          ]),
        ]),
      );
      refreshIcons();
      return;
    }
    const clearSelection = h("button", {
      cls: "icon-button research-selection-clear",
      type: "button",
      title: "移除引用上下文",
      "aria-label": "移除引用上下文",
      onClick: () => {
        state.researchSelection = "";
        window.getSelection()?.removeAllRanges();
        if (noteSelection) noteSelection.textContent = "未选择综述原文，将保存为普通笔记。";
        if (noteSave) noteSave.textContent = "保存笔记";
        renderSelectionContext();
      },
    }, iconNode("x"));
    selectionContext.append(
      h("div", { cls: "research-selection-card" }, [
        h("div", { cls: "research-selection-card-head" }, [
          h("span", { cls: "research-selection-label" }, [iconNode("quote"), " 已引用选段"]),
          h("span", { cls: "research-selection-count" }, `${state.researchSelection.length} 字`),
          clearSelection,
        ]),
        h("p", { cls: "research-selection-preview" }, state.researchSelection),
      ]),
    );
    refreshIcons();
  };
  renderSelectionContext();
  const question = h("textarea", { rows: "3", maxlength: "4000", placeholder: state.researchChatHistory.length ? "继续追问…" : "输入问题…", "aria-label": "研究问题" });
  const submit = h("button", { type: "submit", cls: "primary" }, "发送");
  const chatThread = h("div", { cls: "research-chat-thread", "aria-live": "polite" });
  const isChatNearBottom = () => (
    chatThread.scrollHeight - chatThread.scrollTop - chatThread.clientHeight <= 48
  );
  const scrollChatToBottom = () => {
    chatThread.scrollTop = chatThread.scrollHeight;
  };
  const appendChatBubble = (message, { streaming = false } = {}) => {
    const body = h("div", { cls: "research-chat-bubble-body" });
    if (streaming && !message.content) {
      body.append(h("p", { cls: "research-answer-working" }, [iconNode("loader-circle"), " 正在回答…"]));
    } else if (message.role === "assistant") {
      body.append(renderMarkdown(message.content || "", "aw-markdown"));
    } else {
      body.textContent = message.content || "";
    }
    const bubble = h("article", { cls: `research-chat-bubble is-${message.role}` }, [body]);
    chatThread.append(bubble);
    scrollChatToBottom();
    return { bubble, body };
  };
  state.researchChatHistory.forEach((message) => appendChatBubble(message));
  requestAnimationFrame(scrollChatToBottom);
  const form = h("form", { cls: "research-question-form" }, [question, submit]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const cleanQuestion = question.value.trim();
    if (!cleanQuestion) return;
    const requestHistory = state.researchChatHistory.filter((message) => !message.failed).slice(-40);
    const userMessage = { role: "user", content: cleanQuestion };
    const assistantMessage = { role: "assistant", content: "" };
    state.researchChatHistory.push(userMessage, assistantMessage);
    appendChatBubble(userMessage);
    const assistantBubble = appendChatBubble(assistantMessage, { streaming: true });
    question.value = "";
    try {
      submit.disabled = true;
      refreshIcons();
      const response = await fetch(`/api/projects/${encodeURIComponent(state.projectId)}/assistant/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope: "project",
          question: cleanQuestion,
          selected_text: state.researchSelection,
          history: requestHistory,
        }),
      });
      if (!response.ok) {
        let payload = null;
        try { payload = await response.json(); } catch { payload = null; }
        throw new Error(errorMessage(payload, `请求失败（HTTP ${response.status}）`));
      }
      if (!response.body) throw new Error("浏览器无法读取流式回答");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streamError = "";
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
          try { payload = JSON.parse(dataLines.join("\n") || "{}"); }
          catch { payload = { message: dataLines.join("\n") }; }
          if (eventName === "delta") {
            const shouldFollowStream = isChatNearBottom();
            assistantMessage.content += payload.text || "";
            assistantBubble.body.replaceChildren(
              renderMarkdown(assistantMessage.content, "aw-markdown"),
            );
            if (shouldFollowStream) scrollChatToBottom();
          } else if (eventName === "error") {
            streamError = payload.message || "模型回答失败";
          }
        }
        if (done) break;
      }
      if (streamError) throw new Error(streamError);
      if (!assistantMessage.content.trim()) throw new Error("模型没有返回内容");
      state.researchChatHistory = state.researchChatHistory.slice(-40);
      const save = h("button", { type: "button", cls: "secondary research-save-answer" }, "保存这组问答");
      save.addEventListener("click", () => saveResearchRecord({
        kind: "qa",
        selected_text: state.researchSelection,
        question: cleanQuestion,
        answer: assistantMessage.content,
        citations: [],
      }, save));
      assistantBubble.bubble.append(save);
      question.placeholder = "继续追问…";
    } catch (error) {
      assistantMessage.content = `回答失败：${error.message}`;
      assistantMessage.failed = true;
      assistantBubble.body.replaceChildren(h("p", { cls: "error-text" }, assistantMessage.content));
    } finally {
      submit.disabled = false;
      question.focus();
    }
  });
  askPane.append(selectionContext, chatThread, form);

  noteSelection = h("blockquote", { cls: "research-note-selection" }, state.researchSelection || "未选择综述原文，将保存为普通笔记。" );
  const noteInput = h("textarea", { rows: "4", maxlength: "20000", placeholder: "记录判断、疑问或后续线索…", "aria-label": "研究笔记" });
  noteSave = h("button", { type: "button", cls: "primary" }, state.researchSelection ? "保存批注" : "保存笔记");
  noteSave.addEventListener("click", async () => {
    const content = noteInput.value.trim();
    if (!content) return;
    const annotatedText = state.researchSelection;
    const saved = await saveResearchRecord({
      kind: annotatedText ? "annotation" : "note",
      selected_text: annotatedText,
      content,
    }, noteSave);
    if (saved) noteInput.value = "";
  });
  const list = h("div", { cls: "research-notes-list" });
  renderResearchNotesList(list);
  notesPane.append(noteSelection, noteInput, noteSave, list);
  switchTab(state.researchWorkspaceTab);

  reviewElement.addEventListener("mouseup", () => {
    const selected = window.getSelection();
    const text = selected?.toString().trim().slice(0, 12000) || "";
    if (!text || !selected?.anchorNode || !reviewElement.contains(selected.anchorNode)) return;
    state.researchSelection = text;
    renderSelectionContext();
    noteSelection.textContent = text;
    noteSave.textContent = "保存批注";
  });
  panel.append(
    h("header", {}, [h("div", {}, [h("strong", {}, "研究工作台"), h("small", {}, "多轮聊天、批注与笔记")]), favorite]),
    h("nav", { cls: "research-workspace-tabs" }, [askTab, notesTab]),
    askPane,
    notesPane,
  );
  return panel;
}

function createResearchWorkspaceResizer(layout) {
  const handle = h("div", {
    cls: "research-workspace-resizer",
    role: "separator",
    tabindex: "0",
    "aria-label": "调整综述与研究工作台宽度",
    "aria-orientation": "vertical",
    "aria-valuemin": "380",
    "aria-valuenow": String(state.researchWorkspaceWidth),
  }, h("span", { "aria-hidden": "true" }));
  const applyWidth = (width) => {
    const rect = elements.projectView.getBoundingClientRect();
    const maximum = Math.max(380, rect.width - 372);
    const next = Math.round(Math.min(maximum, Math.max(380, width)));
    state.researchWorkspaceWidth = next;
    layout.style.setProperty("--research-workspace-width", `${next}px`);
    elements.projectView.style.setProperty("--research-workspace-width", `${next}px`);
    handle.setAttribute("aria-valuenow", String(next));
  };
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.classList.add("is-dragging");
    handle.setPointerCapture?.(event.pointerId);
    const onMove = (moveEvent) => {
      applyWidth(window.innerWidth - 40 - moveEvent.clientX - 16);
    };
    const onUp = () => {
      handle.classList.remove("is-dragging");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  });
  handle.addEventListener("keydown", (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    applyWidth(state.researchWorkspaceWidth + (event.key === 'ArrowLeft' ? 24 : -24));
  });
  handle.addEventListener("dblclick", () => applyWidth(430));
  layout.style.setProperty("--research-workspace-width", `${state.researchWorkspaceWidth}px`);
  elements.projectView.style.setProperty("--research-workspace-width", `${state.researchWorkspaceWidth}px`);
  return handle;
}

function renderOutcome(snapshot) {
  const narrative = latestArtifact(snapshot, "NarrativeReview")?.payload;
  const review = latestArtifact(snapshot, "ReviewResult")?.payload;
  const insufficient = snapshot?.project?.stage === "INCONCLUSIVE"
    ? latestArtifact(snapshot, "InsufficientEvidence")?.payload
    : null;
  const needsRecovery = continuationMode(snapshot) === "recovery";
  const operationalRecovery = recoverableOperationalFailure(snapshot);
  const savedDrafts = currentSectionDrafts(snapshot);
  elements.primaryOutcome.replaceChildren();
  elements.projectSummary.classList.remove("has-outcome");
  elements.projectView.classList.toggle("has-research-workspace", Boolean(narrative));

  if (narrative) {
    elements.primaryOutcome.hidden = false;
    elements.projectSummary.classList.add("has-outcome");
    elements.primaryOutcome.append(
      h("div", { cls: "outcome-header" }, [
        h("div", {}, [
          h("p", { cls: "eyebrow" }, needsRecovery ? "成果待补全" : "最终成果"),
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
    const reviewElement = renderNarrativeReviewHTML(narrative);
    const researchLayout = h("div", { cls: "research-review-layout" });
    const workspace = renderResearchWorkspace(narrative, reviewElement);
    researchLayout.append(
      reviewElement,
      createResearchWorkspaceResizer(researchLayout),
      workspace,
    );
    elements.primaryOutcome.append(researchLayout);
    if (state.researchNotesProjectId !== state.projectId) {
      state.researchNotes = [];
      loadResearchNotes(state.projectId);
    }
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
          ? "检索、筛选、证据提取和章节写作均已保留；本次停止来自主编输出格式故障，不代表证据不足。继续后将直接恢复综述整合。"
          : "检索、论文筛选和研究结果整理均已完成，但旧流程提前结束了项目。可复用现有研究材料继续生成提纲和正文。",
      ),
    );
    return;
  }

  if (snapshot?.project?.stage === "REVIEWED" && review?.verdict === "REVISE") {
    const issues = review.fatal_issues || [];
    elements.primaryOutcome.hidden = false;
    elements.projectSummary.classList.add("has-outcome");
    elements.primaryOutcome.append(
      h("div", { cls: "outcome-header" }, [
        h("div", {}, [
          h("p", { cls: "eyebrow" }, "Evidence review"),
          h("h3", {}, "证据审查要求修订"),
        ]),
        h("span", { cls: "outcome-badge is-warning" }, "REVISE"),
      ]),
      h(
        "p",
        { cls: "outcome-abstract" },
        "论文精读和证据均已保留。下一步将按照审查意见修订综合结论，再进行一次独立证据审查。",
      ),
      issues.length
        ? h(
          "ul",
          { cls: "quality-note" },
          issues.slice(0, 4).map((issue) => h("li", {}, issue)),
        )
        : null,
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
  const failedRun = latestFailedRun(snapshot);
  const needsRecovery = continuationMode(snapshot) === "recovery";
  if (failedRun) {
    const detail = failedRun.error || failedRun.message || "后台执行遇到异常。";
    title = "研究任务运行失败";
    text = `已保留成功写入的阶段数据，可以点击下方按钮重试。错误详情：${detail}`;
    elements.stageBadge.textContent = "运行失败";
    elements.stageBadge.className = "stage-badge is-warning";
  } else if (needsRecovery) {
    const completion = narrativeCompletion(snapshot);
    const operationalRecovery = recoverableOperationalFailure(snapshot);
    const savedDrafts = currentSectionDrafts(snapshot);
    title = operationalRecovery && savedDrafts.length
        ? `${savedDrafts.length} 个章节草稿等待整合`
        : "综述正文尚未生成";
    text = operationalRecovery
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

  const latestSearch = latestArtifact(snapshot, "SearchReport")?.payload;
  const latestScreening = latestArtifact(snapshot, "ScreeningDecision")?.payload;
  const narrative = latestArtifact(snapshot, "NarrativeReview")?.payload;
  const metricsAvailable = [
    "EXTRACTED",
    "SYNTHESIZED",
    "REVIEW_PENDING",
    "REVIEWED",
    "OUTLINED",
    "NARRATED",
    "COMPLETED",
  ].includes(project.stage);
  const metrics = [
    ["候选论文", latestSearch?.candidate_count ?? latestSearch?.candidates?.length ?? 0, "首次检索去重结果"],
    ["入选精读", latestScreening?.included_paper_ids?.length || 0, "已确认的论文"],
    ["证据摘录", countFindings(snapshot), "可追踪的研究证据"],
    ["综述章节", narrative?.sections?.length || 0, "最终正文"],
  ].filter(([, value]) => metricsAvailable && value > 0);
  elements.resultHighlights.replaceChildren(
    ...metrics.map(([label, value, hint]) => metricCard(label, value, hint)),
  );
  elements.resultHighlights.hidden = metrics.length === 0;

  renderOutcome(snapshot);
}

// ── Main render ─────────────────────────────────────────────────────

function renderDetails(snapshot) {
  state.snapshot = snapshot;
  const artifacts = (snapshot?.artifacts || []).filter((artifact) => artifact.kind !== "FactCheckReport");
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
      kind.textContent = artifactDisplayLabel(artifact);
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
  const showContinuePanel = Boolean(mode);
  elements.continuePanel.hidden = !showContinuePanel;
  elements.undoDecision.hidden = !canUndoDecision;
  elements.continueResearch.hidden = !showContinuePanel;
  if (mode === "screening") {
    elements.continueEyebrow.textContent = "Screening complete";
    elements.continueTitle.textContent = "候选集已经确认";
    elements.continueText.textContent = "继续后将读取论文、提取证据并完成综述。";
    elements.continueButtonLabel.textContent = "继续研究";
  } else if (mode === "retry") {
    elements.continueEyebrow.textContent = "运行中断";
    elements.continueTitle.textContent = "从已保存进度重试";
    elements.continueText.textContent = "系统会从当前项目阶段重新执行；已经提交的研究产物将继续保留。";
    elements.continueButtonLabel.textContent = "重新运行";
  } else if (mode) {
    const operationalRecovery = recoverableOperationalFailure(snapshot);
    const savedDraftCount = currentSectionDrafts(snapshot).length;
    elements.continueEyebrow.textContent =
      mode === "recovery" ? "成果恢复" : "继续写作";
    elements.continueTitle.textContent =
      operationalRecovery ? "恢复综述整合" : mode === "recovery" ? "补全最终综述" : "继续综述写作";
    elements.continueText.textContent =
      operationalRecovery
        ? `系统将复用已保存的 ${savedDraftCount} 个章节草稿，从整合阶段继续，不会重新检索或重写章节。`
        : "系统将复用已保存的论文卡片和研究结果，从当前写作阶段继续，不会重新检索。";
    elements.continueButtonLabel.textContent = "继续生成综述";
  } else if (canUndoDecision) {
    const stopped = snapshot?.project?.stage === "INCONCLUSIVE";
    elements.continueEyebrow.textContent = "可撤销操作";
    elements.continueTitle.textContent = stopped ? "项目已按审核意见停止" : "候选集已经确认";
    elements.continueText.textContent = "在后续研究尚未开始前，可以恢复到上一版候选集。";
  }
  if (mode && !state.agentAvailable) {
    elements.continueResearch.disabled = true;
    elements.continueResearch.title = "研究助手当前不可用，请检查模型配置";
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
  const latestRun = snapshot?.runs?.[0] || null;
  const activeRun = snapshot?.active_run || null;
  const runtimeEvents = snapshot?.runtime_events || [];
  const latestRuntimeEvent = runtimeEvents.at(-1);
  return [
    snapshot?.project?.updated_at || "",
    snapshot?.project?.stage || "",
    artifacts.length,
    latestArtifact?.artifact_id || "",
    events.length,
    latestEvent?.event_id || "",
    activeRun?.run_id || latestRun?.run_id || "",
    activeRun?.status || latestRun?.status || "",
    activeRun?.updated_at || latestRun?.updated_at || "",
    runtimeEvents.length,
    latestRuntimeEvent?.timestamp || "",
    latestRuntimeEvent?.type || "",
  ].join(":");
}

function activeSnapshotRun(snapshot) {
  if (snapshot?.project?.stage === "SEARCH_REVIEW_PENDING") return null;
  const run = snapshot?.active_run;
  return run && ["queued", "running"].includes(run.status) ? run : null;
}

function latestFailedRun(snapshot) {
  const latestRun = snapshot?.runs?.[0] || null;
  return latestRun?.status === "failed" ? latestRun : null;
}

function displayRunMessage(message, phaseName) {
  if (!message || message === "任务已排队") {
    return RUN_PHASES[phaseName]?.detail || "研究任务正在执行。";
  }
  return message;
}

function conversationIdForSnapshot(snapshot = state.snapshot) {
  return snapshot?.conversation?.conversation_id
    || snapshot?.project?.conversation_id
    || state.conversation?.conversation_id
    || null;
}

function applyProjectSnapshot(snapshot, { keepRunPanel = false, renderInspector = true } = {}) {
  state.snapshot = snapshot;
  rebuildCitationIndex(snapshot);
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
    const stagePhase = STAGE_RUN_PHASES[snapshot.project?.stage] || "thinking";
    const activePhase = activeRun.kind === "continue"
      ? stagePhase
      : activeRun.phase || stagePhase;
    const isNewRun = activeRun.run_id !== state.activeRunId;
    state.activeRun = activeRun;
    state.activeRunId = activeRun.run_id;
    elements.continuePanel.hidden = true;
    if (isNewRun || !state.runStartedAt) {
      beginRunSession({
        stage: snapshot.project?.stage || "CREATED",
        phase: activePhase,
        message: displayRunMessage(activeRun.message, activePhase),
        snapshot,
      });
    } else {
      setRunPhase(
        activePhase,
        displayRunMessage(activeRun.message, activePhase),
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
  elements.currentTask.hidden = continuationMode(snapshot) === "screening";
  elements.runPanel.hidden = !keepRunPanel;
}

async function loadProject(projectId, quiet = false, force = false) {
  if (!projectId) return;
  const loadSession = ++state.projectLoadSession;
  state.projectLoadController?.abort();
  state.projectLoadController = null;
  if (projectId !== state.projectId) {
    stopRunTimers();
    state.runSessionId += 1;
    state.runStartedAt = null;
    state.activeRun = null;
    state.activeRunId = null;
    setBusy(false);
  }
  const cachedSnapshot = !force ? state.completedProjectCache.get(projectId) : null;
  if (cachedSnapshot) {
    indexProjectLibrary(cachedSnapshot.project_library || []);
    applyProjectSnapshot(cachedSnapshot);
    elements.projectIdInput.value = projectId;
    return;
  }
  const loadController = new AbortController();
  state.projectLoadController = loadController;
  try {
    const payload = await api(
      `/api/projects/${encodeURIComponent(projectId)}`,
      { signal: loadController.signal },
    );
    if (loadSession !== state.projectLoadSession) return;
    const snapshot = payload.data;
    if (Array.isArray(snapshot.project_library)) {
      indexProjectLibrary(snapshot.project_library);
    } else {
      await loadProjectLibrary(projectId, loadController.signal);
    }
    if (loadSession !== state.projectLoadSession) return;
    applyProjectSnapshot(snapshot);
    if (snapshot.project?.stage === "COMPLETED") {
      state.completedProjectCache.set(projectId, snapshot);
    }
    const hasCandidateSnapshot = (snapshot.artifacts || []).some(
      (artifact) => artifact.kind === "CandidateSetSnapshot",
    );
    if (
      ["SEARCH_REVIEW_PENDING", "SCREENED", "INCONCLUSIVE"].includes(snapshot.project.stage)
      || (snapshot.project.stage === "SEARCHED" && hasCandidateSnapshot)
    ) {
      const reviewPayload = await api(
        `/api/projects/${encodeURIComponent(projectId)}/search-review`,
        { signal: loadController.signal },
      );
      if (loadSession !== state.projectLoadSession) return;
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
    if (loadSession !== state.projectLoadSession) return;
    if (error.name === "AbortError") return;
    notify(`无法打开项目：${error.message}`, true);
  } finally {
    if (state.projectLoadController === loadController) {
      state.projectLoadController = null;
    }
  }
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
  elements.candidateGrid.replaceChildren();
  const visible = state.candidates;

  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = state.reviewQuery ? "没有匹配的候选论文" : "当前候选集为空";
    elements.candidateGrid.append(empty);
  }

  visible.forEach((candidate) => {
    const id = candidateId(candidate);
    const snapshot = state.review?.candidate_set;
    const agentDecision = candidate.agent_decision || agentDecisionFor(snapshot, id);
    const agentReason = candidate.agent_reason || snapshot?.agent_screening_reasons?.[id];
    const selected = candidate.selected ?? state.selectedIds.has(id);
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
    checkbox.addEventListener("change", async () => {
      const nextSelected = checkbox.checked;
      checkbox.disabled = true;
      candidate.selected = nextSelected;
      card.classList.toggle("is-excluded", !nextSelected);
      if (nextSelected) state.selectedIds.add(id);
      else state.selectedIds.delete(id);
      if (state.manualCandidates.has(id)) {
        state.reviewSelectionDirty = true;
        updateReviewStats();
        checkbox.disabled = false;
        return;
      }
      updateReviewStats(nextSelected ? 1 : -1);
      try {
        await persistReviewSelection([id], nextSelected);
      } catch (error) {
        candidate.selected = !nextSelected;
        checkbox.checked = !nextSelected;
        card.classList.toggle("is-excluded", nextSelected);
        if (nextSelected) state.selectedIds.delete(id);
        else state.selectedIds.add(id);
        updateReviewStats(nextSelected ? -1 : 1);
        notify(`选择状态保存失败：${error.message}`, true);
      } finally {
        checkbox.disabled = false;
      }
    });
    const title = document.createElement("h4");
    title.className = "candidate-title";
    title.textContent = candidate.title || "未命名论文";

    const meta = document.createElement("div");
    meta.className = "candidate-meta";
    const source = document.createElement("span");
    source.className = "candidate-source";
    source.textContent = candidate.source === "library"
      ? "文献库"
      : candidate.source || "未知来源";
    const year = document.createElement("span");
    year.className = "candidate-year";
    year.textContent = candidate.year ? `${candidate.year} 年` : "年份未知";
    meta.append(source, year);
    if (agentDecision) {
      const badge = document.createElement("span");
      badge.className = `candidate-decision is-${agentDecision}`;
      const decisionLabel = agentDecision === "include"
        ? "建议保留"
        : agentDecision === "exclude" ? "建议排除" : "需要判断";
      badge.textContent = decisionLabel;
      meta.append(badge);
    }
    const heading = document.createElement("div");
    heading.className = "candidate-heading";
    heading.append(title, meta);
    head.append(checkbox, heading);

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
    const authorList = candidate.authors || [];
    authors.textContent = authorList.length
      ? authorList.length > 5
        ? `${authorList.slice(0, 5).join("、")} 等 ${authorList.length} 位作者`
        : authorList.join("、")
      : "作者信息暂缺";
    if (authorList.length > 5) authors.title = authorList.join("、");
    const authorsRow = document.createElement("div");
    authorsRow.className = "candidate-detail-row";
    const authorsLabel = document.createElement("span");
    authorsLabel.textContent = "作者";
    authorsRow.append(authorsLabel, authors);

    const ranking = document.createElement("div");
    ranking.className = "candidate-ranking";
    const addRankingItem = (label, value, title = "") => {
      if (value == null || !Number.isFinite(Number(value))) return;
      const item = document.createElement("span");
      item.className = "candidate-ranking-item";
      item.textContent = `${label} ${Math.round(Number(value))}`;
      if (title) item.title = title;
      ranking.append(item);
    };
    addRankingItem("综合", candidate.composite_score, (candidate.ranking_explanation || []).join("；"));
    addRankingItem(
      "影响",
      candidate.impact_score,
      `${(candidate.impact_explanation || []).join("；")}；数据置信度 ${Math.round(Number(candidate.impact_confidence || 0))}`,
    );
    addRankingItem("相关", candidate.relevance_score);
    addRankingItem("场馆", candidate.authority_score, (candidate.authority_explanation || []).join("；"));
    if (candidate.is_retracted) {
      const warning = document.createElement("span");
      warning.className = "candidate-ranking-item is-danger";
      warning.textContent = "撤稿风险";
      ranking.append(warning);
    }

    const reason = document.createElement("div");
    reason.className = "candidate-reason";
    const reasonLabel = document.createElement("strong");
    reasonLabel.textContent = "筛选依据";
    const reasonText = document.createElement("span");
    reasonText.textContent = agentReason || "标题和摘要信息不足，建议人工判断。";
    reason.append(reasonLabel, reasonText);

    const abstract = document.createElement("p");
    abstract.className = "candidate-abstract";
    abstract.textContent = candidate.abstract || "暂无摘要，可在后续精读阶段尝试获取全文。";
    const abstractSection = document.createElement("section");
    abstractSection.className = "candidate-abstract-section";
    const abstractHeader = document.createElement("div");
    abstractHeader.className = "candidate-abstract-header";
    const abstractLabel = document.createElement("strong");
    abstractLabel.textContent = "摘要";
    abstractHeader.append(abstractLabel);
    if ((candidate.abstract || "").length > 320) {
      abstract.classList.add("is-collapsed");
      const toggleAbstract = document.createElement("button");
      toggleAbstract.type = "button";
      toggleAbstract.className = "candidate-abstract-toggle";
      toggleAbstract.textContent = "展开摘要";
      toggleAbstract.setAttribute("aria-expanded", "false");
      toggleAbstract.addEventListener("click", () => {
        const collapsed = abstract.classList.toggle("is-collapsed");
        toggleAbstract.textContent = collapsed ? "展开摘要" : "收起摘要";
        toggleAbstract.setAttribute("aria-expanded", String(!collapsed));
      });
      abstractHeader.append(toggleAbstract);
    }
    abstractSection.append(abstractHeader, abstract);

    const identifiers = document.createElement("div");
    identifiers.className = "candidate-identifiers";
    const code = document.createElement("code");
    code.textContent = candidate.doi ? `DOI ${candidate.doi}` : `标识符 ${id}`;
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

    const body = document.createElement("div");
    body.className = "candidate-body";
    if (ranking.childElementCount) body.append(ranking);
    body.append(authorsRow, venue, reason);
    body.append(abstractSection);
    card.append(head, body, identifiers);
    elements.candidateGrid.append(card);
  });
  refreshIcons();
}

function renderFilteredCandidateCards() {
  const snapshot = state.review?.candidate_set || {};
  const filtered = (snapshot.filtered_candidates || []).filter(
    (candidate) => !state.manualCandidates.has(candidateId(candidate)),
  );
  const filteredTotal = Number(snapshot.filtered_candidate_total ?? filtered.length);
  elements.filteredCandidateCount.textContent = String(filteredTotal);
  elements.filteredCandidatesPanel.hidden = filteredTotal === 0;
  elements.filteredCandidateGrid.replaceChildren();

  filtered.forEach((candidate) => {
    const id = candidateId(candidate);
    const card = document.createElement("article");
    card.className = "filtered-candidate-card";
    const title = document.createElement("h4");
    title.textContent = candidate.title || "未命名论文";
    const reasons = document.createElement("p");
    const reasonList = candidate.agent_reason
      ? [candidate.agent_reason]
      : snapshot.filtered_candidate_reasons?.[id] || [];
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
  renderReviewPagination();
}

function updateReviewStats(delta = 0) {
  const candidateTotal = Number(
    state.review?.candidate_set?.candidate_total
      ?? state.review?.candidate_page?.total
      ?? state.candidates.length,
  );
  elements.candidateCount.textContent = String(candidateTotal);
  if (delta && state.review?.selection) {
    state.review.selection.selected_count = Math.max(
      0,
      Number(state.review.selection.selected_count || 0) + delta,
    );
  }
  const persistedSelected = Number(state.review?.selection?.selected_count || 0);
  const manualSelected = [...state.manualCandidates.keys()].filter((id) => (
    state.selectedIds.has(id)
  )).length;
  const selectedCount = persistedSelected + manualSelected;
  elements.selectedCount.textContent = String(selectedCount);
  const systemLimit = state.review?.candidate_set?.max_papers ?? 8;
  elements.paperCapacity.textContent = `已选 ${selectedCount} 篇 / 系统最多 ${systemLimit} 篇`;
}

function renderReviewPagination() {
  const page = state.review?.candidate_page || { page: 1, total_pages: 1, total: 0 };
  elements.candidatePageStatus.textContent = state.reviewQuery
    ? `第 ${page.page} / ${page.total_pages} 页 · 匹配 ${page.total} 篇`
    : `第 ${page.page} / ${page.total_pages} 页`;
  elements.candidatePrevPage.disabled = page.page <= 1;
  elements.candidateNextPage.disabled = page.page >= page.total_pages;
  elements.candidatePagination.hidden = page.total_pages <= 1 && !state.reviewQuery;

  const filteredPage = state.review?.filtered_candidate_page
    || { page: 1, total_pages: 1 };
  elements.filteredCandidatePageStatus.textContent = `第 ${filteredPage.page} / ${filteredPage.total_pages} 页`;
  elements.filteredCandidatePrevPage.disabled = filteredPage.page <= 1;
  elements.filteredCandidateNextPage.disabled = filteredPage.page >= filteredPage.total_pages;
  elements.filteredCandidatePagination.hidden = filteredPage.total_pages <= 1;
}

async function persistReviewSelection(paperIds, selected) {
  const payload = await api(
    `/api/projects/${encodeURIComponent(state.projectId)}/search-review/selection`,
    {
      method: "PATCH",
      body: JSON.stringify({ paper_ids: paperIds, selected }),
    },
  );
  if (state.review?.selection) {
    state.review.selection.selected_count = payload.data.selected_count;
    state.review.selection.total_count = payload.data.total_count;
  }
  state.reviewSelectionDirty = true;
  updateReviewStats();
  return payload.data;
}

async function setCurrentReviewPageSelection(selected) {
  const paperIds = state.candidates
    .map(candidateId)
    .filter((id) => id && !state.manualCandidates.has(id));
  const manualIds = state.candidates
    .map(candidateId)
    .filter((id) => id && state.manualCandidates.has(id));
  if ((!paperIds.length && !manualIds.length) || state.busy) return;
  setBusy(true);
  try {
    if (paperIds.length) await persistReviewSelection(paperIds, selected);
    state.candidates.forEach((candidate) => {
      candidate.selected = selected;
      const id = candidateId(candidate);
      if (selected) state.selectedIds.add(id);
      else state.selectedIds.delete(id);
    });
    if (manualIds.length) state.reviewSelectionDirty = true;
    renderCandidateCards();
    updateReviewStats();
  } catch (error) {
    notify(`批量更新选择失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function loadReviewPage({ page = state.reviewPage, filteredPage = state.reviewFilteredPage } = {}) {
  if (!state.projectId) return;
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(state.reviewPageSize),
    filtered_page: String(filteredPage),
  });
  if (state.reviewQuery) params.set("q", state.reviewQuery);
  const payload = await api(
    `/api/projects/${encodeURIComponent(state.projectId)}/search-review?${params}`,
  );
  renderReview(payload.data, { preserveManual: true });
}

function normalizeQueryRounds(snapshot) {
  const rounds = Array.isArray(snapshot?.query_rounds) ? snapshot.query_rounds : [];
  const normalized = rounds
    .map((round) => (Array.isArray(round) ? round : []))
    .map((round) => round.map((query) => String(query || "").trim()).filter(Boolean))
    .filter((round) => round.length);
  if (normalized.length) return normalized;
  const executed = Array.isArray(snapshot?.executed_queries) ? snapshot.executed_queries : [];
  const fallback = executed.map((query) => String(query || "").trim()).filter(Boolean);
  return fallback.length ? [fallback] : [];
}

function renderReviewQueryRounds(snapshot) {
  elements.reviewQueryRounds.replaceChildren();
  const rounds = normalizeQueryRounds(snapshot);
  if (!rounds.length) {
    const empty = document.createElement("p");
    empty.className = "review-query-empty";
    empty.textContent = "暂无检索词记录";
    elements.reviewQueryRounds.append(empty);
    return;
  }

  rounds.forEach((queries, index) => {
    const group = document.createElement("div");
    group.className = "review-query-round";
    const label = document.createElement("span");
    label.className = "review-query-round-label";
    label.textContent = `第 ${index + 1} 轮`;
    const list = document.createElement("ul");
    list.className = "review-query-list";
    queries.forEach((query) => {
      const item = document.createElement("li");
      item.textContent = query;
      list.append(item);
    });
    group.append(label, list);
    elements.reviewQueryRounds.append(group);
  });
}

function renderReview(review, { preserveManual = false } = {}) {
  state.review = review;
  if (!preserveManual) {
    state.manualCandidates = new Map();
    state.reviewSelectionDirty = false;
    state.reviewQuery = review.query || "";
    elements.candidateFilter.value = state.reviewQuery;
  }
  state.candidates = review.candidate_set?.candidates || [];
  const snapshot = review.candidate_set || {};
  state.reviewPage = review.candidate_page?.page || 1;
  state.reviewFilteredPage = review.filtered_candidate_page?.page || 1;
  state.reviewPageSize = review.candidate_page?.page_size || state.reviewPageSize;
  elements.candidatePageSize.value = String(state.reviewPageSize);
  state.selectedIds = new Set([
    ...state.candidates.filter((candidate) => candidate.selected).map(candidateId),
    ...[...state.manualCandidates.keys()],
  ]);
  const yearConstraint = snapshot.year_from != null && snapshot.year_to != null
    ? `年份 ${snapshot.year_from}-${snapshot.year_to}`
    : snapshot.year_from != null
      ? `年份 ${snapshot.year_from} 起`
      : snapshot.year_to != null
        ? `年份截至 ${snapshot.year_to}`
        : "历史候选 · 年份范围未记录";
  const roundCount = normalizeQueryRounds(snapshot).length;
  const roundSummary = roundCount
    ? `系统自动检索 ${roundCount} 轮`
    : "系统自动检索轮次未记录";
  elements.reviewConstraints.textContent = [
    roundSummary,
    yearConstraint,
    snapshot.quality_venues_only
      ? "仅 CCF-A、JCR Q1 或 Nature Portfolio"
      : "出版物等级不限",
  ].join(" · ");
  renderReviewQueryRounds(snapshot);
  const shouldShowNotice = Boolean(
    snapshot.blocked_reason || snapshot.search_failures?.length || review.search_failures?.length,
  );
  elements.reviewNotice.hidden = !shouldShowNotice;
  elements.reviewNotice.textContent = shouldShowNotice ? (review.message || "") : "";
  renderProjectHeader(review.project);
  renderProjectSummary({
    project: review.project,
    artifacts: [...(state.snapshot?.artifacts || []), { kind: "CandidateSetSnapshot", payload: snapshot }],
    events: state.snapshot?.events || [],
  });
  elements.reviewPanel.hidden = false;
  elements.currentTask.hidden = true;
  elements.continuePanel.hidden = true;
  elements.undoReview.hidden = !review.can_undo;
  renderCandidateCards();
  renderFilteredCandidateCards();
  updateReviewStats();
}

function feedbackBody(action) {
  const dois = parseList(elements.manualDois.value, true);
  const manualCandidates = [...state.manualCandidates.entries()]
    .filter(([id]) => state.selectedIds.has(id))
    .map(([, candidate]) => candidate);
  const knownManualIds = new Set(
    manualCandidates.flatMap((paper) => [paper.doi, paper.paper_id].filter(Boolean).map(normalizePaperId)),
  );
  return {
    action,
    suggested_queries: action === "refine"
      ? parseList(elements.supplementalQueries.value)
      : [],
    added_papers: [
      ...manualCandidates,
      ...dois
        .filter((doi) => !knownManualIds.has(normalizePaperId(doi)))
        .map((doi) => ({ doi })),
    ],
  };
}

async function submitFeedback(action) {
  if (!state.projectId || state.busy) return;
  const body = feedbackBody(action);
  if (action === "refine") {
    const hasChange =
      body.suggested_queries.length ||
      body.added_papers.length ||
      state.reviewSelectionDirty;
    if (!hasChange) {
      notify("请先填写补充检索词、DOI，或调整候选论文", true);
      return;
    }
  }

  if (action === "accept") {
    const acceptedCount = Number(state.review?.selection?.selected_count || 0)
      + body.added_papers.length;
    const systemLimit = state.review?.candidate_set?.max_papers ?? 8;
    if (!acceptedCount) {
      notify("至少保留或加入一篇论文后才能确认", true);
      return;
    }
    if (acceptedCount > systemLimit) {
      notify(`当前选择 ${acceptedCount} 篇，系统单次最多精读 ${systemLimit} 篇，请缩小候选集`, true);
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
    elements.supplementalQueries.value = "";
    elements.manualDois.value = "";
    if (action === "refine") {
      renderReview(result);
      state.reviewSelectionDirty = false;
      const failures = result.search_failures || [];
      notify(
        failures.length
          ? `候选集已更新，${failures.length} 条记录处理失败，请查看项目产物`
          : "候选集已更新",
        failures.length > 0,
      );
    } else if (action === "accept" && result.ready_to_continue) {
      await loadProjects();
      await loadProject(state.projectId, true, true);
      notify("候选集已确认；你可以先撤销，或点击继续研究开始精读");
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
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function runTimestamp(value, fallback = Date.now()) {
  const timestamp = typeof value === "number" ? value : Date.parse(value || "");
  return Number.isFinite(timestamp) ? timestamp : fallback;
}

function formatActivityTime(timestamp, current = false) {
  if (current) return "进行中";
  const elapsed = formatRunElapsed(
    Math.max(0, runTimestamp(timestamp) - (state.runStartedAt || Date.now())),
  );
  return `完成于 ${elapsed}`;
}

function refreshActivityTimes(now = Date.now()) {
  elements.activityLog.querySelectorAll("li").forEach((item) => {
    const time = item.querySelector(".activity-time");
    if (!time) return;
    const current = item.classList.contains("is-current");
    const timestamp = current ? now : Number(item.dataset.completedAt || now);
    time.textContent = formatActivityTime(timestamp, current);
  });
}

function updateRunClock() {
  if (!state.runStartedAt) return;
  const now = Date.now();
  elements.runElapsed.textContent = `已运行 ${formatRunElapsed(now - state.runStartedAt)}`;
  refreshActivityTimes(now);
}

function setRunPhase(phaseName, detail = "") {
  const phase = RUN_PHASES[phaseName] || RUN_PHASES.thinking;
  const changed = state.runPhase !== phaseName;
  const statusDetail = detail || phase.detail;
  state.runPhase = phaseName;
  elements.runVisualizer.dataset.phase = phaseName;
  elements.runPhaseTitle.textContent = phase.title;
  elements.runStatusText.textContent = statusDetail;
  if (state.runStartedAt) {
    elements.nextActionTitle.textContent = phase.title;
    elements.nextActionText.textContent = statusDetail;
  }
  elements.runPhaseIcon.replaceChildren(iconNode(phase.icon));
  refreshIcons();
  return changed;
}

function markCurrentActivityComplete(completedAt = Date.now()) {
  const current = elements.activityLog.querySelector("li.is-current");
  if (!current) return;
  current.classList.remove("is-current");
  current.classList.add("is-complete");
  current.dataset.completedAt = String(runTimestamp(completedAt));
  const marker = current.querySelector(".activity-marker");
  if (marker) marker.replaceChildren(iconNode("check"));
  refreshActivityTimes();
}

function addActivity(
  message,
  { updateStatus = true, kind = "progress", completedAt = null, details = [] } = {},
) {
  if (!message) return;
  if (message === state.runLastActivity) {
    if (updateStatus) elements.runStatusText.textContent = message;
    return;
  }
  markCurrentActivityComplete(completedAt || Date.now());
  const item = document.createElement("li");
  item.className = kind === "complete" ? "is-complete" : `is-current is-${kind}`;
  if (kind === "complete") {
    item.dataset.completedAt = String(runTimestamp(completedAt));
  }
  const marker = document.createElement("span");
  marker.className = "activity-marker";
  marker.append(iconNode(
    kind === "complete" ? "check" : kind === "error" ? "circle-alert" : "circle",
  ));
  const text = document.createElement("span");
  text.className = "activity-text";
  text.textContent = message;
  const copy = document.createElement("span");
  copy.className = "activity-copy";
  copy.append(text);
  if (details.length) {
    const detailList = document.createElement("span");
    detailList.className = "activity-details";
    details.filter(Boolean).forEach((detail) => {
      const line = document.createElement("span");
      line.textContent = detail;
      detailList.append(line);
    });
    copy.append(detailList);
  }
  const time = document.createElement("time");
  time.className = "activity-time";
  time.textContent = formatActivityTime(
    kind === "complete" ? item.dataset.completedAt : Date.now(),
    kind !== "complete",
  );
  item.append(marker, copy, time);
  elements.activityLog.append(item);
  while (elements.activityLog.children.length > 14) {
    elements.activityLog.firstElementChild?.remove();
  }
  elements.activityLog.scrollTop = elements.activityLog.scrollHeight;
  state.runLastActivity = message;
  if (updateStatus) elements.runStatusText.textContent = message;
  refreshIcons();
}

function runtimeEventIdentity(event, index = 0) {
  return `${event?.timestamp || index}:${event?.type || "runtime"}:${event?.message || ""}`;
}

function sourceRoundSummary(statuses = []) {
  const summary = new Map();
  statuses.forEach((status) => {
    const source = status?.source;
    if (!source) return;
    const current = summary.get(source) || { count: 0, failed: false };
    if (status.ok === false) current.failed = true;
    else current.count += Number(status.count || 0);
    summary.set(source, current);
  });
  return [...summary.entries()].map(([source, value]) => (
    value.failed && value.count === 0
      ? `${source}：本轮跳过`
      : `${source}：${value.count} 篇${value.failed ? "（部分查询跳过）" : ""}`
  ));
}

function appendRuntimeEvent(event, index = 0) {
  const identity = runtimeEventIdentity(event, index);
  if (state.runKnownRuntimeEvents.has(identity)) return;
  const data = event?.data || {};
  if (data.scope !== "portfolio") return;

  let activity = null;
  if (event.type === "search.started") {
    activity = {
      message: `第 ${data.round || 1} 轮检索`,
      details: [
        data.queries?.length ? `检索词：${data.queries.join("；")}` : "",
        data.sources?.length ? `来源：${data.sources.join("、")}` : "",
      ],
      kind: "progress",
    };
  } else if (event.type === "search.results") {
    activity = {
      message: `第 ${data.round || 1} 轮检索完成：去重后 ${data.count || 0} 篇`,
      details: sourceRoundSummary(data.source_status),
      kind: "complete",
    };
  } else if (event.type === "search.synthesizing") {
    activity = {
      message: `第 ${data.round || 1} 轮综合分析`,
      details: [`正在比较 ${data.candidate_count || 0} 篇候选论文并检查覆盖盲区`],
      kind: "progress",
    };
  } else if (event.type === "search.summary") {
    activity = {
      message: `检索综合完成：${data.rounds || 0} 轮，共 ${data.candidate_count || 0} 篇候选论文`,
      details: [
        data.search_terms?.length ? `综合检索词：${data.search_terms.join("；")}` : "",
        data.coverage_gaps?.length ? `仍需关注：${data.coverage_gaps.join("；")}` : "覆盖分析已完成",
      ],
      kind: "complete",
    };
  }
  if (!activity) return;

  state.runKnownRuntimeEvents.add(identity);
  setRunPhase("searching", event.message || RUN_PHASES.searching.detail);
  addActivity(activity.message, {
    updateStatus: false,
    kind: activity.kind,
    completedAt: event.timestamp,
    details: activity.details,
  });
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
  state.runStartedAt = runTimestamp(snapshot?.project?.created_at);
  state.runKnownEvents = new Set(
    (snapshot?.events || []).map((event, index) => eventIdentity(event, index)),
  );
  state.runKnownArtifacts = new Set(
    (snapshot?.artifacts || []).map((artifact, index) => artifactIdentity(artifact, index)),
  );
  state.runKnownRuntimeEvents = new Set();
  state.runSnapshotSignature = snapshot ? snapshotSignature(snapshot) : "";
  state.runLastActivity = "";
  elements.activityLog.replaceChildren();
  elements.currentTask.hidden = true;
  elements.runPanel.hidden = false;
  elements.runElapsed.textContent = `已运行 ${formatRunElapsed(Date.now() - state.runStartedAt)}`;
  orderedWorkflowEvents(snapshot?.events || []).forEach((event) => {
    addActivity(transitionActivity(event), {
      updateStatus: false,
      kind: "complete",
      completedAt: event.created_at,
    });
  });
  const activePhase = phase || STAGE_RUN_PHASES[stage] || "thinking";
  setRunPhase(activePhase, message);
  (snapshot?.runtime_events || []).forEach(appendRuntimeEvent);
  state.runClockTimer = window.setInterval(updateRunClock, 1000);
}

function finishRunSession() {
  stopRunTimers();
  state.runSessionId += 1;
  markCurrentActivityComplete();
  refreshIcons();
  state.runStartedAt = null;
  elements.currentTask.hidden = false;
}

function transitionActivity(event) {
  const labels = {
    SEARCHED: "初步文献检索已完成",
    SEARCH_REVIEW_PENDING: "候选论文已提交人工审核",
    SCREENED: "候选论文已确认",
    EXTRACTED: "论文精读已完成",
    SYNTHESIZED: "研究结果初稿已整理",
    REVIEW_PENDING: "研究结果正在进行完整性检查",
    OUTLINED: "综述提纲已生成",
    NARRATED: "综述正文已生成",
    COMPLETED: "最终综述已生成，研究已结束",
    INCONCLUSIVE: "研究因证据不足停止",
  };
  if (event?.to_stage === "REVIEWED") {
    return event.review_verdict === "REVISE"
      ? "证据审查完成：需要修订"
      : event.review_verdict === "PASS"
        ? "证据审查完成：已通过"
        : "证据审查已完成";
  }
  return labels[event?.to_stage]
    || `${STAGE_LABELS[event?.from_stage] || event?.from_stage}进入${STAGE_LABELS[event?.to_stage] || event?.to_stage}`;
}

function artifactActivity(artifact) {
  const payload = artifact?.payload || {};
  if (artifact?.kind === "PaperCard") {
    return payload.title ? `论文精读完成：${payload.title}` : "一篇论文已完成精读";
  }
  if (artifact?.kind === "SectionDraft") {
    const title = artifactDisplayLabel(artifact);
    return title ? `章节草稿已生成：${title}` : "一个章节草稿已生成";
  }
  return "";
}

function syncRunningSnapshot(snapshot) {
  const signature = snapshotSignature(snapshot);
  const runFinished = Boolean(state.activeRunId && !activeSnapshotRun(snapshot));
  if (signature === state.runSnapshotSignature && !runFinished) return false;

  (snapshot?.artifacts || []).forEach((artifact, index) => {
    const identity = artifactIdentity(artifact, index);
    if (state.runKnownArtifacts.has(identity)) return;
    state.runKnownArtifacts.add(identity);
    const message = artifactActivity(artifact);
    if (message) {
      addActivity(message, {
        updateStatus: false,
        kind: "complete",
        completedAt: artifact.created_at,
      });
    }
  });

  orderedWorkflowEvents(snapshot?.events || []).forEach((event, index) => {
    const identity = eventIdentity(event, index);
    if (state.runKnownEvents.has(identity)) return;
    state.runKnownEvents.add(identity);
    addActivity(transitionActivity(event), {
      updateStatus: false,
      kind: "complete",
      completedAt: event.created_at,
    });
  });

  state.runSnapshotSignature = signature;
  applyProjectSnapshot(snapshot, {
    keepRunPanel: true,
    renderInspector: state.inspectorOpen,
  });
  if (!state.busy) {
    elements.runPanel.hidden = true;
    return runFinished;
  }
  const stage = snapshot?.project?.stage;
  setRunPhase(STAGE_RUN_PHASES[stage] || "thinking");
  (snapshot?.runtime_events || []).forEach(appendRuntimeEvent);
  const latestPortfolioEvent = [...(snapshot?.runtime_events || [])]
    .reverse()
    .find((event) => event?.data?.scope === "portfolio");
  if (latestPortfolioEvent && stage === "CREATED") {
    setRunPhase("searching", latestPortfolioEvent.message);
  }
  return runFinished;
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
    const projectId = state.projectId;
    const runFinished = syncRunningSnapshot(payload.data);
    if (runFinished && projectId === state.projectId) {
      await loadProject(projectId, true, true);
      await loadProjects();
      const failedRun = latestFailedRun(payload.data);
      if (failedRun) {
        notify(`研究执行失败：${failedRun.error || failedRun.message || "未知错误"}`, true);
      } else {
        notify("本阶段已经完成，界面已自动更新");
      }
    }
  } catch {
    // The main request owns error reporting; polling remains best-effort.
  } finally {
    state.runPollInFlight = false;
  }
}

async function openPendingSearchReview(projectId) {
  if (!projectId || projectId !== state.projectId) return;
  try {
    const reviewPayload = await api(
      `/api/projects/${encodeURIComponent(projectId)}/search-review`,
    );
    if (projectId !== state.projectId) return;
    renderReview(reviewPayload.data);
    elements.currentTask.hidden = true;
    elements.runPanel.hidden = true;
    notify("检索已完成，请审核候选论文");
  } catch (error) {
    notify(`候选论文已生成，但审核界面加载失败：${error.message}`, true);
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
  if (eventName === "done") return "本轮研究任务执行结束";
  if (eventName === "fallback") return "模型不可用，已进入降级流程";
  if (eventName === "error") return payload?.message || "研究助手执行失败";
  const labels = {
    thinking: "正在分析当前材料并规划下一步",
    searching: "正在扩展检索词并查找候选论文",
    reading: "正在读取论文并提取结构化信息",
    synthesizing: "正在比较并整理各论文的研究发现",
    reviewing: "正在检查研究结果是否完整",
    outlining: "正在规划综述章节与论证顺序",
    writing: "正在整合章节正文与参考文献",
  };
  return labels[phaseName] || "正在执行研究任务";
}

async function handleStreamEvent(eventName, payload) {
  const project = findProject(payload);
  let phaseName = streamPhase(eventName, payload);
  if (project) {
    state.projectId = project.project_id;
    const projectStartedAt = runTimestamp(project.created_at, state.runStartedAt || Date.now());
    if (!state.runStartedAt || projectStartedAt < state.runStartedAt) {
      state.runStartedAt = projectStartedAt;
      updateRunClock();
    }
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
    throw new Error(payload?.message || "研究助手流式执行失败");
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
    notify("本轮研究任务执行结束");
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
    notify("研究助手当前不可用，请先检查模型配置", true);
    return;
  }
  const mode = continuationMode(state.snapshot);
  const confirmation = mode === "screening"
    ? "继续后将开始逐篇读取论文，这可能需要几分钟。确认开始吗？"
    : mode === "retry"
      ? "将从当前已保存阶段重新运行研究任务。确认开始吗？"
    : recoverableOperationalFailure(state.snapshot)
      ? "将从已保存的章节草稿恢复综述整合，不会重新检索、重读论文或重写章节。确认开始吗？"
      : "将复用已保存的研究结果继续生成综述，不会重新检索或重读论文。确认开始吗？";
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

async function continueResearch(options = {}) {
  const {
    skipConfirm = false,
    allowBusy = false,
    startMessage = "正在从已保存进度恢复研究",
    successMessage = "后续研究已在后台启动；可以切换到其他对话",
  } = options;
  if (!state.projectId || (state.busy && !allowBusy)) return false;
  if (!state.agentAvailable) {
    notify("研究助手当前不可用，请先检查模型配置", true);
    return false;
  }
  const conversationId = conversationIdForSnapshot();
  if (!conversationId) {
    notify("旧项目没有独立对话记录，请重新创建一个研究对话", true);
    return false;
  }
  const mode = continuationMode(state.snapshot);
  const confirmation = mode === "screening"
    ? "继续后将开始逐篇读取论文，这可能需要几分钟。确认开始吗？"
    : mode === "retry"
      ? "将从当前已保存阶段重新运行研究任务。确认开始吗？"
    : recoverableOperationalFailure(state.snapshot)
      ? "将从已保存的章节草稿恢复综述整合，不会重新检索、重读论文或重写章节。确认开始吗？"
      : "将复用已保存的研究结果继续生成综述，不会重新检索或重读论文。确认开始吗？";
  if (!skipConfirm && !window.confirm(confirmation)) return false;

  setBusy(true);
  beginRunSession({
    stage: state.snapshot?.project?.stage || "SCREENED",
    phase: STAGE_RUN_PHASES[state.snapshot?.project?.stage] || "thinking",
    message: startMessage,
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
    notify(successMessage);
    return true;
  } catch (error) {
    if (error.message === "conversation_already_running") {
      await loadProject(state.projectId, true, true);
      notify("研究任务已在后台运行");
      return true;
    }
    if (state.runStartedAt) finishRunSession();
    elements.runPanel.hidden = true;
    setBusy(false);
    notify(`继续执行失败：${error.message}`, true);
    return false;
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
elements.paperPdfPages.addEventListener("scroll", () => {
  syncPaperHorizontalScroll(elements.paperPdfPages, elements.paperHorizontalScroller);
});
elements.paperHorizontalScroller.addEventListener("scroll", () => {
  syncPaperHorizontalScroll(elements.paperHorizontalScroller, elements.paperPdfPages);
});
window.addEventListener("resize", () => window.requestAnimationFrame(updatePaperHorizontalScroller));
window.addEventListener("scroll", () => {
  if (elements.paperWorkspaceView.hidden) return;
  const page = nearestVisiblePaperPage();
  if (page) queuePaperReadingProgress(page);
}, { passive: true });
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
    clearPaperSelection();
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
    clearPaperSelection();
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
elements.emptyReadPaper.addEventListener("click", async () => {
  await openLibrary();
  notify("请选择一篇论文，点击“打开论文研读工作台”开始精读");
});
elements.emptyOpenLibrary.addEventListener("click", openLibrary);
elements.projectSearch.addEventListener("input", renderProjectList);
elements.cancelNewProject.addEventListener("click", () => toggleNewProject(false));
elements.cancelNewProjectSecondary.addEventListener("click", () => toggleNewProject(false));
elements.newProjectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const topic = byId("topicInput").value.trim();
  const question = byId("questionInput").value.trim();
  if (!topic || !question) return;
  const reviewLimits = {
    max_search_rounds: numberInputValue(elements.initialMaxSearchRounds, 3),
    year_from: numberInputValue(elements.initialYearFrom, 2024),
    year_to: numberInputValue(elements.initialYearTo, 2026),
    quality_venues_only: elements.initialQualityVenuesOnly.checked,
    prefer_library_search: elements.initialPreferLibrarySearch.checked,
  };
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
elements.toggleProjectSelection.addEventListener("click", () => {
  setProjectSelectionMode(!state.projectSelectionMode);
});
elements.selectAllProjects.addEventListener("click", toggleVisibleProjectSelection);
elements.cancelProjectSelection.addEventListener("click", () => setProjectSelectionMode(false));
elements.deleteSelectedProjects.addEventListener("click", deleteSelectedProjectRecords);
elements.projectLookupForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const projectId = elements.projectIdInput.value.trim();
  if (!projectId) return;
  closeMenus();
  loadProject(projectId);
});
elements.reloadProject.addEventListener("click", () => loadProject(state.projectId, false, true));
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
elements.candidateFilter.addEventListener("input", () => {
  window.clearTimeout(state.reviewSearchTimer);
  state.reviewSearchTimer = window.setTimeout(async () => {
    state.reviewQuery = elements.candidateFilter.value.trim();
    try {
      await loadReviewPage({ page: 1 });
    } catch (error) {
      notify(`候选论文搜索失败：${error.message}`, true);
    }
  }, 220);
});
elements.selectAll.addEventListener("click", () => setCurrentReviewPageSelection(true));
elements.clearAll.addEventListener("click", () => setCurrentReviewPageSelection(false));
elements.candidatePrevPage.addEventListener("click", async () => {
  try {
    await loadReviewPage({ page: Math.max(1, state.reviewPage - 1) });
  } catch (error) {
    notify(`上一页加载失败：${error.message}`, true);
  }
});
elements.candidateNextPage.addEventListener("click", async () => {
  try {
    await loadReviewPage({ page: state.reviewPage + 1 });
  } catch (error) {
    notify(`下一页加载失败：${error.message}`, true);
  }
});
elements.candidatePageSize.addEventListener("change", async () => {
  state.reviewPageSize = Number(elements.candidatePageSize.value || 20);
  try {
    await loadReviewPage({ page: 1, filteredPage: 1 });
  } catch (error) {
    notify(`分页设置更新失败：${error.message}`, true);
  }
});
elements.filteredCandidatePrevPage.addEventListener("click", async () => {
  try {
    await loadReviewPage({ filteredPage: Math.max(1, state.reviewFilteredPage - 1) });
  } catch (error) {
    notify(`上一页加载失败：${error.message}`, true);
  }
});
elements.filteredCandidateNextPage.addEventListener("click", async () => {
  try {
    await loadReviewPage({ filteredPage: state.reviewFilteredPage + 1 });
  } catch (error) {
    notify(`下一页加载失败：${error.message}`, true);
  }
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
elements.recentHistoryToggle.addEventListener("click", (event) => {
  event.stopPropagation();
  if (elements.appShell.dataset.sidebar !== "collapsed") return;
  const popover = document.getElementById("recentHistoryPopover");
  const shouldOpen = !popover || popover.hidden;
  closeMenus();
  if (shouldOpen) openRecentHistoryPopover();
});
elements.brandHome.addEventListener("click", clearProjectView);
elements.homeToggle.addEventListener("click", clearProjectView);
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
  if (!event.target.closest(".project-list-entry")) closeConversationMenus();
  const recentPopover = document.getElementById("recentHistoryPopover");
  if (
    recentPopover
    && !recentPopover.hidden
    && !recentPopover.contains(event.target)
    && !elements.recentHistoryToggle.contains(event.target)
  ) {
    closeRecentHistoryPopover();
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
  const recentPopover = document.getElementById("recentHistoryPopover");
  if (recentPopover && !recentPopover.hidden) {
    event.preventDefault();
    closeRecentHistoryPopover({ restoreFocus: true });
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
  showWorkspace("empty");
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
  if (document.visibilityState === "visible") void loadProjects({ preserveOrder: true });
}, 4000);
