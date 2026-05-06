const TEXT = {
  heroEyebrow: "托管 V2 控制台",
  heroTitle: "Chief 驱动交付，租户隔离，审计可追溯。",
  heroBody:
    "当前控制台只保留 V2 架构。项目会经过需求契约、项目蓝图、DAG 波次执行、真实补丁捕获、测试证据、质量门禁、审批与 Worker 队列。",
  metricSurface: "运行面",
  metricTenant: "当前租户",
  metricWorkers: "Worker 作业",
  metricAudits: "审计事件",
  createEyebrow: "创建与运行",
  createTitle: "启动托管 V2 项目",
  tenantScoped: "租户范围",
  projectName: "项目标识",
  projectTitle: "项目标题",
  projectPath: "项目路径（可选）",
  automationMode: "自动化模式",
  modeGuided: "引导模式",
  modeSupervised: "监督自动（默认）",
  modeFullAuto: "全自动候选",
  requirements: "需求说明",
  createSubmit: "创建项目并派发运行",
  creating: "正在派发...",
  modelEyebrow: "模型提供商",
  modelTitle: "第三方 API 配置",
  mockMode: "使用本地模拟模式",
  providerProfile: "提供商 Profile",
  apiBase: "API Base",
  apiPath: "API Path（可选覆盖）",
  modelName: "模型名称",
  apiKey: "API Key（不会明文持久化）",
  wireApi: "Wire API",
  timeout: "超时秒数",
  retries: "重试次数",
  maxTokens: "最大 Tokens",
  temperature: "温度",
  keyWarning: "建议通过环境变量提供密钥。保存配置不会明文写入 API Key；页面只会显示掩码。",
  saveModel: "保存配置",
  testModel: "测试连接",
  savingModel: "正在保存...",
  testingModel: "正在测试...",
  modelSaved: "配置已保存；密钥不会明文持久化。",
  modelTestOk: "连接成功",
  modelTestFail: "连接失败",
  projectsEyebrow: "项目",
  projectsTitle: "租户队列",
  refresh: "刷新",
  resultEyebrow: "Chief 结果",
  selectProject: "选择一个项目",
  approveCandidate: "审批 GO 候选",
  applyPatch: "应用发布补丁",
  timeline: "时间线",
  chiefEvents: "Chief 事件",
  agents: "智能体",
  attempts: "尝试记录",
  patches: "补丁",
  gitDiff: "Git diff 捕获",
  tests: "测试",
  runtimeProof: "运行证据",
  workers: "Worker 作业",
  hostedQueue: "托管队列",
  audit: "审计",
  tenantTrail: "租户轨迹",
  emptyProjects: "还没有 V2 项目。请从左侧创建一个项目。",
  emptyTimeline: "暂无运行事件。",
  emptyAgents: "暂无智能体尝试。",
  emptyPatches: "暂无补丁捕获。",
  emptyTests: "暂无测试证据。",
  emptyWorkers: "暂无 Worker 作业。",
  emptyAudit: "暂无审计事件。",
  noSummary: "暂无摘要",
  noChangedFiles: "无变更文件",
  noCommand: "未检测到命令",
  unknown: "未知",
  notRun: "未运行",
  unassigned: "未分配",
  approvedNote: "从托管 V2 控制台审批通过。",
};

const LABELS = {
  Project: "项目",
  Tenant: "租户",
  Owner: "负责人",
  Run: "运行",
  Decision: "决策",
  "Gate Score": "门禁分",
  Approval: "审批状态",
  Approvals: "审批数",
  "Changed Files": "变更文件",
  "Test Evidence": "测试证据",
  "Automation Mode": "自动化模式",
  "Decomposition Score": "拆解分",
  "Autonomy Level": "自治等级",
  "Repair Rounds": "修复轮次",
  "Continuation": "续跑状态",
};

const STATUS_TEXT = {
  draft: "草稿",
  contracted: "已契约化",
  running: "运行中",
  queued: "排队中",
  completed: "已完成",
  blocked: "已阻断",
  failed: "失败",
  cancelled: "已取消",
  release_candidate_ready: "发布候选就绪",
  ready: "就绪",
  applied: "已应用",
  approved: "已审批",
  not_requested: "未申请",
  passed: "通过",
  conflict: "冲突",
  error: "错误",
  info: "信息",
  warning: "警告",
  idle: "空闲",
  not_run: "未运行",
  supervised_auto: "监督自动",
  guided: "引导模式",
  full_auto_candidate: "全自动候选",
  planned: "已规划",
  quality_gate: "质量门禁",
  release_candidate_ready_state: "候选就绪",
};

const $ = (id) => document.getElementById(id);

const state = {
  projects: [],
  selectedProjectId: "",
  selectedRunId: "",
  tenant: null,
  user: null,
  workerJobs: [],
  auditEvents: [],
  config: null,
  profiles: [],
  pollTimer: null,
};

const nodes = {
  tenantPill: $("tenant-pill"),
  workerCount: $("worker-count"),
  auditCount: $("audit-count"),
  projectForm: $("project-form"),
  createSubmit: $("create-submit"),
  projectList: $("project-list"),
  refreshButton: $("refresh-button"),
  detailTitle: $("detail-title"),
  decisionChip: $("decision-chip"),
  summaryGrid: $("summary-grid"),
  approveButton: $("approve-button"),
  applyButton: $("apply-button"),
  timelineList: $("timeline-list"),
  agentList: $("agent-list"),
  patchList: $("patch-list"),
  testList: $("test-list"),
  workerList: $("worker-list"),
  auditList: $("audit-list"),
  modelForm: $("model-form"),
  modelChip: $("model-chip"),
  modelResult: $("model-result"),
  saveModelButton: $("save-model-button"),
  testModelButton: $("test-model-button"),
};

function applyTextDictionary() {
  document.querySelectorAll("[data-text]").forEach((node) => {
    node.textContent = TEXT[node.dataset.text] || node.dataset.text;
  });
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.message || `请求失败：${response.status}`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "-";
}

function translateStatus(value) {
  if (value === "GO" || value === "NO_GO" || value === "V2" || value === "DAG") return value;
  return STATUS_TEXT[String(value || "").toLowerCase()] || value || "-";
}

function latestRun(project) {
  return (project.runs || [])[0] || null;
}

function latestCandidate(project) {
  return project.latest_release_candidate || null;
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (["go", "ready", "release_candidate_ready", "passed", "applied", "completed", "approved"].includes(normalized)) return "good";
  if (["no_go", "blocked", "failed", "conflict", "error"].includes(normalized)) return "bad";
  if (["running", "queued", "info", "warning"].includes(normalized)) return "live";
  return "neutral";
}

function intField(form, name, fallback = 0) {
  const value = Number.parseInt(form[name].value, 10);
  return Number.isFinite(value) ? value : fallback;
}

function floatField(form, name, fallback = 0) {
  const value = Number.parseFloat(form[name].value);
  return Number.isFinite(value) ? value : fallback;
}

function renderProjects() {
  nodes.projectList.innerHTML = "";
  if (!state.projects.length) {
    nodes.projectList.innerHTML = `<div class="empty-state">${TEXT.emptyProjects}</div>`;
    return;
  }
  for (const project of state.projects) {
    const run = latestRun(project);
    const candidate = latestCandidate(project);
    const active = project.id === state.selectedProjectId ? "active" : "";
    const decision = candidate?.decision || project.status || "draft";
    const card = document.createElement("button");
    card.type = "button";
    card.className = `project-row ${active}`;
    card.innerHTML = `
      <span class="project-row-main">
        <strong>${escapeHtml(project.title || project.name)}</strong>
        <small>${escapeHtml(project.name)} / ${shortId(project.id)}</small>
      </span>
      <span class="status-chip ${statusClass(decision)}">${escapeHtml(translateStatus(decision))}</span>
      <small>${escapeHtml(translateStatus(run?.status || "not_run"))}</small>
    `;
    card.addEventListener("click", () => selectProject(project.id));
    nodes.projectList.appendChild(card);
  }
}

function renderSummary(project, runDetail) {
  const candidate = latestCandidate(project) || runDetail?.release_candidate || {};
  const quality = candidate.quality_gate || {};
  const approvals = project.approvals || [];
  const continuation = runDetail?.continuation_state || {};
  const values = [
    ["Project", project.name],
    ["Tenant", project.tenant_id],
    ["Owner", project.owner_user_id],
    ["Run", translateStatus(runDetail?.status || latestRun(project)?.status || "not_run")],
    ["Decision", candidate.decision || "-"],
    ["Gate Score", quality.score ?? candidate.gate_score ?? "-"],
    ["Approval", translateStatus(project.approval_state || "not_requested")],
    ["Approvals", approvals.length],
    ["Changed Files", (candidate.changed_files || []).length],
    ["Test Evidence", candidate.test_evidence_count ?? 0],
    ["Automation Mode", translateStatus(project.automation_mode || "supervised_auto")],
    ["Decomposition Score", runDetail?.decomposition_score ?? "-"],
    ["Autonomy Level", runDetail?.autonomy_level ?? "-"],
    ["Repair Rounds", runDetail?.repair_round_count ?? 0],
    ["Continuation", translateStatus(continuation.state || "-")],
  ];
  nodes.summaryGrid.innerHTML = values
    .map(([label, value]) => `<article><span>${escapeHtml(LABELS[label] || label)}</span><strong>${escapeHtml(value)}</strong></article>`)
    .join("");
  nodes.detailTitle.textContent = project.title || project.name;
  nodes.decisionChip.textContent = translateStatus(candidate.decision || project.status || "draft");
  nodes.decisionChip.className = `status-chip ${statusClass(candidate.decision || project.status)}`;
  const canApprove = candidate.status === "ready" && candidate.decision === "GO" && project.approval_state !== "approved";
  nodes.approveButton.disabled = !canApprove;
  nodes.applyButton.disabled = !(candidate.status === "ready" && candidate.decision === "GO");
}

function renderEvidenceList(node, items, renderer, emptyText) {
  node.innerHTML = "";
  if (!items.length) {
    node.innerHTML = `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
    return;
  }
  for (const item of items.slice(0, 80)) {
    const row = document.createElement("article");
    row.className = `evidence-row ${statusClass(item.status || item.level || item.decision)}`;
    row.innerHTML = renderer(item);
    node.appendChild(row);
  }
}

function renderRunEvidence(runDetail, events) {
  renderEvidenceList(
    nodes.timelineList,
    events,
    (event) => `
      <strong>${escapeHtml(event.source)} / ${escapeHtml(translateStatus(event.level))}</strong>
      <p>${escapeHtml(event.message)}</p>
      <small>${escapeHtml(event.ts)}</small>
    `,
    TEXT.emptyTimeline
  );
  renderEvidenceList(
    nodes.agentList,
    runDetail?.agent_runs || [],
    (run) => `
      <strong>${escapeHtml(run.role)} / ${escapeHtml(translateStatus(run.status))}</strong>
      <p>${escapeHtml(run.summary || run.error || TEXT.noSummary)}</p>
      <small>${escapeHtml(run.work_package_id)} / 第 ${escapeHtml(run.attempt)} 次</small>
    `,
    TEXT.emptyAgents
  );
  renderEvidenceList(
    nodes.patchList,
    runDetail?.patch_sets || [],
    (patch) => `
      <strong>${escapeHtml(patch.role)} / ${escapeHtml(translateStatus(patch.status))}</strong>
      <p>${escapeHtml((patch.files_changed || []).join(", ") || TEXT.noChangedFiles)}</p>
      <small>${escapeHtml(patch.diff_summary || patch.error || patch.work_package_id)}</small>
    `,
    TEXT.emptyPatches
  );
  renderEvidenceList(
    nodes.testList,
    runDetail?.test_runs || [],
    (test) => `
      <strong>${escapeHtml(test.kind)} / ${escapeHtml(translateStatus(test.status))}</strong>
      <p>${escapeHtml(test.command || TEXT.noCommand)}</p>
      <small>退出码 ${escapeHtml(test.exit_code)} / 修复轮次 ${escapeHtml(test.repair_round)}</small>
    `,
    TEXT.emptyTests
  );
}

function renderHostedLists() {
  nodes.tenantPill.textContent = state.tenant?.slug || TEXT.unknown;
  nodes.workerCount.textContent = String(state.workerJobs.length);
  nodes.auditCount.textContent = String(state.auditEvents.length);
  renderEvidenceList(
    nodes.workerList,
    state.workerJobs,
    (job) => `
      <strong>${escapeHtml(job.queue)} / ${escapeHtml(translateStatus(job.status))}</strong>
      <p>run ${shortId(job.run_id)} / worker ${escapeHtml(job.worker_id || TEXT.unassigned)}</p>
      <small>${escapeHtml(job.updated_at || job.created_at)}</small>
    `,
    TEXT.emptyWorkers
  );
  renderEvidenceList(
    nodes.auditList,
    state.auditEvents,
    (event) => `
      <strong>${escapeHtml(event.action)}</strong>
      <p>${escapeHtml(event.message)}</p>
      <small>${escapeHtml(event.resource_type)} ${shortId(event.resource_id)} / ${escapeHtml(event.created_at)}</small>
    `,
    TEXT.emptyAudit
  );
}

function renderProfiles() {
  const select = nodes.modelForm.provider_profile;
  select.innerHTML = "";
  for (const profile of state.profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = `${profile.id} - ${profile.label}`;
    select.appendChild(option);
  }
}

function renderModelConfig() {
  const llm = state.config?.llm || {};
  const form = nodes.modelForm;
  form.use_mock.checked = Boolean(llm.use_mock);
  form.provider_profile.value = llm.provider_profile || (llm.wire_api === "responses" ? "openai-responses" : "openai-chat-completions");
  form.api_base.value = llm.api_base || "";
  form.api_path.value = llm.api_path || "";
  form.model.value = llm.model || "";
  form.api_key.value = llm.api_key === "***" ? "" : llm.api_key || "";
  form.wire_api.value = llm.wire_api || "chat_completions";
  form.timeout_seconds.value = llm.timeout_seconds || 90;
  form.retry_attempts.value = llm.retry_attempts || 2;
  form.max_tokens.value = llm.max_tokens || 3200;
  form.temperature.value = llm.temperature ?? 0.2;
  nodes.modelChip.textContent = llm.use_mock ? "模拟模式" : "待测试";
  nodes.modelChip.className = `status-chip ${llm.use_mock ? "neutral" : "live"}`;
}

function collectModelPayload() {
  const form = nodes.modelForm;
  const llm = {
    use_mock: form.use_mock.checked,
    provider_profile: form.provider_profile.value,
    api_base: form.api_base.value.trim(),
    api_path: form.api_path.value.trim(),
    model: form.model.value.trim(),
    api_key: form.api_key.value.trim(),
    wire_api: form.wire_api.value,
    timeout_seconds: intField(form, "timeout_seconds", 90),
    retry_attempts: intField(form, "retry_attempts", 2),
    max_tokens: intField(form, "max_tokens", 3200),
    temperature: floatField(form, "temperature", 0.2),
  };
  if (!llm.api_key) {
    llm.api_key = "***";
  }
  return { llm };
}

function renderModelResult(payload, okText, failText) {
  const ok = Boolean(payload.ok);
  nodes.modelChip.textContent = ok ? okText : failText;
  nodes.modelChip.className = `status-chip ${ok ? "good" : "bad"}`;
  const parts = [
    payload.profile?.id,
    payload.wire_api,
    payload.model,
    payload.elapsed_ms ? `${payload.elapsed_ms} ms` : "",
  ].filter(Boolean);
  nodes.modelResult.innerHTML = `
    <strong>${escapeHtml(ok ? okText : failText)}</strong>
    <p>${escapeHtml(payload.error || payload.response_preview || parts.join(" / ") || "")}</p>
  `;
}

async function loadModelState() {
  const [config, profiles] = await Promise.all([fetchJson("/api/config"), fetchJson("/api/v2/model/profiles")]);
  state.config = config;
  state.profiles = profiles.items || [];
  renderProfiles();
  renderModelConfig();
}

async function saveModelConfig(event) {
  event.preventDefault();
  nodes.saveModelButton.disabled = true;
  nodes.saveModelButton.textContent = TEXT.savingModel;
  try {
    const payload = await fetchJson("/api/config", {
      method: "POST",
      body: JSON.stringify(collectModelPayload()),
    });
    state.config = payload.config;
    renderModelConfig();
    renderModelResult({ ok: true, profile: { id: state.config.llm.provider_profile }, model: state.config.llm.model }, TEXT.modelSaved, TEXT.modelTestFail);
  } catch (error) {
    renderModelResult({ ok: false, error: error.message }, TEXT.modelTestOk, TEXT.modelTestFail);
  } finally {
    nodes.saveModelButton.disabled = false;
    nodes.saveModelButton.textContent = TEXT.saveModel;
  }
}

async function testModelConnection() {
  nodes.testModelButton.disabled = true;
  nodes.testModelButton.textContent = TEXT.testingModel;
  try {
    const result = await fetchJson("/api/v2/model/test-connection", {
      method: "POST",
      body: JSON.stringify(collectModelPayload()),
    });
    renderModelResult(result, TEXT.modelTestOk, TEXT.modelTestFail);
  } catch (error) {
    renderModelResult({ ok: false, error: error.message }, TEXT.modelTestOk, TEXT.modelTestFail);
  } finally {
    nodes.testModelButton.disabled = false;
    nodes.testModelButton.textContent = TEXT.testModel;
  }
}

async function loadHostedState() {
  const [tenant, projects, jobs, audits] = await Promise.all([
    fetchJson("/api/v2/tenants/current"),
    fetchJson("/api/v2/projects"),
    fetchJson("/api/v2/worker-jobs"),
    fetchJson("/api/v2/audit-events"),
  ]);
  state.tenant = tenant.tenant;
  state.user = tenant.user;
  state.projects = projects.items || [];
  state.workerJobs = jobs.items || [];
  state.auditEvents = audits.items || [];
  if (!state.selectedProjectId && state.projects.length) {
    state.selectedProjectId = state.projects[0].id;
  }
  renderProjects();
  renderHostedLists();
  if (state.selectedProjectId) {
    await selectProject(state.selectedProjectId, { preserveList: true });
  }
}

async function selectProject(projectId, options = {}) {
  state.selectedProjectId = projectId;
  const project = await fetchJson(`/api/v2/projects/${projectId}`);
  const run = latestRun(project);
  let runDetail = null;
  let events = [];
  if (run) {
    state.selectedRunId = run.id;
    runDetail = await fetchJson(`/api/v2/runs/${run.id}`);
    events = (await fetchJson(`/api/v2/runs/${run.id}/events`)).items || [];
  }
  const index = state.projects.findIndex((item) => item.id === project.id);
  if (index >= 0) state.projects[index] = project;
  if (!options.preserveList) renderProjects();
  renderSummary(project, runDetail);
  renderRunEvidence(runDetail, events);
}

async function createProjectAndRun(event) {
  event.preventDefault();
  const form = event.currentTarget;
  nodes.createSubmit.disabled = true;
  nodes.createSubmit.textContent = TEXT.creating;
  try {
    const project = await fetchJson("/api/v2/projects", {
      method: "POST",
      body: JSON.stringify({
        name: form.name.value.trim(),
        title: form.title.value.trim(),
        project_path: form.project_path.value.trim(),
        automation_mode: form.automation_mode.value,
        description: form.description.value.trim(),
      }),
    });
    state.selectedProjectId = project.id;
    await fetchJson(`/api/v2/projects/${project.id}/runs`, { method: "POST", body: JSON.stringify({}) });
    form.reset();
    form.automation_mode.value = "supervised_auto";
    await loadHostedState();
    schedulePolling();
  } finally {
    nodes.createSubmit.disabled = false;
    nodes.createSubmit.textContent = TEXT.createSubmit;
  }
}

async function approveSelected() {
  const project = state.projects.find((item) => item.id === state.selectedProjectId);
  if (!project) return;
  await fetchJson("/api/v2/approvals", {
    method: "POST",
    body: JSON.stringify({
      project_id: project.id,
      approver: state.user?.display_name || "local-operator",
      note: TEXT.approvedNote,
    }),
  });
  await loadHostedState();
}

async function applySelected() {
  const project = state.projects.find((item) => item.id === state.selectedProjectId);
  const candidate = latestCandidate(project || {});
  if (!candidate?.id) return;
  await fetchJson(`/api/v2/release-candidates/${candidate.id}/apply`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  await loadHostedState();
}

function schedulePolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      await loadHostedState();
      const activeRun = state.projects.map(latestRun).find((run) => run && run.status === "running");
      if (!activeRun && state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch (error) {
      console.error(error);
    }
  }, 2500);
}

applyTextDictionary();
nodes.projectForm.addEventListener("submit", createProjectAndRun);
nodes.refreshButton.addEventListener("click", () => loadHostedState());
nodes.approveButton.addEventListener("click", approveSelected);
nodes.applyButton.addEventListener("click", applySelected);
nodes.modelForm.addEventListener("submit", saveModelConfig);
nodes.testModelButton.addEventListener("click", testModelConnection);

Promise.all([loadModelState(), loadHostedState()]).catch((error) => {
  nodes.projectList.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
});
