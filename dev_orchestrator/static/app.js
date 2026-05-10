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

let API_BASE = "/api/v6";
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
  v6Summary: $("v6-summary"),
  v6Mission: $("v6-mission"),
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
  const pathText = String(path || "");
  let candidate = pathText;
  if (pathText.startsWith("/api/")) {
    candidate = pathText;
  } else if (pathText.startsWith("/")) {
    candidate = `${API_BASE}${pathText}`;
  }
  const response = await fetch(candidate, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "-";
}

function statusClass(value) {
  const item = String(value || "").toLowerCase();
  if (["completed", "release_ready", "go", "passed", "running", "ok", "pass"].includes(item)) return "good";
  if (["no_go", "dead_letter", "blocked", "failed", "rollback_failed", "fail", "blocked_for_human_review"].includes(item)) return "bad";
  if (["queued", "retry", "paused", "leased", "recovering", "retry_ai_call"].includes(item)) return "live";
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
  if (className) {
    button.className = className;
  }
  if (title) {
    button.title = title;
  }
  button.disabled = disabled;
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) {
      return;
    }
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Working...";
    try {
      await onClick();
    } catch (error) {
      nodes.health.textContent = error.message;
      nodes.health.className = "pill bad";
    } finally {
      button.textContent = originalLabel;
      button.disabled = disabled;
    }
  });
  return button;
}

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

  const meta = document.createElement("div");
  meta.className = "action-meta";
  meta.innerHTML = `
    <span class="chip ${statusClass(current.status)}">${esc(current.status || "-")}</span>
    <span class="chip neutral">checkpoint ${esc(current.checkpoint || "-")}</span>
    <span class="chip ${statusClass(nextAction)}">next ${esc(nextAction)}</span>
    <span class="chip ${statusClass(releaseStatus)}">release ${esc(releaseStatus)}</span>
  `;

  const buttons = document.createElement("div");
  buttons.className = "action-buttons";
  buttons.appendChild(createActionButton("Apply release", {
    className: "primary",
    disabled: !canApply,
    title: releaseCandidate?.id ? `Apply release candidate ${shortId(releaseCandidate.id)}` : "No release candidate yet",
    onClick: async () => {
      if (!releaseCandidate?.id) return;
      await fetchJson(`${API_BASE}/release-candidates/${releaseCandidate.id}/apply`, { method: "POST" });
      await refreshAll();
    },
  }));
  buttons.appendChild(createActionButton("Rollback", {
    className: "danger subtle",
    disabled: !canRollback,
    title: releaseCandidate?.id ? `Rollback release candidate ${shortId(releaseCandidate.id)}` : "No release candidate yet",
    onClick: async () => {
      if (!releaseCandidate?.id) return;
      await fetchJson(`${API_BASE}/release-candidates/${releaseCandidate.id}/rollback`, { method: "POST" });
      await refreshAll();
    },
  }));
  buttons.appendChild(createActionButton("Repair", {
    className: "subtle",
    disabled: !canRepair,
    title: "Create a repair job for this run",
    onClick: async () => {
      await fetchJson(`${API_BASE}/runs/${current.id}/repair`, { method: "POST" });
      await refreshAll();
    },
  }));
  buttons.appendChild(createActionButton("Requeue blocked", {
    className: "subtle",
    disabled: !canRequeue,
    title: "Move a blocked run back to queued",
    onClick: async () => {
      await fetchJson(`${API_BASE}/runs/${current.id}/requeue-blocked`, { method: "POST" });
      await refreshAll();
    },
  }));
  buttons.appendChild(createActionButton("Pause", {
    className: "subtle",
    disabled: !canPause,
    title: "Pause at the next job boundary",
    onClick: async () => {
      await fetchJson(`${API_BASE}/runs/${current.id}/pause`, { method: "POST" });
      await refreshAll();
    },
  }));
  buttons.appendChild(createActionButton("Resume", {
    className: "subtle",
    disabled: !canResume,
    title: "Resume a paused run",
    onClick: async () => {
      await fetchJson(`${API_BASE}/runs/${current.id}/resume`, { method: "POST" });
      await refreshAll();
    },
  }));
  buttons.appendChild(createActionButton("Refresh run", {
    className: "subtle",
    title: "Refresh this run panel",
    onClick: async () => {
      await renderRun(current.id);
    },
  }));

  const note = document.createElement("div");
  note.className = "action-note";
  note.textContent = releaseCandidate?.id
    ? `Release candidate ${shortId(releaseCandidate.id)} is the manual trigger point. Apply starts the release job; rollback reverts the applied candidate.`
    : "This run has no release candidate yet. Wait for quality and release candidate generation, or use Repair if the run is blocked.";

  nodes.runActions.append(meta, buttons, note);
}

async function refreshAll() {
  const [health, projects, workers, modelSettings] = await Promise.all([
    fetchJson(`${API_BASE}/health`),
    fetchJson(`${API_BASE}/projects`),
    fetchJson(`${API_BASE}/workers?limit=12`),
    fetchJson(`${API_BASE}/model-settings`).catch(() => ({ llm: null, recommended: recommendedModelSettings })),
  ]);
  nodes.health.textContent = `${health.status} / ${health.kernel}`;
  nodes.health.className = "pill good";
  state.projects = projects.items || [];
  state.workers = workers.items || [];
  state.modelSettings = modelSettings;
  renderModelSettings(modelSettings);
  state.selectedProjectIds = new Set([...state.selectedProjectIds].filter((id) => state.projects.some((project) => project.id === id)));
  if (state.selectedProjectId && !state.projects.some((project) => project.id === state.selectedProjectId)) {
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
  if (nodes.exportForm) {
    nodes.exportForm.classList.add("hidden");
  }
  setDeliveryExportStatus("");
  nodes.v6Summary.innerHTML = "";
  if (nodes.v6Mission) nodes.v6Mission.innerHTML = "";
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
  if (!project) {
    clearDetailView();
    return;
  }
  const runs = state.selectedProjectRuns || [];
  const latestRun = runs[0];
  const technologyGuidance = project.config?.stack_pack || "AI-decided";
  const targetScale = project.config?.target_scale || "-";
  const projectPath = project.resolved_project_root || project.project_path_status?.project_root || project.project_path || "-";
  nodes.runTitle.textContent = `${project.title || project.name} - Project view`;
  nodes.runSummary.innerHTML = `
    <div><strong>${esc(project.status || "-")}</strong><small>project status</small></div>
    <div><strong>${esc(technologyGuidance)}</strong><small>technology guidance</small></div>
    <div><strong>${esc(targetScale)}</strong><small>target scale</small></div>
  `;
  nodes.runActions.innerHTML = '<div class="empty">No run selected</div>';
  if (nodes.exportForm) {
    nodes.exportForm.classList.add("hidden");
  }
  setDeliveryExportStatus("");
  nodes.v6Summary.innerHTML = `
    <div><strong>${esc(projectPath)}</strong><small>project path</small></div>
    <div><strong>${esc(runs.length)}</strong><small>run count</small></div>
    <div><strong>${esc(latestRun?.status || "none")}</strong><small>latest run</small></div>
    <div><strong>${esc(latestRun?.checkpoint || "-")}</strong><small>latest checkpoint</small></div>
  `;
  if (nodes.v6Mission) {
    nodes.v6Mission.innerHTML = `
      <div><strong>${esc(project.scale_profile?.name || project.config?.target_scale || "-")}</strong><small>scale profile</small></div>
      <div><strong>${esc(project.scale_profile?.kernel_generation || project.config?.kernel_generation || "-")}</strong><small>kernel</small></div>
      <div><strong>${esc(project.scale_profile?.wave_parallelism || "-")}</strong><small>wave parallelism</small></div>
      <div><strong>${esc(project.scale_profile?.recovery_policy || "-")}</strong><small>recovery policy</small></div>
    `;
  }
  nodes.contractValidation.innerHTML = '<div class="empty">No run selected</div>';
  nodes.patchTransactions.innerHTML = '<div class="empty">No run selected</div>';
  nodes.testExecution.innerHTML = '<div class="empty">No run selected</div>';
  nodes.codeContractIndex.innerHTML = '<div class="empty">No run selected</div>';
  nodes.aiCallList.innerHTML = '<div class="empty">No run selected</div>';
  nodes.waveList.innerHTML = '<div class="empty">No run selected</div>';
  nodes.packageList.innerHTML = '<div class="empty">No run selected</div>';
  nodes.qualityGateList.innerHTML = '<div class="empty">No run selected</div>';
  nodes.repairList.innerHTML = '<div class="empty">No run selected</div>';
  nodes.jobList.innerHTML = '<div class="empty">No run selected</div>';
  nodes.contextIndex.textContent = JSON.stringify(
    {
      project_id: project.id,
      project_name: project.name,
      project_title: project.title,
      latest_run_id: latestRun?.id || "",
      latest_run_status: latestRun?.status || "none",
    },
    null,
    2,
  );
  nodes.continuation.textContent = JSON.stringify(
    {
      project_view: true,
      project_id: project.id,
      latest_run_id: latestRun?.id || "",
      latest_run_status: latestRun?.status || "none",
      latest_run_checkpoint: latestRun?.checkpoint || "",
      next_action: latestRun?.id ? "open_latest_run" : "create_new_run",
    },
    null,
    2,
  );
  nodes.artifactList.innerHTML = '<div class="empty">No run selected</div>';
}

function renderProjects() {
  nodes.projectList.innerHTML = "";
  const selectedCount = state.selectedProjectIds.size;
  nodes.deleteSelectedProjects.disabled = selectedCount === 0;
  nodes.deleteSelectedProjects.textContent = selectedCount ? `Delete selected (${selectedCount})` : "Delete selected";
  nodes.selectAllProjects.checked = state.projects.length > 0 && state.projects.every((project) => state.selectedProjectIds.has(project.id));
  nodes.selectAllProjects.indeterminate = selectedCount > 0 && selectedCount < state.projects.length;
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
    const projectCheck = row.querySelector(".project-check");
    const projectRun = row.querySelector(".project-run");
    const syncSelection = () => {
      if (projectCheck.checked) {
        state.selectedProjectIds.add(project.id);
      } else {
        state.selectedProjectIds.delete(project.id);
      }
      renderProjects();
    };
    projectCheck.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    projectCheck.addEventListener("change", (event) => {
      event.stopPropagation();
      syncSelection();
    });
    const openProject = async () => {
      const [projectPayload, runsPayload] = await Promise.all([
        fetchJson(`${API_BASE}/projects/${project.id}`),
        fetchJson(`${API_BASE}/projects/${project.id}/runs`).catch(() => ({ items: [] })),
      ]);
      state.selectedProjectId = project.id;
      state.selectedProject = projectPayload.project || project;
      state.selectedProjectRuns = runsPayload.items || [];
      state.selectedRunId = state.selectedProjectRuns[0]?.id || "";
      await refreshAll();
    };
    const runProject = async () => {
      const run = await fetchJson(`${API_BASE}/projects/${project.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ requirements_text: project.description || project.title || project.name }),
      });
      state.selectedProjectId = project.id;
      state.selectedProject = project;
      state.selectedProjectRuns = [run.run, ...(state.selectedProjectRuns || []).filter((item) => item.id !== run.run.id)];
      state.selectedRunId = run.run.id;
      await refreshAll();
    };
    row.addEventListener("click", (event) => {
      if (event.target.closest("input, button, select, textarea, label")) {
        return;
      }
      openProject();
    });
    row.addEventListener("keydown", (event) => {
      if (event.target.closest("input, button, select, textarea, label")) {
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openProject();
      }
    });
    projectRun.addEventListener("click", (event) => {
      event.stopPropagation();
      runProject().catch((error) => {
        nodes.health.textContent = error.message;
        nodes.health.className = "pill bad";
      });
    });
    nodes.projectList.appendChild(row);
  }
}

async function deleteSelectedProjects() {
  const projectIds = [...state.selectedProjectIds];
  if (!projectIds.length) return;
  const names = state.projects
    .filter((project) => state.selectedProjectIds.has(project.id))
    .map((project) => project.title || project.name)
    .slice(0, 5)
    .join(", ");
  if (!window.confirm(`Delete ${projectIds.length} project(s) from the control plane?\n\n${names}`)) {
    return;
  }
  await fetchJson(`${API_BASE}/projects/batch-delete`, {
    method: "POST",
    body: JSON.stringify({ project_ids: projectIds }),
  });
  if (state.selectedProjectId && state.selectedProjectIds.has(state.selectedProjectId)) {
    state.selectedProjectId = "";
    state.selectedRunId = "";
    state.selectedProject = null;
    state.selectedProjectRuns = [];
    nodes.runTitle.textContent = "Select a Project";
    clearDetailView();
  }
  state.selectedProjectIds.clear();
  await refreshAll();
}

function providerNameFromProfile(profile) {
  const value = String(profile || "").trim();
  if (value.startsWith("custom-")) return "custom";
  return value || "custom";
}

function recommendedFromPayload(payload) {
  return payload?.recommended || recommendedModelSettings;
}

function modelProviderFromSettings(settings) {
  const llm = settings?.llm || {};
  const recommended = recommendedFromPayload(settings);
  return {
    model_provider: providerNameFromProfile(llm.provider_profile) || recommended.model_provider,
    model: llm.model || recommended.model,
    model_reasoning_effort: llm.model_reasoning_effort || recommended.model_reasoning_effort,
    disable_response_storage: Boolean(llm.disable_response_storage ?? recommended.disable_response_storage),
    model_providers: {
      custom: {
        name: "custom",
        wire_api: llm.wire_api || recommended.model_providers.custom.wire_api,
        requires_openai_auth: (llm.auth_header || "Authorization") === "Authorization",
        base_url: llm.api_base || recommended.model_providers.custom.base_url,
      },
    },
  };
}

function fillModelSettingsForm(settings) {
  if (!nodes.modelSettingsForm) return;
  const values = modelProviderFromSettings(settings);
  const form = nodes.modelSettingsForm;
  form.elements.model_provider.value = values.model_provider;
  form.elements.base_url.value = values.model_providers.custom.base_url;
  form.elements.model.value = values.model;
  form.elements.wire_api.value = values.model_providers.custom.wire_api;
  form.elements.model_reasoning_effort.value = values.model_reasoning_effort;
  form.elements.disable_response_storage.checked = values.disable_response_storage;
  form.elements.requires_openai_auth.checked = Boolean(values.model_providers.custom.requires_openai_auth);
  form.elements.api_key.value = "";
}

function renderModelSettings(settings, { force = false } = {}) {
  if (!nodes.modelSettingsStatus) return;
  const formActive = nodes.modelSettingsForm?.contains(document.activeElement);
  if (force || (!state.modelSettingsDirty && !formActive)) {
    fillModelSettingsForm(settings);
  }
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
  const form = new FormData(nodes.modelSettingsForm);
  const provider = String(form.get("model_provider") || "custom").trim();
  const wireApi = String(form.get("wire_api") || "responses").trim();
  const payload = {
    model_provider: provider,
    model: String(form.get("model") || "").trim(),
    model_reasoning_effort: String(form.get("model_reasoning_effort") || "").trim(),
    disable_response_storage: Boolean(form.get("disable_response_storage")),
    api_key: String(form.get("api_key") || ""),
    model_providers: {
      [provider]: {
        name: provider,
        wire_api: wireApi,
        requires_openai_auth: Boolean(form.get("requires_openai_auth")),
        base_url: String(form.get("base_url") || "").trim(),
      },
    },
  };
  return payload;
}

async function saveModelSettings(event) {
  event.preventDefault();
  const originalLabel = nodes.saveModelSettings?.textContent || "Save settings";
  if (nodes.saveModelSettings) {
    nodes.saveModelSettings.disabled = true;
    nodes.saveModelSettings.textContent = "Saving...";
  }
  try {
    const payload = buildModelSettingsPayloadFromForm();
    const result = await fetchJson(`${API_BASE}/model-settings`, { method: "PUT", body: JSON.stringify(payload) });
    state.modelSettings = result;
    state.modelSettingsDirty = false;
    renderModelSettings(result, { force: true });
    nodes.modelSettingsStatus.insertAdjacentHTML("beforeend", '<span class="chip good">saved</span>');
  } catch (error) {
    nodes.modelSettingsStatus.innerHTML = `<span class="chip bad">${esc(error.message)}</span>`;
  } finally {
    if (nodes.saveModelSettings) {
      nodes.saveModelSettings.disabled = false;
      nodes.saveModelSettings.textContent = originalLabel;
    }
  }
}

async function testModelSettings() {
  const originalLabel = nodes.testModelSettings?.textContent || "Test API";
  if (nodes.testModelSettings) {
    nodes.testModelSettings.disabled = true;
    nodes.testModelSettings.textContent = "Testing...";
  }
  nodes.modelSettingsStatus.innerHTML = '<span class="chip live">testing connection</span>';
  try {
    const payload = buildModelSettingsPayloadFromForm();
    const result = await fetchJson(`${API_BASE}/model-settings/test`, { method: "POST", body: JSON.stringify(payload) });
    state.modelSettings = result;
    state.modelSettingsDirty = false;
    renderModelSettings(result, { force: true });
    const probe = result.result || {};
    const tone = result.ok ? "good" : "bad";
    const message = result.ok ? "connection ok" : (probe.error || "connection failed");
    nodes.modelSettingsStatus.insertAdjacentHTML("beforeend", `<span class="chip ${tone}">${esc(message)}</span>`);
  } catch (error) {
    nodes.modelSettingsStatus.innerHTML = `<span class="chip bad">${esc(error.message)}</span>`;
  } finally {
    if (nodes.testModelSettings) {
      nodes.testModelSettings.disabled = false;
      nodes.testModelSettings.textContent = originalLabel;
    }
  }
}

function renderWorkers() {
  nodes.workerList.innerHTML = "";
  if (!state.workers.length) {
    nodes.workerList.innerHTML = '<div class="empty">No worker heartbeat yet</div>';
    return;
  }
  for (const worker of state.workers) {
    const row = document.createElement("div");
    row.className = "row static";
    row.innerHTML = `
      <span>
        <strong>${esc(worker.role || worker.worker_id)}</strong>
        <small>pid ${esc(worker.pid || "-")} / ${esc(worker.last_heartbeat || "-")}</small>
      </span>
      <span class="chip ${statusClass(worker.status)}">${esc(worker.status)}</span>
    `;
    nodes.workerList.appendChild(row);
  }
}

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
    fetchJson(`/api/v6/runs/${runId}/mission-state`).catch(() => ({ mission_state: null })),
  ]);
  const current = run.run;
  const qualityReport = quality.report || {};
  const releaseCandidate = latestArtifactByKind(artifacts.items || [], "release_candidate");
  const currentWave = continuation.continuation?.current_wave || "-";
  const effectiveLoc = qualityReport.effective_loc?.total ?? current.metadata?.effective_loc_metrics?.total ?? 0;
  const targetLoc = current.metadata?.project_config?.effective_loc_target || "-";
  const failedGates = (qualityReport.gates || []).filter((gate) => !gate.ok);

  nodes.runTitle.textContent = `Run ${shortId(current.id)}`;
  nodes.runSummary.innerHTML = `
    <div><strong>${esc(current.status)}</strong><small>status</small></div>
    <div><strong>${esc(current.checkpoint)}</strong><small>checkpoint</small></div>
    <div><strong>${esc(layout.layout?.delivery_root || current.metadata?.project_layout?.delivery_root || "-")}</strong><small>delivery root</small></div>
  `;
  renderRunActions(current, continuation, artifacts);
  renderDeliveryExport(current);
  nodes.v6Summary.innerHTML = `
    <div><strong>${esc(currentWave)}</strong><small>current wave</small></div>
    <div><strong>${esc(effectiveLoc)} / ${esc(targetLoc)}</strong><small>effective LOC</small></div>
    <div><strong>${esc(aiCalls.items?.length || 0)}</strong><small>agent runs</small></div>
    <div><strong>${esc(patchSets.items?.length || 0)}</strong><small>patch sets</small></div>
  `;
  const missionState = mission.mission_state || {};
  const profile = missionState.scale_profile || current.metadata?.scale_profile || {};
  const recovery = missionState.recovery || {};
  const provider = missionState.provider_health || {};
  if (nodes.v6Mission) {
    nodes.v6Mission.innerHTML = `
      <div><strong>${esc(profile.name || "-")}</strong><small>scale profile</small></div>
      <div><strong>${esc(profile.wave_parallelism || "-")}</strong><small>wave width</small></div>
      <div><strong class="${statusClass(missionState.next_action)}">${esc(missionState.next_action || "-")}</strong><small>mission next</small></div>
      <div><strong class="${recovery.dead_letter_count ? "bad" : "good"}">${esc(recovery.dead_letter_count || 0)}</strong><small>dead letters</small></div>
      <div><strong>${esc(recovery.retryable_provider_failures || 0)}</strong><small>retryable provider failures</small></div>
      <div><strong class="${provider.degraded_count ? "bad" : "good"}">${esc(provider.degraded_count || 0)}</strong><small>degraded providers</small></div>
      <div><strong>${esc(shortId(missionState.context?.mission_memory_hash || ""))}</strong><small>memory hash</small></div>
      <div><strong>${esc(profile.recovery_policy || "-")}</strong><small>recovery policy</small></div>
    `;
  }
  const contractPayload = contractReport.report || {};
  const patchPayload = patchTransactions.report || {};
  const testPayload = testExecution.report || {};
  const codePayload = codeIndex.index || {};
  const contractIndexPayload = contractIndex.index || {};
  nodes.contractValidation.innerHTML = `
    <div><strong class="${contractPayload.ok ? "good" : "bad"}">${esc(contractPayload.status || "pending")}</strong><small>status</small></div>
    <div><strong>${esc(contractPayload.validation_count || 0)}</strong><small>validated calls</small></div>
    <div><strong>${esc(contractPayload.violation_count || 0)}</strong><small>violations</small></div>
    <div><strong>${esc(shortId(contractPayload.run_id || current.id))}</strong><small>run</small></div>
  `;
  nodes.patchTransactions.innerHTML = `
    <div><strong class="${patchPayload.ok !== false ? "good" : "bad"}">${esc(patchPayload.transaction_count || patchTransactions.items?.length || 0)}</strong><small>transactions</small></div>
    <div><strong>${esc(patchPayload.changed_file_count || 0)}</strong><small>changed files</small></div>
    <div><strong class="${(patchPayload.conflict_count || patchTransactions.conflicts?.length || 0) ? "bad" : "good"}">${esc(patchPayload.conflict_count || patchTransactions.conflicts?.length || 0)}</strong><small>conflicts</small></div>
    <div><strong>${esc(patchTransactions.items?.[patchTransactions.items.length - 1]?.payload?.transaction_id ? shortId(patchTransactions.items[patchTransactions.items.length - 1].payload.transaction_id) : "-")}</strong><small>latest transaction</small></div>
  `;
  nodes.testExecution.innerHTML = `
    <div><strong class="${testPayload.ok ? "good" : "bad"}">${esc(testPayload.status || "pending")}</strong><small>status</small></div>
    <div><strong>${esc(testPayload.executed_count || 0)} / ${esc(testPayload.command_count || 0)}</strong><small>executed</small></div>
    <div><strong>${esc(testPayload.source || "-")}</strong><small>source</small></div>
    <div><strong>${esc((testPayload.safety_failures || []).length)}</strong><small>safety blocks</small></div>
  `;
  nodes.codeContractIndex.innerHTML = `
    <div><strong>${esc((codePayload.files || []).length)}</strong><small>indexed files</small></div>
    <div><strong>${esc(shortId(codePayload.index_hash || ""))}</strong><small>code hash</small></div>
    <div><strong>${esc((contractIndexPayload.contracts || []).length)}</strong><small>contracts</small></div>
    <div><strong>${esc(shortId(contractIndexPayload.index_hash || ""))}</strong><small>contract hash</small></div>
  `;
  nodes.aiCallList.innerHTML = (aiCalls.items || []).map((artifact) => {
    const payload = artifact.payload || {};
    return `
      <div class="row static">
        <span>
          <strong>${esc(payload.task_kind || payload.job_type || "ai_call")}</strong>
          <small>${esc(payload.model_tier || "-")} / ${esc(payload.model || "-")} / ${esc(payload.elapsed_ms || 0)}ms</small>
        </span>
        <span class="chip ${payload.ok ? "good" : "bad"}">${payload.degraded ? "degraded" : payload.ok ? "ok" : "failed"}</span>
      </div>
    `;
  }).join("") || '<div class="empty">No AI calls yet</div>';
  nodes.waveList.innerHTML = (waves.items || []).map((wave) => `
    <div class="row static">
      <span><strong>${esc(wave.wave_key)}</strong><small>sequence ${esc(wave.sequence)} / packages ${esc(wave.payload?.package_count || "-")}</small></span>
      <span class="chip ${statusClass(wave.status)}">${esc(wave.status)}</span>
    </div>
  `).join("") || '<div class="empty">No waves yet</div>';
  nodes.packageList.innerHTML = (packages.items || []).map((pkg) => `
    <div class="row static">
      <span><strong>${esc(pkg.package_key)}</strong><small>${esc(pkg.payload?.subsystem || pkg.domain)} / ${esc(pkg.role)} / ${esc(pkg.wave_key)}</small></span>
      <span class="chip ${statusClass(pkg.status)}">${esc(pkg.status)}</span>
    </div>
  `).join("") || '<div class="empty">No packages yet</div>';
  nodes.qualityGateList.innerHTML = (qualityReport.gates || []).map((gate) => `
    <div class="row static">
      <span><strong>${esc(gate.name)}</strong><small>${esc(gate.severity)} / ${esc(JSON.stringify(gate.details || {}).slice(0, 160))}</small></span>
      <span class="chip ${gate.ok ? "good" : "bad"}">${gate.ok ? "pass" : "fail"}</span>
    </div>
  `).join("") || '<div class="empty">No quality report yet</div>';
  nodes.repairList.innerHTML = (repairs.items || []).map((artifact) => `
    <div class="row static">
      <span><strong>${esc(artifact.payload?.failure_reason || "repair")}</strong><small>${esc(artifact.payload?.next_action || "-")}</small></span>
      <span class="chip ${statusClass(artifact.payload?.status)}">${esc(artifact.payload?.status || "-")}</span>
    </div>
  `).join("") || '<div class="empty">No repair history</div>';
  nodes.jobList.innerHTML = (jobs.items || []).map((job) => `
    <div class="row static">
      <span><strong>${esc(job.job_type)}</strong><small>${esc(job.role)} / ${esc(job.subsystem || "-")} / ${shortId(job.id)}</small></span>
      <span class="chip ${statusClass(job.status)}">${esc(job.status)}</span>
    </div>
  `).join("") || '<div class="empty">No jobs yet</div>';
  nodes.contextIndex.textContent = JSON.stringify(contextIndex.snapshot || {}, null, 2);
  nodes.continuation.textContent = JSON.stringify(continuation.continuation, null, 2);
  nodes.artifactList.innerHTML = (artifacts.items || []).map((artifact) => `
    <div class="row static">
      <span><strong>${esc(artifact.kind)}</strong><small>${esc(artifact.path || "-")}</small></span>
      <span class="chip neutral">${esc(artifact.size || 0)}b</span>
    </div>
  `).join("") || '<div class="empty">No artifacts yet</div>';
}

function renderDeliveryExport(current) {
  if (!nodes.exportForm) return;
  const exportable = ["release_ready", "completed"].includes(String(current.status || "").toLowerCase());
  nodes.exportForm.classList.toggle("hidden", !current?.id);
  nodes.exportDelivery.disabled = !exportable;
  nodes.exportDelivery.title = exportable ? "Copy runnable delivery files to the selected folder" : "Available after the run reaches release_ready or completed";
  if (!nodes.exportForm.elements.target_path.value && current?.metadata?.delivery_export_target) {
    nodes.exportForm.elements.target_path.value = current.metadata.delivery_export_target;
  }
  if (exportable && !nodes.deliveryExportStatus.textContent) {
    setDeliveryExportStatus("Ready to export runnable project files.", "neutral");
  } else if (!exportable) {
    setDeliveryExportStatus("Export is available after release is ready.", "live");
  }
}

async function exportDelivery(event) {
  event.preventDefault();
  if (!state.selectedRunId) return;
  const originalLabel = nodes.exportDelivery?.textContent || "Export delivery";
  if (nodes.exportDelivery) {
    nodes.exportDelivery.disabled = true;
    nodes.exportDelivery.textContent = "Exporting...";
  }
  const form = new FormData(nodes.exportForm);
  const payload = {
    target_path: String(form.get("target_path") || "").trim(),
    overwrite: Boolean(form.get("overwrite")),
  };
  setDeliveryExportStatus("Copying runnable files...", "live");
  try {
    const result = await fetchJson(`${API_BASE}/runs/${state.selectedRunId}/export-delivery`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const report = result.report || {};
    setDeliveryExportStatus(`Exported ${report.copied_count || 0} files to ${report.target_path || "-"}.`, "good");
    await renderRun(state.selectedRunId);
  } catch (error) {
    setDeliveryExportStatus(error.message, "bad");
  } finally {
    if (nodes.exportDelivery) {
      nodes.exportDelivery.disabled = false;
      nodes.exportDelivery.textContent = originalLabel;
    }
  }
}

nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const originalLabel = nodes.createRun?.textContent || "Create and Run";
  if (nodes.createRun) {
    nodes.createRun.disabled = true;
    nodes.createRun.textContent = "Creating...";
  }
  setCreateRunStatus("Creating project...", "live");
  const form = new FormData(nodes.form);
  const payload = {
    name: form.get("name") || "",
    title: form.get("title") || "",
    project_path: form.get("project_path") || "",
    description: form.get("description") || "",
    stack_pack: form.get("stack_pack") || "auto",
    target_scale: form.get("target_scale") || "auto",
    effective_loc_target: Number.parseInt(form.get("effective_loc_target") || "1000", 10),
  };
  try {
    const project = await fetchJson(`${API_BASE}/projects`, { method: "POST", body: JSON.stringify(payload) });
    setCreateRunStatus("Project created. Starting run...", "live");
    state.selectedProjectId = project.project.id;
    const run = await fetchJson(`${API_BASE}/projects/${project.project.id}/runs`, {
      method: "POST",
      body: JSON.stringify({ requirements_text: payload.description }),
    });
    state.selectedRunId = run.run.id;
    nodes.form.reset();
    setCreateRunStatus(`Run ${shortId(run.run.id)} queued.`, "good");
    await refreshAll();
  } catch (error) {
    nodes.health.textContent = error.message;
    nodes.health.className = "pill bad";
    setCreateRunStatus(error.message, "bad");
  } finally {
    if (nodes.createRun) {
      nodes.createRun.disabled = false;
      nodes.createRun.textContent = originalLabel;
    }
  }
});

nodes.refresh.addEventListener("click", refreshAll);
nodes.selectAllProjects.addEventListener("change", () => {
  if (nodes.selectAllProjects.checked) {
    state.projects.forEach((project) => state.selectedProjectIds.add(project.id));
  } else {
    state.selectedProjectIds.clear();
  }
  renderProjects();
});
nodes.deleteSelectedProjects.addEventListener("click", () => {
  deleteSelectedProjects().catch((error) => {
    nodes.health.textContent = error.message;
    nodes.health.className = "pill bad";
  });
});
nodes.exportForm?.addEventListener("submit", exportDelivery);
nodes.applyRecommendedModel?.addEventListener("click", applyRecommendedModelSettings);
nodes.testModelSettings?.addEventListener("click", testModelSettings);
nodes.modelSettingsForm?.addEventListener("input", () => {
  state.modelSettingsDirty = true;
});
nodes.modelSettingsForm?.addEventListener("submit", saveModelSettings);
refreshAll().catch((error) => {
  nodes.health.textContent = error.message;
  nodes.health.className = "pill bad";
});
setInterval(() => refreshAll().catch(() => {}), 5000);
