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
  ["COMPLETED", "完成"],
];

const STAGE_LABELS = Object.fromEntries(STAGES);
STAGE_LABELS.INCONCLUSIVE = "证据不足";

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
};

const byId = (id) => document.getElementById(id);

const elements = {
  healthBadge: byId("healthBadge"),
  newProjectToggle: byId("newProjectToggle"),
  newProjectForm: byId("newProjectForm"),
  cancelNewProject: byId("cancelNewProject"),
  emptyNewProject: byId("emptyNewProject"),
  projectList: byId("projectList"),
  refreshProjects: byId("refreshProjects"),
  projectLookupForm: byId("projectLookupForm"),
  projectIdInput: byId("projectIdInput"),
  emptyState: byId("emptyState"),
  projectView: byId("projectView"),
  stageBadge: byId("stageBadge"),
  projectIdLabel: byId("projectIdLabel"),
  projectTopic: byId("projectTopic"),
  projectQuestion: byId("projectQuestion"),
  copyProjectId: byId("copyProjectId"),
  reloadProject: byId("reloadProject"),
  stageStepper: byId("stageStepper"),
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
  querySuggestions: byId("querySuggestions"),
  manualDois: byId("manualDois"),
  feedbackComment: byId("feedbackComment"),
  refineReview: byId("refineReview"),
  acceptReview: byId("acceptReview"),
  stopReview: byId("stopReview"),
  continuePanel: byId("continuePanel"),
  continueResearch: byId("continueResearch"),
  projectDetails: byId("projectDetails"),
  artifactSummary: byId("artifactSummary"),
  eventTimeline: byId("eventTimeline"),
  artifactList: byId("artifactList"),
  toast: byId("toast"),
};

function candidateId(candidate) {
  return candidate.paper_id || candidate.doi || `title:${candidate.title || ""}`;
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
  ].forEach((button) => {
    button.disabled = busy;
  });
  elements.continueResearch.disabled =
    busy || (state.project?.stage === "SCREENED" && !state.agentAvailable);
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

function renderProjectList() {
  elements.projectList.replaceChildren();
  if (!state.projects.length) {
    const empty = document.createElement("p");
    empty.className = "muted small";
    empty.textContent = "还没有项目记录";
    elements.projectList.append(empty);
    return;
  }

  state.projects.forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-list-item";
    button.classList.toggle("is-active", project.project_id === state.projectId);
    button.setAttribute("aria-current", project.project_id === state.projectId ? "page" : "false");

    const title = document.createElement("span");
    title.className = "project-list-title";
    title.textContent = project.topic || "未命名研究";

    const meta = document.createElement("span");
    meta.className = "project-list-meta";
    const stage = document.createElement("span");
    stage.textContent = STAGE_LABELS[project.stage] || project.stage;
    const date = document.createElement("span");
    date.textContent = formatDate(project.updated_at);
    meta.append(stage, date);

    button.append(title, meta);
    button.addEventListener("click", () => loadProject(project.project_id));
    elements.projectList.append(button);
  });
}

async function loadProjects() {
  try {
    const payload = await api("/api/projects?limit=30");
    state.projects = payload.data || [];
    renderProjectList();
  } catch (error) {
    elements.projectList.textContent = `项目载入失败：${error.message}`;
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
  elements.emptyState.hidden = true;
  elements.projectView.hidden = false;
  elements.projectIdLabel.textContent = project.project_id;
  elements.projectTopic.textContent = project.topic || "未命名研究";
  elements.projectQuestion.textContent = project.research_question || "";
  elements.stageBadge.textContent = project.stage;
  elements.stageBadge.className = "stage-badge";
  if (["COMPLETED", "REVIEWED"].includes(project.stage)) {
    elements.stageBadge.classList.add("is-done");
  }
  if (project.stage === "INCONCLUSIVE") {
    elements.stageBadge.classList.add("is-terminal");
  }
  renderStepper(project.stage);
  renderProjectList();
}

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
    chip.textContent = `${kind} × ${count}`;
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
      transition.textContent = `${event.from_stage} → ${event.to_stage}`;
      const meta = document.createElement("span");
      meta.textContent = `${event.actor} · ${formatDate(event.created_at)}`;
      item.append(transition, meta);
      elements.eventTimeline.append(item);
    });
  }

  elements.artifactList.replaceChildren();
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
      kind.textContent = artifact.kind;
      const time = document.createElement("span");
      time.className = "muted";
      time.textContent = formatDate(artifact.created_at);
      summary.append(kind, time);
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(artifact.payload, null, 2);
      details.append(summary, pre);
      elements.artifactList.append(details);
    });
  }
}

function renderStagePanels(project) {
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = project.stage !== "SCREENED";
  if (project.stage === "SCREENED" && !state.agentAvailable) {
    elements.continueResearch.disabled = true;
    elements.continueResearch.title = "Agent 当前不可用，请检查模型配置";
  } else {
    elements.continueResearch.title = "";
  }
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
    renderProjectHeader(snapshot.project);
    renderStagePanels(snapshot.project);
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
    checkbox.setAttribute("aria-label", `${selected ? "排除" : "保留"}：${candidate.title}`);
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

    const authors = document.createElement("p");
    authors.className = "candidate-authors";
    authors.textContent = (candidate.authors || []).length
      ? candidate.authors.join("、")
      : "作者信息暂缺";

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
      link.textContent = "查看原文 ↗";
      identifiers.append(link);
    }

    card.append(head, meta, authors, abstract, identifiers);
    elements.candidateGrid.append(card);
  });
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
  state.selectedIds = new Set(state.candidates.map(candidateId));
  renderProjectHeader(review.project);
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
  };
}

async function submitFeedback(action) {
  if (!state.projectId || state.busy) return;
  const body = feedbackBody(action);
  if (body.suggested_queries.length > 3) {
    notify("每轮最多提交 3 条补充检索词", true);
    return;
  }
  if (action === "refine") {
    const hasChange =
      body.suggested_queries.length ||
      body.added_papers.length ||
      body.excluded_paper_ids.length ||
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
    if (!state.selectedIds.size && !body.added_papers.length) {
      notify("至少保留或加入一篇论文后才能确认", true);
      return;
    }
    if (
      !window.confirm(
        `确认保留 ${state.selectedIds.size + body.added_papers.length} 篇论文并结束人工审核？`,
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
  }
  if (eventName === "awaiting_input" && payload?.data) {
    renderReview(payload.data);
    await loadProjects();
  }
  if (eventName === "error") {
    throw new Error(payload?.message || "Agent 流式执行失败");
  }
}

async function startResearch(topic, question) {
  state.projectId = null;
  state.snapshot = null;
  state.review = null;
  elements.emptyState.hidden = true;
  elements.projectView.hidden = false;
  elements.reviewPanel.hidden = true;
  elements.continuePanel.hidden = true;
  elements.projectDetails.hidden = true;
  elements.runPanel.hidden = false;
  elements.activityLog.replaceChildren();
  renderProjectHeader({
    project_id: "正在创建项目…",
    topic,
    research_question: question,
    stage: "CREATED",
  });
  addActivity("正在创建项目并准备检索");
  setBusy(true);

  try {
    const response = await fetch("/api/research/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, research_question: question }),
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
  if (!window.confirm("继续后将开始逐篇读取论文，这可能需要几分钟。确认开始吗？")) {
    return;
  }
  setBusy(true);
  elements.runPanel.hidden = false;
  elements.activityLog.replaceChildren();
  addActivity("正在恢复项目并启动论文精读");
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
  elements.newProjectForm.hidden = !show;
  if (show) byId("topicInput").focus();
}

elements.newProjectToggle.addEventListener("click", () => {
  toggleNewProject(elements.newProjectForm.hidden);
});
elements.emptyNewProject.addEventListener("click", () => toggleNewProject(true));
elements.cancelNewProject.addEventListener("click", () => toggleNewProject(false));
elements.newProjectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const topic = byId("topicInput").value.trim();
  const question = byId("questionInput").value.trim();
  if (!topic || !question) return;
  toggleNewProject(false);
  await startResearch(topic, question);
});
elements.refreshProjects.addEventListener("click", loadProjects);
elements.projectLookupForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadProject(elements.projectIdInput.value.trim());
});
elements.reloadProject.addEventListener("click", () => loadProject(state.projectId));
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

async function initialize() {
  await Promise.all([checkHealth(), loadProjects()]);
  const params = new URLSearchParams(window.location.search);
  const requestedProject = params.get("project");
  if (requestedProject) await loadProject(requestedProject, true);
}

initialize();
