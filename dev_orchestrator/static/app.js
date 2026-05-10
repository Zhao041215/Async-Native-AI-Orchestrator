const API_BASE = "/api/v7";

const state = {
  projects: [],
  selectedProjectId: "",
  selectedRunId: "",
  selectedProject: null,
  selectedProjectRuns: [],
  selectedProjectIds: new Set(),
  workers: [],
  modelSettings: null,
  modelSettingsDirty: false,
};

const $ = (id) => document.getElementById(id);

const nodes = {
  health: $("health-pill"),
  workers: $("worker-count"),
  refresh: $("refresh"),
  createRun: $("create-run"),
  createRunStatus: $("create-run-status"),
  modelSettingsForm: $("model-settings-form"),
  applyRecommendedModel: $("apply-recommended-model"),
  testModelSettings: $("test-model-settings"),
  saveModelSettings: $("save-model-settings"),
  modelSettingsStatus: $("model-settings-status"),
  selectAllProjects: $("select-all-projects"),
  deleteSelectedProjects: $("delete-selected-projects"),
  form: $("project-form"),
  exportForm: $("delivery-export-form"),
  exportDelivery: $("export-delivery"),
  deliveryExportStatus: $("delivery-export-status"),
  projectList: $("project-list"),
  runTitle: $("run-title"),
  runSummary: $("run-summary"),
  runActions: $("run-actions"),
  missionSummary: $("mission-summary"),
  missionBlocker: $("mission-blocker"),
  missionKernel: $("mission-kernel"),
  executionPlan: $("execution-plan"),
  implementationPlan: $("implementation-plan"),
  stabilityReport: $("stability-report"),
  frontendQuality: $("frontend-quality"),
  contractValidation: $("contract-validation"),
  patchTransactions: $("patch-transactions"),
  testExecution: $("test-execution"),
  codeContractIndex: $("code-contract-index"),
  aiCallList: $("ai-call-list"),
  waveList: $("wave-list"),
  packageList: $("package-list"),
  qualityGateList: $("quality-gate-list"),
  repairList: $("repair-list"),
  jobList: $("job-list"),
  contextIndex: $("context-index"),
  continuation: $("continuation"),
  artifactList: $("artifact-list"),
  workerList: $("worker-list"),
};

const recommendedModelSettings = {
  model_provider: "custom",
  model: "gpt-5.3-codex",
  model_reasoning_effort: "xhigh",
  disable_response_storage: true,
  model_providers: {
    custom: {
      name: "custom",
      wire_api: "responses",
      requires_openai_auth: true,
      base_url: "https://deepkey.top/v1",
    },
  },
};

async function fetchJson(path, options = {}) {
  const url = String(path || "").startsWith("/api/") ? path : `${API_BASE}${path}`;
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  return payload;
}

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "-";
}

function statusClass(value) {
  const s = String(value || "").toLowerCase();
  if (["completed", "release_ready", "go", "passed", "running", "ok", "pass"].includes(s)) return "good";
  if (["no_go", "dead_letter", "blocked", "failed", "rollback_failed", "fail", "blocked_for_human_review"].includes(s)) return "bad";
  if (["queued", "retry", "paused", "leased", "recovering", "retry_ai_call"].includes(s)) return "live";
  return "neutral";
}

function latestArtifactByKind(items, kind) {
  const matches = (items || []).filter((item) => item.kind === kind);
  return matches[matches.length - 1] || null;
}

function setCreateRunStatus(message, tone = "neutral") {
  if (!nodes.createRunStatus) return;
  nodes.createRunStatus.textContent = message || "";
  nodes.createRunStatus.className = `form-status ${tone}`;
}

function setDeliveryExportStatus(message, tone = "neutral") {
  if (!nodes.deliveryExportStatus) return;
  nodes.deliveryExportStatus.textContent = message || "";
  nodes.deliveryExportStatus.className = `form-status ${tone}`;
}

function createActionButton(label, { className = "", disabled = false, title = "", onClick }) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (className) button.className = className;
  if (title) button.title = title;
  button.disabled = disabled;
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "...";
    try {
      await onClick();
    } catch (error) {
      nodes.health.textContent = error.message;
      nodes.health.className = "pill bad";
    } finally {
      button.textContent = original;
      button.disabled = disabled;
    }
  });
  return button;
}

// ─── Render Actions ───

function renderRunActions(current, continuation, artifacts) {
  nodes.runActions.innerHTML = "";
  if (!current) {
    nodes.runActions.innerHTML = '<div class="empty">No run selected</div>';
    return;
  }

  const releaseCandidate = latestArtifactByKind(artifacts.items || [], "release_candidate");
  const nextAction = continuation.continuation?.next_action || current.continuation?.next_action || "-";
  const releaseStatus = continuation.continuation?.release_status || current.continuation?.release_status || "-";
  const canApply = Boolean(releaseCandidate?.id) && (current.status === "release_ready" || nextAction === "apply" || releaseStatus === "GO");
  const canRollback = Boolean(releaseCandidate?.id) && (current.status === "completed" || releaseStatus === "applied" || nextAction === "delivery_complete");
  const canRepair = ["blocked", "no_go", "blocked_for_human_review"].includes(String(current.status || "").toLowerCase()) || String(nextAction || "").startsWith("repair");
  const canPause = !["paused", "completed", "rolled_back", "cancelled"].includes(String(current.status || "").toLowerCase());
  const canResume = ["paused", "blocked"].includes(String(current.status || "").toLowerCase());
  const canRequeue = ["blocked", "paused", "no_go"].includes(String(current.status || "").toLowerCase());
  const canRecoverAll = ["blocked", "paused", "no_go", "recovering"].includes(String(current.status || "").toLowerCase());

  const meta = document.createElement("div");
  meta.className = "action-meta";
  meta.innerHTML = `
    <span class="chip ${statusClass(current.status)}">${esc(current.status || "-")}</span>
    <span class="chip neutral">ckpt ${esc(current.checkpoint || "-")}</span>
    <span class="chip ${statusClass(nextAction)}">next ${esc(nextAction)}</span>
    <span class="chip ${statusClass(releaseStatus)}">rel ${esc(releaseStatus)}</span>
  `;

  const buttons = document.createElement("div");
  buttons.className = "action-buttons";
  buttons.appendChild(createActionButton("Apply", {
    className: "primary", disabled: !canApply,
    title: releaseCandidate?.id ? `Apply release candidate ${shortId(releaseCandidate.id)}` : "No release candidate yet",
    onClick: async () => { if (!releaseCandidate?.id) return; await fetchJson(`${API_BASE}/release-candidates/${releaseCandidate.id}/apply`, { method: "POST" }); await refreshAll(); },
  }));
  buttons.appendChild(createActionButton("Rollback", {
    className: "danger subtle", disabled: !canRollback,
    title: releaseCandidate?.id ? `Rollback release candidate ${shortId(releaseCandidate.id)}` : "No release candidate yet",
    onClick: async () => { if (!releaseCandidate?.id) return; await fetchJson(`${API_BASE}/release-candidates/${releaseCandidate.id}/rollback`, { method: "POST" }); await refreshAll(); },
  }));
  buttons.appendChild(createActionButton("Repair", {
    className: "subtle", disabled: !canRepair, title: "Create a repair job",
    onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/repair`, { method: "POST" }); await refreshAll(); },
  }));
  buttons.appendChild(createActionButton("Requeue", {
    className: "subtle", disabled: !canRequeue, title: "Move blocked run back to queued",
    onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/requeue-blocked`, { method: "POST" }); await refreshAll(); },
  }));
  buttons.appendChild(createActionButton("Pause", {
    className: "subtle", disabled: !canPause, title: "Pause at next job boundary",
    onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/pause`, { method: "POST" }); await refreshAll(); },
  }));
  buttons.appendChild(createActionButton("Resume", {
    className: "subtle", disabled: !canResume, title: "Resume a paused run",
    onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/resume`, { method: "POST" }); await refreshAll(); },
  }));
  buttons.appendChild(createActionButton("Refresh", {
    className: "subtle", title: "Refresh this run",
    onClick: async () => { await renderRun(current.id); },
  }));
  buttons.appendChild(createActionButton("Recover", {
    className: "subtle", disabled: !canRecoverAll, title: "Recover stale seeds and dead-letter jobs",
    onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/recover-all`, { method: "POST" }); await refreshAll(); },
  }));

  const note = document.createElement("div");
  note.className = "action-note";
  note.textContent = releaseCandidate?.id
    ? `Release candidate ${shortId(releaseCandidate.id)} ready. Apply starts the release; rollback reverts.`
    : "No release candidate yet. Wait for quality and release generation, or use Repair if blocked.";

  nodes.runActions.append(meta, buttons, note);
}

// ─── Refresh All ───

async function refreshAll() {
  const [health, projects, workers, modelSettings] = await Promise.all([
    fetchJson(`${API_BASE}/health`),
    fetchJson(`${API_BASE}/projects`),
    fetchJson(`${API_BASE}/workers?limit=12`).catch(() => ({ items: [] })),
    fetchJson(`${API_BASE}/model-settings`).catch(() => ({ llm: null, recommended: recommendedModelSettings })),
  ]);
  nodes.health.textContent = `${health.status} / ${health.kernel || "v7"}`;
  nodes.health.className = "pill good";
  state.projects = projects.items || projects || [];
  state.workers = workers.items || [];
  state.modelSettings = modelSettings;
  renderModelSettings(modelSettings);
  state.selectedProjectIds = new Set([...state.selectedProjectIds].filter((id) => state.projects.some((p) => p.id === id)));
  if (state.selectedProjectId && !state.projects.some((p) => p.id === state.selectedProjectId)) {
    state.selectedProjectId = "";
    state.selectedRunId = "";
    state.selectedProject = null;
    state.selectedProjectRuns = [];
  }
  nodes.workers.textContent = `workers ${state.workers.length}`;
  renderProjects();
  renderWorkers();
  if (state.selectedRunId) {
    await renderRun(state.selectedRunId);
  } else if (state.selectedProject) {
    renderProjectOverview();
  } else {
    clearDetailView();
  }
}

function clearDetailView() {
  nodes.runTitle.textContent = "Select a Project";
  nodes.runSummary.innerHTML = "";
  nodes.runActions.innerHTML = "";
  if (nodes.exportForm) nodes.exportForm.classList.add("hidden");
  setDeliveryExportStatus("");
  nodes.missionSummary.innerHTML = "";
  if (nodes.missionBlocker) nodes.missionBlocker.innerHTML = "";
  if (nodes.missionKernel) nodes.missionKernel.innerHTML = "";
  if (nodes.executionPlan) nodes.executionPlan.innerHTML = "";
  if (nodes.implementationPlan) nodes.implementationPlan.innerHTML = "";
  if (nodes.stabilityReport) nodes.stabilityReport.innerHTML = "";
  if (nodes.frontendQuality) nodes.frontendQuality.innerHTML = "";
  nodes.contractValidation.innerHTML = "";
  nodes.patchTransactions.innerHTML = "";
  nodes.testExecution.innerHTML = "";
  nodes.codeContractIndex.innerHTML = "";
  nodes.aiCallList.innerHTML = '<div class="empty">No project selected</div>';
  nodes.waveList.innerHTML = '<div class="empty">No project selected</div>';
  nodes.packageList.innerHTML = '<div class="empty">No project selected</div>';
  nodes.qualityGateList.innerHTML = '<div class="empty">No project selected</div>';
  nodes.repairList.innerHTML = '<div class="empty">No project selected</div>';
  nodes.jobList.innerHTML = '<div class="empty">No project selected</div>';
  nodes.contextIndex.textContent = "";
  nodes.continuation.textContent = "";
  nodes.artifactList.innerHTML = '<div class="empty">No project selected</div>';
}

function renderProjectOverview() {
  const project = state.selectedProject;
  if (!project) { clearDetailView(); return; }
  const runs = state.selectedProjectRuns || [];
  const latestRun = runs[0];
  const tech = project.config?.stack_pack || "AI-decided";
  const scale = project.config?.target_scale || "-";
  const path = project.resolved_project_root || project.project_path_status?.project_root || project.project_path || "-";
  nodes.runTitle.textContent = `${project.title || project.name} — Project`;
  nodes.runSummary.innerHTML = `
    <div><strong>${esc(project.status || "-")}</strong><small>status</small></div>
    <div><strong>${esc(tech)}</strong><small>tech</small></div>
    <div><strong>${esc(scale)}</strong><small>scale</small></div>
  `;
  nodes.runActions.innerHTML = '<div class="empty">No run selected</div>';
  if (nodes.exportForm) nodes.exportForm.classList.add("hidden");
  setDeliveryExportStatus("");
  nodes.missionSummary.innerHTML = `
    <div><strong>${esc(path)}</strong><small>path</small></div>
    <div><strong>${esc(runs.length)}</strong><small>runs</small></div>
    <div><strong>${esc(latestRun?.status || "none")}</strong><small>latest</small></div>
    <div><strong>${esc(latestRun?.checkpoint || "-")}</strong><small>checkpoint</small></div>
  `;
  if (nodes.missionKernel) {
    nodes.missionKernel.innerHTML = `
      <div><strong>${esc(project.scale_profile?.name || project.config?.target_scale || "-")}</strong><small>profile</small></div>
      <div><strong>${esc(project.scale_profile?.kernel_generation || "-")}</strong><small>kernel</small></div>
      <div><strong>${esc(project.scale_profile?.wave_parallelism || "-")}</strong><small>parallelism</small></div>
      <div><strong>${esc(project.scale_profile?.recovery_policy || "-")}</strong><small>recovery</small></div>
    `;
  }
  const emptyMsg = '<div class="empty">No run selected</div>';
  nodes.contractValidation.innerHTML = emptyMsg;
  nodes.patchTransactions.innerHTML = emptyMsg;
  nodes.testExecution.innerHTML = emptyMsg;
  nodes.codeContractIndex.innerHTML = emptyMsg;
  nodes.aiCallList.innerHTML = emptyMsg;
  nodes.waveList.innerHTML = emptyMsg;
  nodes.packageList.innerHTML = emptyMsg;
  nodes.qualityGateList.innerHTML = emptyMsg;
  nodes.repairList.innerHTML = emptyMsg;
  nodes.jobList.innerHTML = emptyMsg;
  nodes.contextIndex.textContent = JSON.stringify({ project_id: project.id, name: project.name, title: project.title, latest_run: latestRun?.id || "", status: latestRun?.status || "none" }, null, 2);
  nodes.continuation.textContent = JSON.stringify({ project_view: true, project_id: project.id, latest_run: latestRun?.id || "", status: latestRun?.status || "none", checkpoint: latestRun?.checkpoint || "", next: latestRun?.id ? "open_latest_run" : "create_new_run" }, null, 2);
  nodes.artifactList.innerHTML = emptyMsg;
}

// ─── Projects ───

function renderProjects() {
  nodes.projectList.innerHTML = "";
  const count = state.selectedProjectIds.size;
  nodes.deleteSelectedProjects.disabled = count === 0;
  nodes.deleteSelectedProjects.textContent = count ? `Delete (${count})` : "Delete selected";
  nodes.selectAllProjects.checked = state.projects.length > 0 && state.projects.every((p) => state.selectedProjectIds.has(p.id));
  nodes.selectAllProjects.indeterminate = count > 0 && count < state.projects.length;
  if (!state.projects.length) {
    nodes.projectList.innerHTML = '<div class="empty">No projects yet</div>';
    return;
  }
  for (const project of state.projects) {
    const row = document.createElement("div");
    row.className = `row project-row actionable ${project.id === state.selectedProjectId ? "active" : ""}`;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.innerHTML = `
      <input class="project-check" type="checkbox" ${state.selectedProjectIds.has(project.id) ? "checked" : ""} aria-label="Select ${esc(project.title || project.name)}">
      <span class="project-meta">
        <strong>${esc(project.title || project.name)}</strong>
        <small>${esc(project.name)} / ${shortId(project.id)}</small>
      </span>
      <span class="chip ${statusClass(project.status)}">${esc(project.status)}</span>
      <button class="project-run subtle" type="button">Run</button>
    `;
    const check = row.querySelector(".project-check");
    const runBtn = row.querySelector(".project-run");
    const sync = () => { check.checked ? state.selectedProjectIds.add(project.id) : state.selectedProjectIds.delete(project.id); renderProjects(); };
    check.addEventListener("click", (e) => e.stopPropagation());
    check.addEventListener("change", (e) => { e.stopPropagation(); sync(); });
    const open = async () => {
      const [p, r] = await Promise.all([
        fetchJson(`${API_BASE}/projects/${project.id}`),
        fetchJson(`${API_BASE}/projects/${project.id}/runs`).catch(() => ({ items: [] })),
      ]);
      state.selectedProjectId = project.id;
      state.selectedProject = p.project || p;
      state.selectedProjectRuns = r.items || r || [];
      state.selectedRunId = state.selectedProjectRuns[0]?.id || "";
      await refreshAll();
    };
    const run = async () => {
      const r = await fetchJson(`${API_BASE}/projects/${project.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ requirements_text: project.description || project.title || project.name }),
      });
      state.selectedProjectId = project.id;
      state.selectedProject = project;
      state.selectedProjectRuns = [r.run, ...(state.selectedProjectRuns || []).filter((i) => i.id !== r.run.id)];
      state.selectedRunId = r.run.id;
      await refreshAll();
    };
    row.addEventListener("click", (e) => { if (e.target.closest("input, button, select, textarea, label")) return; open(); });
    row.addEventListener("keydown", (e) => { if (e.target.closest("input, button, select, textarea, label")) return; if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    runBtn.addEventListener("click", (e) => { e.stopPropagation(); run().catch((err) => { nodes.health.textContent = err.message; nodes.health.className = "pill bad"; }); });
    nodes.projectList.appendChild(row);
  }
}

async function deleteSelectedProjects() {
  const ids = [...state.selectedProjectIds];
  if (!ids.length) return;
  const names = state.projects.filter((p) => state.selectedProjectIds.has(p.id)).map((p) => p.title || p.name).slice(0, 5).join(", ");
  if (!window.confirm(`Delete ${ids.length} project(s)?\n\n${names}`)) return;
  await fetchJson(`${API_BASE}/projects/batch-delete`, { method: "POST", body: JSON.stringify({ project_ids: ids }) });
  if (state.selectedProjectId && state.selectedProjectIds.has(state.selectedProjectId)) {
    state.selectedProjectId = ""; state.selectedRunId = ""; state.selectedProject = null; state.selectedProjectRuns = [];
    nodes.runTitle.textContent = "Select a Project"; clearDetailView();
  }
  state.selectedProjectIds.clear();
  await refreshAll();
}

// ─── Model Settings ───

function providerNameFromProfile(profile) {
  const v = String(profile || "").trim();
  return v.startsWith("custom-") ? "custom" : v || "custom";
}

function recommendedFromPayload(payload) { return payload?.recommended || recommendedModelSettings; }

function modelProviderFromSettings(settings) {
  const llm = settings?.llm || {};
  const rec = recommendedFromPayload(settings);
  return {
    model_provider: providerNameFromProfile(llm.provider_profile) || rec.model_provider,
    model: llm.model || rec.model,
    model_reasoning_effort: llm.model_reasoning_effort || rec.model_reasoning_effort,
    disable_response_storage: Boolean(llm.disable_response_storage ?? rec.disable_response_storage),
    model_providers: {
      custom: {
        name: "custom",
        wire_api: llm.wire_api || rec.model_providers.custom.wire_api,
        requires_openai_auth: (llm.auth_header || "Authorization") === "Authorization",
        base_url: llm.api_base || rec.model_providers.custom.base_url,
      },
    },
  };
}

function fillModelSettingsForm(settings) {
  if (!nodes.modelSettingsForm) return;
  const v = modelProviderFromSettings(settings);
  const f = nodes.modelSettingsForm;
  f.elements.model_provider.value = v.model_provider;
  f.elements.base_url.value = v.model_providers.custom.base_url;
  f.elements.model.value = v.model;
  f.elements.wire_api.value = v.model_providers.custom.wire_api;
  f.elements.model_reasoning_effort.value = v.model_reasoning_effort;
  f.elements.disable_response_storage.checked = v.disable_response_storage;
  f.elements.requires_openai_auth.checked = Boolean(v.model_providers.custom.requires_openai_auth);
  f.elements.api_key.value = "";
}

function renderModelSettings(settings, { force = false } = {}) {
  if (!nodes.modelSettingsStatus) return;
  const formActive = nodes.modelSettingsForm?.contains(document.activeElement);
  if (force || (!state.modelSettingsDirty && !formActive)) fillModelSettingsForm(settings);
  const llm = settings?.llm || {};
  const profile = llm.provider_profile || "custom";
  const keyState = llm.api_key === "***" ? "key stored" : "key missing";
  nodes.modelSettingsStatus.innerHTML = `
    <span class="chip ${llm.use_mock ? "live" : "good"}">${esc(llm.use_mock ? "mock" : "live")}</span>
    <span class="chip neutral">${esc(profile)}</span>
    <span class="chip neutral">${esc(llm.wire_api || "responses")}</span>
    <span class="chip ${llm.api_key === "***" ? "good" : "live"}">${esc(keyState)}</span>
  `;
}

function applyRecommendedModelSettings() {
  fillModelSettingsForm({ recommended: recommendedModelSettings, llm: {} });
  state.modelSettingsDirty = true;
  nodes.modelSettingsStatus.innerHTML = '<span class="chip live">preset ready</span>';
}

function buildModelSettingsPayloadFromForm() {
  const f = new FormData(nodes.modelSettingsForm);
  const provider = String(f.get("model_provider") || "custom").trim();
  return {
    model_provider: provider,
    model: String(f.get("model") || "").trim(),
    model_reasoning_effort: String(f.get("model_reasoning_effort") || "").trim(),
    disable_response_storage: Boolean(f.get("disable_response_storage")),
    api_key: String(f.get("api_key") || ""),
    model_providers: {
      [provider]: {
        name: provider,
        wire_api: String(f.get("wire_api") || "responses").trim(),
        requires_openai_auth: Boolean(f.get("requires_openai_auth")),
        base_url: String(f.get("base_url") || "").trim(),
      },
    },
  };
}

async function saveModelSettings(event) {
  event.preventDefault();
  const orig = nodes.saveModelSettings?.textContent || "Save settings";
  if (nodes.saveModelSettings) { nodes.saveModelSettings.disabled = true; nodes.saveModelSettings.textContent = "Saving..."; }
  try {
    const result = await fetchJson(`${API_BASE}/model-settings`, { method: "PUT", body: JSON.stringify(buildModelSettingsPayloadFromForm()) });
    state.modelSettings = result; state.modelSettingsDirty = false;
    renderModelSettings(result, { force: true });
    nodes.modelSettingsStatus.insertAdjacentHTML("beforeend", '<span class="chip good">saved</span>');
  } catch (error) {
    nodes.modelSettingsStatus.innerHTML = `<span class="chip bad">${esc(error.message)}</span>`;
  } finally {
    if (nodes.saveModelSettings) { nodes.saveModelSettings.disabled = false; nodes.saveModelSettings.textContent = orig; }
  }
}

async function testModelSettings() {
  const orig = nodes.testModelSettings?.textContent || "Test API";
  if (nodes.testModelSettings) { nodes.testModelSettings.disabled = true; nodes.testModelSettings.textContent = "Testing..."; }
  nodes.modelSettingsStatus.innerHTML = '<span class="chip live">testing...</span>';
  try {
    const result = await fetchJson(`${API_BASE}/model-settings/test`, { method: "POST", body: JSON.stringify(buildModelSettingsPayloadFromForm()) });
    state.modelSettings = result; state.modelSettingsDirty = false;
    renderModelSettings(result, { force: true });
    const probe = result.result || {};
    const tone = result.ok ? "good" : "bad";
    const msg = result.ok ? "connection ok" : (probe.error || "failed");
    nodes.modelSettingsStatus.insertAdjacentHTML("beforeend", `<span class="chip ${tone}">${esc(msg)}</span>`);
  } catch (error) {
    nodes.modelSettingsStatus.innerHTML = `<span class="chip bad">${esc(error.message)}</span>`;
  } finally {
    if (nodes.testModelSettings) { nodes.testModelSettings.disabled = false; nodes.testModelSettings.textContent = orig; }
  }
}

// ─── Workers ───

function renderWorkers() {
  nodes.workerList.innerHTML = "";
  if (!state.workers.length) {
    nodes.workerList.innerHTML = '<div class="empty">No workers yet</div>';
    return;
  }
  for (const w of state.workers) {
    const row = document.createElement("div");
    row.className = "row static";
    row.innerHTML = `
      <span><strong>${esc(w.role || w.worker_id)}</strong><small>pid ${esc(w.pid || "-")} / ${esc(w.last_heartbeat || "-")}</small></span>
      <span class="chip ${statusClass(w.status)}">${esc(w.status)}</span>
    `;
    nodes.workerList.appendChild(row);
  }
}

// ─── Run Detail ───

async function renderRun(runId) {
  const [run, jobs, continuation, artifacts, aiCalls, waves, packages, quality, contextIndex, repairs, layout, patchSets, contractReport, patchTransactions, testExecution, codeIndex, contractIndex, mission] = await Promise.all([
    fetchJson(`${API_BASE}/runs/${runId}`),
    fetchJson(`${API_BASE}/runs/${runId}/jobs`),
    fetchJson(`${API_BASE}/runs/${runId}/continuation`),
    fetchJson(`${API_BASE}/runs/${runId}/artifacts`),
    fetchJson(`${API_BASE}/runs/${runId}/ai-calls`).catch(() => ({ items: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/waves`).catch(() => ({ items: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/packages`).catch(() => ({ items: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/quality-report`).catch(() => ({ report: null })),
    fetchJson(`${API_BASE}/runs/${runId}/context-index`).catch(() => ({ snapshot: null })),
    fetchJson(`${API_BASE}/runs/${runId}/repair-history`).catch(() => ({ items: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/project-layout`).catch(() => ({ layout: null })),
    fetchJson(`${API_BASE}/runs/${runId}/patch-sets`).catch(() => ({ items: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/agent-contract-report`).catch(() => ({ report: null })),
    fetchJson(`${API_BASE}/runs/${runId}/patch-transactions`).catch(() => ({ report: null, items: [], conflicts: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/test-execution`).catch(() => ({ report: null })),
    fetchJson(`${API_BASE}/runs/${runId}/code-index`).catch(() => ({ index: null })),
    fetchJson(`${API_BASE}/runs/${runId}/contract-index`).catch(() => ({ index: null })),
    fetchJson(`${API_BASE}/runs/${runId}/mission`).catch(() => ({ mission_state: null })),
  ]);

  const current = run.run || run;
  const qr = quality.report || {};
  const rc = latestArtifactByKind(artifacts.items || [], "release_candidate");
  const wave = continuation.continuation?.current_wave || "-";
  const effLoc = qr.effective_loc?.total ?? current.metadata?.effective_loc_metrics?.total ?? 0;
  const tgtLoc = current.metadata?.project_config?.effective_loc_target || "-";
  const ms = mission.mission_state || mission || {};
  const profile = ms.scale_profile || current.metadata?.scale_profile || {};
  const recovery = ms.recovery || {};
  const provider = ms.provider_health || {};

  nodes.runTitle.textContent = `Run ${shortId(current.id)}`;
  nodes.runSummary.innerHTML = `
    <div><strong>${esc(current.status)}</strong><small>status</small></div>
    <div><strong>${esc(current.checkpoint)}</strong><small>checkpoint</small></div>
    <div><strong>${esc(layout.layout?.delivery_root || current.metadata?.project_layout?.delivery_root || "-")}</strong><small>delivery</small></div>
  `;
  renderRunActions(current, continuation, artifacts);
  renderDeliveryExport(current);

  nodes.missionSummary.innerHTML = `
    <div><strong>${esc(wave)}</strong><small>wave</small></div>
    <div><strong>${esc(effLoc)} / ${esc(tgtLoc)}</strong><small>LOC</small></div>
    <div><strong>${esc(aiCalls.items?.length || 0)}</strong><small>runs</small></div>
    <div><strong>${esc(patchSets.items?.length || 0)}</strong><small>patches</small></div>
  `;

  const blocker = current.continuation?.active_blocker || current.continuation?.failure_reason || recovery?.last_failure_reason || "none";
  if (nodes.missionBlocker) {
    nodes.missionBlocker.innerHTML = `
      <div><strong class="${statusClass(current.status)}">${esc(blocker)}</strong><small>blocker</small></div>
      <div><strong>${esc(recovery.dead_letter_count || 0)}</strong><small>dead letters</small></div>
      <div><strong>${esc(recovery.retryable_provider_failures || 0)}</strong><small>retryable</small></div>
      <div><strong>${esc(provider.degraded_count || 0)}</strong><small>degraded</small></div>
    `;
  }

  if (nodes.missionKernel) {
    nodes.missionKernel.innerHTML = `
      <div><strong>${esc(profile.name || "-")}</strong><small>profile</small></div>
      <div><strong>${esc(profile.wave_parallelism || "-")}</strong><small>width</small></div>
      <div><strong class="${statusClass(ms.next_action)}">${esc(ms.next_action || "-")}</strong><small>next</small></div>
      <div><strong class="${recovery.dead_letter_count ? "bad" : "good"}">${esc(recovery.dead_letter_count || 0)}</strong><small>dead</small></div>
      <div><strong>${esc(recovery.retryable_provider_failures || 0)}</strong><small>retryable</small></div>
      <div><strong class="${provider.degraded_count ? "bad" : "good"}">${esc(provider.degraded_count || 0)}</strong><small>degraded</small></div>
      <div><strong>${esc(shortId(ms.context?.mission_memory_hash || ""))}</strong><small>memory</small></div>
      <div><strong>${esc(profile.recovery_policy || "-")}</strong><small>policy</small></div>
    `;
  }

  if (nodes.executionPlan) {
    nodes.executionPlan.innerHTML = `
      <div><strong>${esc(ms.mission_flow?.stages?.length || 0)}</strong><small>stages</small></div>
      <div><strong>${esc((ms.blockers || []).length)}</strong><small>blockers</small></div>
      <div><strong>${esc(ms.graph?.package_count || 0)}</strong><small>packages</small></div>
      <div><strong>${esc(ms.graph?.wave_count || 0)}</strong><small>waves</small></div>
    `;
  }

  if (nodes.implementationPlan) {
    const impl = ms.implementation_plan || {};
    nodes.implementationPlan.innerHTML = `
      <div><strong>${esc((impl.flows || []).length)}</strong><small>flows</small></div>
      <div><strong>${esc((impl.blockers || []).length)}</strong><small>blockers</small></div>
      <div><strong>${esc((impl.flows?.[0]?.micro_tasks || []).length || 0)}</strong><small>micro</small></div>
      <div><strong>${esc((impl.flows?.[0]?.file_plan || []).length || 0)}</strong><small>files</small></div>
    `;
  }

  if (nodes.stabilityReport) {
    const stab = ms.stability_report || {};
    nodes.stabilityReport.innerHTML = `
      <div><strong>${esc((stab.dead_letter_jobs || []).length)}</strong><small>dead</small></div>
      <div><strong>${esc((stab.ai_slots?.active_count || 0))}</strong><small>slots</small></div>
      <div><strong>${esc((stab.provider_health?.degraded_count || 0))}</strong><small>degraded</small></div>
      <div><strong>${esc((stab.blockers || []).length)}</strong><small>blockers</small></div>
    `;
  }

  if (nodes.frontendQuality) {
    const fq = ms.frontend_quality || {};
    nodes.frontendQuality.innerHTML = `
      <div><strong class="${fq.ok ? "good" : "bad"}">${esc(fq.ok ? "pass" : "fail")}</strong><small>ux</small></div>
      <div><strong>${esc(fq.frontend_ux_gate?.name || "-")}</strong><small>gate</small></div>
      <div><strong>${esc(fq.frontend_ux_gate?.severity || "-")}</strong><small>severity</small></div>
      <div><strong>${esc(shortId(fq.quality_report?.index_hash || ""))}</strong><small>hash</small></div>
    `;
  }

  const cp = contractReport.report || {};
  const pp = patchTransactions.report || {};
  const tp = testExecution.report || {};
  const ci = codeIndex.index || {};
  const cxi = contractIndex.index || {};

  nodes.contractValidation.innerHTML = `
    <div><strong class="${cp.ok ? "good" : "bad"}">${esc(cp.status || "pending")}</strong><small>status</small></div>
    <div><strong>${esc(cp.validation_count || 0)}</strong><small>validated</small></div>
    <div><strong>${esc(cp.violation_count || 0)}</strong><small>violations</small></div>
    <div><strong>${esc(shortId(cp.run_id || current.id))}</strong><small>run</small></div>
  `;

  nodes.patchTransactions.innerHTML = `
    <div><strong class="${pp.ok !== false ? "good" : "bad"}">${esc(pp.transaction_count || patchTransactions.items?.length || 0)}</strong><small>txns</small></div>
    <div><strong>${esc(pp.changed_file_count || 0)}</strong><small>files</small></div>
    <div><strong class="${(pp.conflict_count || patchTransactions.conflicts?.length || 0) ? "bad" : "good"}">${esc(pp.conflict_count || patchTransactions.conflicts?.length || 0)}</strong><small>conflicts</small></div>
    <div><strong>${esc(patchTransactions.items?.[patchTransactions.items.length - 1]?.payload?.transaction_id ? shortId(patchTransactions.items[patchTransactions.items.length - 1].payload.transaction_id) : "-")}</strong><small>latest</small></div>
  `;

  nodes.testExecution.innerHTML = `
    <div><strong class="${tp.ok ? "good" : "bad"}">${esc(tp.status || "pending")}</strong><small>status</small></div>
    <div><strong>${esc(tp.executed_count || 0)} / ${esc(tp.command_count || 0)}</strong><small>executed</small></div>
    <div><strong>${esc(tp.source || "-")}</strong><small>source</small></div>
    <div><strong>${esc((tp.safety_failures || []).length)}</strong><small>safety</small></div>
  `;

  nodes.codeContractIndex.innerHTML = `
    <div><strong>${esc((ci.files || []).length)}</strong><small>files</small></div>
    <div><strong>${esc(shortId(ci.index_hash || ""))}</strong><small>code</small></div>
    <div><strong>${esc((cxi.contracts || []).length)}</strong><small>contracts</small></div>
    <div><strong>${esc(shortId(cxi.index_hash || ""))}</strong><small>hash</small></div>
  `;

  nodes.aiCallList.innerHTML = (aiCalls.items || []).map((a) => {
    const p = a.payload || {};
    return `<div class="row static"><span><strong>${esc(p.task_kind || p.job_type || "ai_call")}</strong><small>${esc(p.model_tier || "-")} / ${esc(p.model || "-")} / ${esc(p.elapsed_ms || 0)}ms</small></span><span class="chip ${p.ok ? "good" : "bad"}">${p.degraded ? "degraded" : p.ok ? "ok" : "fail"}</span></div>`;
  }).join("") || '<div class="empty">No AI calls yet</div>';

  nodes.waveList.innerHTML = (waves.items || []).map((w) =>
    `<div class="row static"><span><strong>${esc(w.wave_key)}</strong><small>seq ${esc(w.sequence)} / pkgs ${esc(w.payload?.package_count || "-")}</small></span><span class="chip ${statusClass(w.status)}">${esc(w.status)}</span></div>`
  ).join("") || '<div class="empty">No waves yet</div>';

  nodes.packageList.innerHTML = (packages.items || []).map((p) =>
    `<div class="row static"><span><strong>${esc(p.package_key)}</strong><small>${esc(p.payload?.subsystem || p.domain)} / ${esc(p.role)} / ${esc(p.wave_key)}</small></span><span class="chip ${statusClass(p.status)}">${esc(p.status)}</span></div>`
  ).join("") || '<div class="empty">No packages yet</div>';

  nodes.qualityGateList.innerHTML = (qr.gates || []).map((g) =>
    `<div class="row static"><span><strong>${esc(g.name)}</strong><small>${esc(g.severity)} / ${esc(JSON.stringify(g.details || {}).slice(0, 120))}</small></span><span class="chip ${g.ok ? "good" : "bad"}">${g.ok ? "pass" : "fail"}</span></div>`
  ).join("") || '<div class="empty">No quality report yet</div>';

  nodes.repairList.innerHTML = (repairs.items || []).map((a) =>
    `<div class="row static"><span><strong>${esc(a.payload?.failure_reason || "repair")}</strong><small>${esc(a.payload?.next_action || "-")}</small></span><span class="chip ${statusClass(a.payload?.status)}">${esc(a.payload?.status || "-")}</span></div>`
  ).join("") || '<div class="empty">No repairs</div>';

  nodes.jobList.innerHTML = (jobs.items || []).map((j) =>
    `<div class="row static"><span><strong>${esc(j.job_type)}</strong><small>${esc(j.role)} / ${esc(j.subsystem || "-")} / ${shortId(j.id)}</small></span><span class="chip ${statusClass(j.status)}">${esc(j.status)}</span></div>`
  ).join("") || '<div class="empty">No jobs yet</div>';

  nodes.contextIndex.textContent = JSON.stringify(contextIndex.snapshot || {}, null, 2);
  nodes.continuation.textContent = JSON.stringify(continuation.continuation, null, 2);

  nodes.artifactList.innerHTML = (artifacts.items || []).map((a) =>
    `<div class="row static"><span><strong>${esc(a.kind)}</strong><small>${esc(a.path || "-")}</small></span><span class="chip neutral">${esc(a.size || 0)}b</span></div>`
  ).join("") || '<div class="empty">No artifacts yet</div>';
}

// ─── Export ───

function renderDeliveryExport(current) {
  if (!nodes.exportForm) return;
  const ok = ["release_ready", "completed"].includes(String(current.status || "").toLowerCase());
  nodes.exportForm.classList.toggle("hidden", !current?.id);
  nodes.exportDelivery.disabled = !ok;
  nodes.exportDelivery.title = ok ? "Export deployment files to target directory" : "Available after release_ready or completed";
  if (!nodes.exportForm.elements.target_path.value && current?.metadata?.delivery_export_target) {
    nodes.exportForm.elements.target_path.value = current.metadata.delivery_export_target;
  }
  if (ok && !nodes.deliveryExportStatus.textContent) {
    setDeliveryExportStatus("Ready to export deployment files.", "neutral");
  } else if (!ok) {
    setDeliveryExportStatus("Export available after release is ready.", "live");
  }
}

async function exportDelivery(event) {
  event.preventDefault();
  if (!state.selectedRunId) return;
  const orig = nodes.exportDelivery?.textContent || "Export";
  if (nodes.exportDelivery) { nodes.exportDelivery.disabled = true; nodes.exportDelivery.textContent = "Exporting..."; }
  const f = new FormData(nodes.exportForm);
  const payload = {
    target_path: String(f.get("target_path") || "").trim(),
    overwrite: Boolean(f.get("overwrite")),
  };
  setDeliveryExportStatus("Copying deployment files...", "live");
  try {
    const result = await fetchJson(`${API_BASE}/runs/${state.selectedRunId}/export-delivery`, {
      method: "POST", body: JSON.stringify(payload),
    });
    const report = result.report || {};
    setDeliveryExportStatus(`Exported ${report.copied_count || 0} files to ${report.target_path || "-"}.`, "good");
    await renderRun(state.selectedRunId);
  } catch (error) {
    setDeliveryExportStatus(error.message, "bad");
  } finally {
    if (nodes.exportDelivery) { nodes.exportDelivery.disabled = false; nodes.exportDelivery.textContent = orig; }
  }
}

// ─── Event Listeners ───

nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const orig = nodes.createRun?.textContent || "Create and Run";
  if (nodes.createRun) { nodes.createRun.disabled = true; nodes.createRun.textContent = "Creating..."; }
  setCreateRunStatus("Creating project...", "live");
  const f = new FormData(nodes.form);
  const payload = {
    name: f.get("name") || "", title: f.get("title") || "",
    project_path: f.get("project_path") || "", description: f.get("description") || "",
    stack_pack: f.get("stack_pack") || "auto", target_scale: f.get("target_scale") || "auto",
    effective_loc_target: Number.parseInt(f.get("effective_loc_target") || "1000", 10),
  };
  try {
    const project = await fetchJson(`${API_BASE}/projects`, { method: "POST", body: JSON.stringify(payload) });
    setCreateRunStatus("Project created. Starting run...", "live");
    state.selectedProjectId = project.project?.id || project.id;
    const run = await fetchJson(`${API_BASE}/projects/${state.selectedProjectId}/runs`, {
      method: "POST", body: JSON.stringify({ requirements_text: payload.description }),
    });
    state.selectedRunId = run.run?.id || run.id;
    nodes.form.reset();
    setCreateRunStatus(`Run ${shortId(state.selectedRunId)} queued.`, "good");
    await refreshAll();
  } catch (error) {
    nodes.health.textContent = error.message; nodes.health.className = "pill bad";
    setCreateRunStatus(error.message, "bad");
  } finally {
    if (nodes.createRun) { nodes.createRun.disabled = false; nodes.createRun.textContent = orig; }
  }
});

nodes.refresh.addEventListener("click", refreshAll);
nodes.selectAllProjects.addEventListener("change", () => {
  if (nodes.selectAllProjects.checked) { state.projects.forEach((p) => state.selectedProjectIds.add(p.id)); }
  else { state.selectedProjectIds.clear(); }
  renderProjects();
});
nodes.deleteSelectedProjects.addEventListener("click", () => {
  deleteSelectedProjects().catch((err) => { nodes.health.textContent = err.message; nodes.health.className = "pill bad"; });
});
nodes.exportForm?.addEventListener("submit", exportDelivery);
nodes.applyRecommendedModel?.addEventListener("click", applyRecommendedModelSettings);
nodes.testModelSettings?.addEventListener("click", testModelSettings);
nodes.modelSettingsForm?.addEventListener("input", () => { state.modelSettingsDirty = true; });
nodes.modelSettingsForm?.addEventListener("submit", saveModelSettings);

refreshAll().catch((err) => { nodes.health.textContent = err.message; nodes.health.className = "pill bad"; });
setInterval(() => refreshAll().catch(() => {}), 5000);
