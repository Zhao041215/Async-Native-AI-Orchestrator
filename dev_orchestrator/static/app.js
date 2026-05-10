/* Orchestrator V8 — Frontend application (V8 API only) */
const API_BASE = "/api/v8";

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
  healthPill:             $("health-pill"),
  schemaPill:             $("schema-pill"),
  refresh:                $("refresh"),
  workerCount:            $("worker-count"),
  createRun:              $("create-run"),
  createRunStatus:        $("create-run-status"),
  form:                   $("project-form"),
  selectAll:              $("select-all-projects"),
  deleteSelected:         $("delete-selected-projects"),
  projectList:            $("project-list"),
  // detail
  runTitle:               $("run-title"),
  runBadgeStrip:          $("run-badge-strip"),
  runSummary:             $("run-summary"),
  runActions:             $("run-actions"),
  exportForm:             $("delivery-export-form"),
  exportDelivery:         $("export-delivery"),
  exportStatus:           $("delivery-export-status"),
  missionSummary:         $("mission-summary"),
  missionBlocker:         $("mission-blocker"),
  missionKernel:          $("mission-kernel"),
  executionPlan:          $("execution-plan"),
  implementationPlan:     $("implementation-plan"),
  stabilityReport:        $("stability-report"),
  frontendQuality:        $("frontend-quality"),
  contractValidation:     $("contract-validation"),
  patchTransactions:      $("patch-transactions"),
  testExecution:          $("test-execution"),
  codeContractIndex:      $("code-contract-index"),
  aiCallList:             $("ai-call-list"),
  waveList:               $("wave-list"),
  packageList:            $("package-list"),
  qualityGateList:        $("quality-gate-list"),
  repairList:             $("repair-list"),
  jobList:                $("job-list"),
  artifactList:           $("artifact-list"),
  contextIndex:           $("context-index"),
  continuation:           $("continuation"),
  workerList:             $("worker-list"),
  // model settings
  modelSettingsForm:      $("model-settings-form"),
  applyRecommended:       $("apply-recommended-model"),
  testModelSettings:      $("test-model-settings"),
  saveModelSettings:      $("save-model-settings"),
  modelSettingsStatus:    $("model-settings-status"),
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

// ─── HTTP ──────────────────────────────────────────────────────────

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

// ─── Utilities ─────────────────────────────────────────────────────

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function shortId(value) { return value ? String(value).slice(0, 8) : "—"; }

function statusClass(value) {
  const s = String(value || "").toLowerCase();
  if (["completed","release_ready","go","passed","running","ok","pass"].includes(s)) return "good";
  if (["no_go","dead_letter","blocked","failed","rollback_failed","fail","blocked_for_human_review"].includes(s)) return "bad";
  if (["queued","retry","paused","leased","recovering","retry_ai_call"].includes(s)) return "live";
  return "neutral";
}

function chip(cls, text) {
  return `<span class="chip ${cls}">${esc(text)}</span>`;
}

function statCell(value, label, cls = "") {
  return `<div class="stat-cell"><strong class="${cls}">${esc(value)}</strong><small>${esc(label)}</small></div>`;
}

function latestArtifactByKind(items, kind) {
  const matches = (items || []).filter((i) => i.kind === kind);
  return matches[matches.length - 1] || null;
}

function setFeedback(node, message, tone = "neutral") {
  if (!node) return;
  node.textContent = message || "";
  node.className = `form-feedback ${tone}`;
}

function clearNode(node, html = "") {
  if (node) node.innerHTML = html;
}

// ─── Action button helper ───────────────────────────────────────────

function makeButton(label, { cls = "", disabled = false, title = "", onClick }) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = label;
  if (cls) btn.className = cls;
  if (title) btn.title = title;
  btn.disabled = disabled;
  btn.addEventListener("click", async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (btn.disabled) return;
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try { await onClick(); }
    catch (err) { setHealthError(err.message); }
    finally { btn.textContent = orig; btn.disabled = disabled; }
  });
  return btn;
}

function setHealthError(msg) {
  if (nodes.healthPill) {
    nodes.healthPill.textContent = msg;
    nodes.healthPill.className = "status-pill fail";
  }
}

// ─── Run Actions ────────────────────────────────────────────────────

function renderRunActions(current, continuation, artifacts) {
  if (!nodes.runActions) return;
  nodes.runActions.innerHTML = "";
  if (!current) {
    nodes.runActions.innerHTML = '<div class="action-meta"><span class="chip neutral">No run selected</span></div>';
    return;
  }

  const rc = latestArtifactByKind(artifacts.items || [], "release_candidate");
  const nextAction = continuation.continuation?.next_action || current.continuation?.next_action || "—";
  const releaseStatus = continuation.continuation?.release_status || current.continuation?.release_status || "—";
  const canApply    = Boolean(rc?.id) && (current.status === "release_ready" || nextAction === "apply" || releaseStatus === "GO");
  const canRollback = Boolean(rc?.id) && (current.status === "completed" || releaseStatus === "applied" || nextAction === "delivery_complete");
  const canRepair   = ["blocked","no_go","blocked_for_human_review"].includes(String(current.status||"").toLowerCase()) || String(nextAction||"").startsWith("repair");
  const canPause    = !["paused","completed","rolled_back","cancelled"].includes(String(current.status||"").toLowerCase());
  const canResume   = ["paused","blocked"].includes(String(current.status||"").toLowerCase());
  const canRequeue  = ["blocked","paused","no_go"].includes(String(current.status||"").toLowerCase());
  const canRecover  = ["blocked","paused","no_go","recovering"].includes(String(current.status||"").toLowerCase());

  const meta = document.createElement("div");
  meta.className = "action-meta";
  meta.innerHTML = `
    ${chip(statusClass(current.status), current.status || "—")}
    ${chip("neutral", `ckpt ${current.checkpoint || "—"}`)}
    ${chip(statusClass(nextAction), `next ${nextAction}`)}
    ${chip(statusClass(releaseStatus), `rel ${releaseStatus}`)}
    ${current.v8 ? chip("v8", "V8") : ""}
  `;

  const btns = document.createElement("div");
  btns.className = "action-buttons";
  btns.append(
    makeButton("Apply",   { cls: "primary", disabled: !canApply,   title: rc?.id ? `Apply ${shortId(rc.id)}` : "No release candidate", onClick: async () => { if (rc?.id) { await fetchJson(`${API_BASE}/release-candidates/${rc.id}/apply`, { method: "POST" }); await refreshAll(); } } }),
    makeButton("Rollback",{ cls: "danger",  disabled: !canRollback, title: rc?.id ? `Rollback ${shortId(rc.id)}` : "No release candidate", onClick: async () => { if (rc?.id) { await fetchJson(`${API_BASE}/release-candidates/${rc.id}/rollback`, { method: "POST" }); await refreshAll(); } } }),
    makeButton("Repair",  { disabled: !canRepair,  onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/repair`, { method: "POST" }); await refreshAll(); } }),
    makeButton("Requeue", { disabled: !canRequeue, onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/requeue-blocked`, { method: "POST" }); await refreshAll(); } }),
    makeButton("Pause",   { disabled: !canPause,   onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/pause`,  { method: "POST" }); await refreshAll(); } }),
    makeButton("Resume",  { disabled: !canResume,  onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/resume`, { method: "POST" }); await refreshAll(); } }),
    makeButton("Refresh", { onClick: async () => renderRun(current.id) }),
    makeButton("Recover", { disabled: !canRecover, onClick: async () => { await fetchJson(`${API_BASE}/runs/${current.id}/recover-all`, { method: "POST" }); await refreshAll(); } }),
  );

  const note = document.createElement("div");
  note.className = "action-note";
  note.textContent = rc?.id
    ? `Release candidate ${shortId(rc.id)} ready. Apply starts the release; rollback reverts.`
    : "No release candidate yet. Wait for quality and release generation, or use Repair if blocked.";

  nodes.runActions.append(meta, btns, note);
}

// ─── Refresh ────────────────────────────────────────────────────────

async function refreshAll() {
  let health, projects, workers, modelSettings;
  try {
    [health, projects, workers, modelSettings] = await Promise.all([
      fetchJson(`${API_BASE}/health`),
      fetchJson(`${API_BASE}/projects`),
      fetchJson(`${API_BASE}/workers?limit=12`).catch(() => ({ items: [] })),
      fetchJson(`${API_BASE}/model-settings`).catch(() => ({ llm: null, recommended: recommendedModelSettings })),
    ]);
  } catch (err) {
    setHealthError(err.message);
    return;
  }

  if (nodes.healthPill) {
    const storeLabel = health.store || "memory";
    const dbOk = health.db_connected !== false;
    nodes.healthPill.textContent = `${health.ok ? "ok" : "fail"} / ${storeLabel}${!dbOk ? " (no db)" : ""}`;
    nodes.healthPill.className = `status-pill ${health.ok && dbOk ? "ok" : "fail"}`;
  }
  if (nodes.schemaPill) {
    nodes.schemaPill.textContent = `v${health.schema_version || "8.0"}`;
  }

  state.projects = (Array.isArray(projects) ? projects : projects.items || []);
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

  if (nodes.workerCount) nodes.workerCount.textContent = state.workers.length;
  renderProjects();
  renderWorkers();

  if (state.selectedRunId) await renderRun(state.selectedRunId);
  else if (state.selectedProject) renderProjectOverview();
  else clearDetail();
}

function clearDetail() {
  if (nodes.runTitle) nodes.runTitle.textContent = "Select a project";
  clearNode(nodes.runBadgeStrip);
  clearNode(nodes.runSummary);
  clearNode(nodes.runActions);
  if (nodes.exportForm) nodes.exportForm.classList.add("hidden");
  setFeedback(nodes.exportStatus, "");

  const emptyHtml = '<div class="empty">—</div>';
  [nodes.missionSummary, nodes.missionBlocker, nodes.missionKernel,
   nodes.executionPlan, nodes.implementationPlan, nodes.stabilityReport,
   nodes.frontendQuality, nodes.contractValidation, nodes.patchTransactions,
   nodes.testExecution, nodes.codeContractIndex].forEach((n) => clearNode(n));
  [nodes.aiCallList, nodes.waveList, nodes.packageList, nodes.qualityGateList,
   nodes.repairList, nodes.jobList, nodes.artifactList].forEach((n) => clearNode(n, emptyHtml));
  if (nodes.contextIndex) nodes.contextIndex.textContent = "";
  if (nodes.continuation) nodes.continuation.textContent = "";
}

// ─── Project Overview ────────────────────────────────────────────────

function renderProjectOverview() {
  const project = state.selectedProject;
  if (!project) { clearDetail(); return; }
  const runs = state.selectedProjectRuns || [];
  const latest = runs[0];
  if (nodes.runTitle) nodes.runTitle.textContent = `${project.title || project.name} — Project`;
  clearNode(nodes.runBadgeStrip, chip("neutral", `id ${shortId(project.id)}`));
  clearNode(nodes.runSummary, `
    ${statCell(project.status || "—", "status")}
    ${statCell(project.config?.target_scale || "—", "scale")}
    ${statCell(runs.length, "runs")}
    ${statCell(latest?.status || "none", "latest")}
  `);
  clearNode(nodes.runActions, '<div class="action-meta"><span class="chip neutral">No run selected</span></div>');
  if (nodes.exportForm) nodes.exportForm.classList.add("hidden");
  clearNode(nodes.missionSummary, `
    ${statCell(project.resolved_project_root || project.project_path || "—", "path")}
    ${statCell(latest?.checkpoint || "—", "checkpoint")}
  `);
  clearNode(nodes.missionKernel, `
    ${statCell(project.scale_profile?.name || project.config?.target_scale || "—", "profile")}
    ${statCell(project.scale_profile?.kernel_generation || "—", "kernel")}
  `);
  const emptyHtml = '<div class="empty">No run selected</div>';
  [nodes.contractValidation, nodes.patchTransactions, nodes.testExecution,
   nodes.codeContractIndex, nodes.aiCallList, nodes.waveList, nodes.packageList,
   nodes.qualityGateList, nodes.repairList, nodes.jobList, nodes.artifactList].forEach((n) => clearNode(n, emptyHtml));
  if (nodes.contextIndex) nodes.contextIndex.textContent = JSON.stringify({ project_id: project.id, name: project.name, title: project.title, latest_run: latest?.id || "", status: latest?.status || "none" }, null, 2);
  if (nodes.continuation) nodes.continuation.textContent = JSON.stringify({ project_view: true, project_id: project.id, latest_run: latest?.id || "", status: latest?.status || "none", checkpoint: latest?.checkpoint || "", next: latest?.id ? "open_latest_run" : "create_new_run" }, null, 2);
}

// ─── Projects list ───────────────────────────────────────────────────

function renderProjects() {
  if (!nodes.projectList) return;
  nodes.projectList.innerHTML = "";
  const count = state.selectedProjectIds.size;
  if (nodes.deleteSelected) {
    nodes.deleteSelected.disabled = count === 0;
    nodes.deleteSelected.textContent = count ? `Delete (${count})` : "Delete";
  }
  if (nodes.selectAll) {
    nodes.selectAll.checked = state.projects.length > 0 && state.projects.every((p) => state.selectedProjectIds.has(p.id));
    nodes.selectAll.indeterminate = count > 0 && count < state.projects.length;
  }
  if (!state.projects.length) {
    nodes.projectList.innerHTML = '<div class="empty">No projects yet</div>';
    return;
  }
  for (const project of state.projects) {
    const row = document.createElement("div");
    row.className = `item-row ${project.id === state.selectedProjectId ? "active" : ""}`;
    row.tabIndex = 0; row.setAttribute("role", "button");
    row.innerHTML = `
      <input class="proj-check" type="checkbox" ${state.selectedProjectIds.has(project.id) ? "checked" : ""} aria-label="Select ${esc(project.title || project.name)}">
      <div class="item-meta">
        <strong>${esc(project.title || project.name)}</strong>
        <small>${esc(project.name)} / ${shortId(project.id)}</small>
      </div>
      <span class="chip ${statusClass(project.status)}">${esc(project.status)}</span>
      <button class="btn-ghost btn-sm proj-run" type="button">Run</button>
    `;
    const check = row.querySelector(".proj-check");
    const runBtn = row.querySelector(".proj-run");
    const syncCheck = () => { check.checked ? state.selectedProjectIds.add(project.id) : state.selectedProjectIds.delete(project.id); renderProjects(); };
    check.addEventListener("click", (e) => e.stopPropagation());
    check.addEventListener("change", (e) => { e.stopPropagation(); syncCheck(); });

    const openProject = async () => {
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
    const runProject = async () => {
      const r = await fetchJson(`${API_BASE}/projects/${project.id}/runs`, {
        method: "POST", body: JSON.stringify({ requirements_text: project.description || project.title || project.name }),
      });
      state.selectedProjectId = project.id;
      state.selectedProject = project;
      state.selectedProjectRuns = [r.run, ...(state.selectedProjectRuns || []).filter((i) => i.id !== r.run.id)];
      state.selectedRunId = r.run.id;
      await refreshAll();
    };
    row.addEventListener("click", (e) => { if (e.target.closest("input, button")) return; openProject().catch(setHealthError); });
    row.addEventListener("keydown", (e) => { if (e.target.closest("input, button")) return; if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openProject().catch(setHealthError); } });
    runBtn.addEventListener("click", (e) => { e.stopPropagation(); runProject().catch((err) => setHealthError(err.message)); });
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
    clearDetail();
  }
  state.selectedProjectIds.clear();
  await refreshAll();
}

// ─── Workers ─────────────────────────────────────────────────────────

function renderWorkers() {
  if (!nodes.workerList) return;
  nodes.workerList.innerHTML = "";
  if (!state.workers.length) {
    nodes.workerList.innerHTML = '<div class="empty">No workers</div>';
    return;
  }
  for (const w of state.workers) {
    const row = document.createElement("div");
    row.className = "item-row static";
    row.innerHTML = `
      <div class="item-meta">
        <strong>${esc(w.role || w.worker_id)}</strong>
        <small>pid ${esc(w.pid || "—")} / ${esc(w.last_heartbeat || "—")}</small>
      </div>
      <span class="chip ${statusClass(w.status)}">${esc(w.status)}</span>
    `;
    nodes.workerList.appendChild(row);
  }
}

// ─── Model Settings ───────────────────────────────────────────────────

function providerNameFromProfile(profile) {
  const v = String(profile || "").trim();
  return v.startsWith("custom-") ? "custom" : v || "custom";
}

function modelProviderFromSettings(settings) {
  const llm = settings?.llm || {};
  const rec = settings?.recommended || recommendedModelSettings;
  return {
    model_provider: providerNameFromProfile(llm.provider_profile) || rec.model_provider,
    model: llm.model || rec.model,
    model_reasoning_effort: llm.model_reasoning_effort || rec.model_reasoning_effort,
    disable_response_storage: Boolean(llm.disable_response_storage ?? rec.disable_response_storage),
    model_providers: {
      custom: {
        name: "custom",
        wire_api: llm.wire_api || rec.model_providers?.custom?.wire_api || "responses",
        requires_openai_auth: (llm.auth_header || "Authorization") === "Authorization",
        base_url: llm.api_base || rec.model_providers?.custom?.base_url || "",
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
  const keyState = llm.api_key === "***" ? "key stored" : "key missing";
  nodes.modelSettingsStatus.innerHTML = `
    ${chip(llm.use_mock ? "live" : "good", llm.use_mock ? "mock" : "live")}
    ${chip("neutral", llm.provider_profile || "custom")}
    ${chip("neutral", llm.wire_api || "responses")}
    ${chip(llm.api_key === "***" ? "good" : "live", keyState)}
  `;
}

function applyRecommendedModelSettings() {
  fillModelSettingsForm({ recommended: recommendedModelSettings, llm: {} });
  state.modelSettingsDirty = true;
  if (nodes.modelSettingsStatus) nodes.modelSettingsStatus.innerHTML = chip("live", "preset ready");
}

function buildModelSettingsPayload() {
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
  const orig = nodes.saveModelSettings?.textContent || "Save";
  if (nodes.saveModelSettings) { nodes.saveModelSettings.disabled = true; nodes.saveModelSettings.textContent = "Saving…"; }
  try {
    const result = await fetchJson(`${API_BASE}/model-settings`, { method: "PUT", body: JSON.stringify(buildModelSettingsPayload()) });
    state.modelSettings = result; state.modelSettingsDirty = false;
    renderModelSettings(result, { force: true });
    nodes.modelSettingsStatus.insertAdjacentHTML("beforeend", chip("good", "saved"));
  } catch (err) {
    if (nodes.modelSettingsStatus) nodes.modelSettingsStatus.innerHTML = chip("bad", err.message);
  } finally {
    if (nodes.saveModelSettings) { nodes.saveModelSettings.disabled = false; nodes.saveModelSettings.textContent = orig; }
  }
}

async function testModelSettings() {
  const orig = nodes.testModelSettings?.textContent || "Test";
  if (nodes.testModelSettings) { nodes.testModelSettings.disabled = true; nodes.testModelSettings.textContent = "Testing…"; }
  if (nodes.modelSettingsStatus) nodes.modelSettingsStatus.innerHTML = chip("live", "testing…");
  try {
    const result = await fetchJson(`${API_BASE}/model-settings/test`, { method: "POST", body: JSON.stringify(buildModelSettingsPayload()) });
    state.modelSettings = result; state.modelSettingsDirty = false;
    renderModelSettings(result, { force: true });
    const tone = result.ok ? "good" : "bad";
    const msg = result.ok ? "connection ok" : (result.result?.error || "failed");
    nodes.modelSettingsStatus.insertAdjacentHTML("beforeend", chip(tone, msg));
  } catch (err) {
    if (nodes.modelSettingsStatus) nodes.modelSettingsStatus.innerHTML = chip("bad", err.message);
  } finally {
    if (nodes.testModelSettings) { nodes.testModelSettings.disabled = false; nodes.testModelSettings.textContent = orig; }
  }
}

// ─── Export ──────────────────────────────────────────────────────────

function renderDeliveryExport(current) {
  if (!nodes.exportForm) return;
  const exportable = ["release_ready","completed"].includes(String(current?.status || "").toLowerCase());
  nodes.exportForm.classList.toggle("hidden", !current?.id);
  if (nodes.exportDelivery) {
    nodes.exportDelivery.disabled = !exportable;
    nodes.exportDelivery.title = exportable ? "Export deployment files to target directory" : "Available after release_ready or completed";
  }
  if (!nodes.exportForm.elements.target_path.value && current?.metadata?.delivery_export_target) {
    nodes.exportForm.elements.target_path.value = current.metadata.delivery_export_target;
  }
  if (exportable && !nodes.exportStatus?.textContent) {
    setFeedback(nodes.exportStatus, "Ready to export.", "neutral");
  } else if (!exportable) {
    setFeedback(nodes.exportStatus, "Export available after release is ready.", "live");
  }
}

async function exportDelivery(event) {
  event.preventDefault();
  if (!state.selectedRunId) return;
  const orig = nodes.exportDelivery?.textContent || "Export";
  if (nodes.exportDelivery) { nodes.exportDelivery.disabled = true; nodes.exportDelivery.textContent = "Exporting…"; }
  const f = new FormData(nodes.exportForm);
  setFeedback(nodes.exportStatus, "Copying deployment files…", "live");
  try {
    const result = await fetchJson(`${API_BASE}/runs/${state.selectedRunId}/export-delivery`, {
      method: "POST", body: JSON.stringify({ target_path: String(f.get("target_path") || "").trim(), overwrite: Boolean(f.get("overwrite")) }),
    });
    const report = result.report || {};
    setFeedback(nodes.exportStatus, `Exported ${report.copied_count || 0} files to ${report.target_path || "—"}.`, "good");
    await renderRun(state.selectedRunId);
  } catch (err) {
    setFeedback(nodes.exportStatus, err.message, "bad");
  } finally {
    if (nodes.exportDelivery) { nodes.exportDelivery.disabled = false; nodes.exportDelivery.textContent = orig; }
  }
}

// ─── Run Detail ───────────────────────────────────────────────────────

async function renderRun(runId) {
  const [run, jobs, continuation, artifacts, aiCalls, waves, packages, quality, contextSnap, repairs, layout, patchSets, patchTxns, testExec, codeIdx, contractIdx, mission, contractViolations] = await Promise.all([
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
    fetchJson(`${API_BASE}/runs/${runId}/patch-transactions`).catch(() => ({ report: null, items: [], conflicts: [] })),
    fetchJson(`${API_BASE}/runs/${runId}/test-execution`).catch(() => ({ report: null })),
    fetchJson(`${API_BASE}/runs/${runId}/code-index`).catch(() => ({ index: null })),
    fetchJson(`${API_BASE}/runs/${runId}/contract-index`).catch(() => ({ index: null })),
    fetchJson(`${API_BASE}/runs/${runId}/mission`).catch(() => ({ mission_state: null })),
    fetchJson(`${API_BASE}/runs/${runId}/contract_violations`).catch(() => ({ items: [] })),
  ]);

  const current = run.run || run;
  const cs = current.contract_summary || {};
  const qr = quality.report || {};
  const wave = continuation.continuation?.current_wave || "—";
  const effLoc = qr.effective_loc?.total ?? current.metadata?.effective_loc_metrics?.total ?? 0;
  const tgtLoc = current.metadata?.project_config?.effective_loc_target || "—";
  const ms = mission.mission_state || mission || {};
  const profile = ms.scale_profile || current.metadata?.scale_profile || {};
  const recovery = ms.recovery || {};
  const provider = ms.provider_health || {};

  // Header
  if (nodes.runTitle) nodes.runTitle.textContent = `Run ${shortId(current.id)}`;
  clearNode(nodes.runBadgeStrip, `
    ${chip(statusClass(current.status), current.status || "—")}
    ${current.v8 ? chip("v8", "V8") : ""}
    ${chip("neutral", `ckpt ${current.checkpoint || "—"}`)}
    ${cs.failed ? chip("bad", `${cs.failed} contract fail`) : ""}
  `);

  // Top stats
  clearNode(nodes.runSummary, `
    ${statCell(current.status, "status", statusClass(current.status))}
    ${statCell(current.checkpoint || "—", "checkpoint")}
    ${statCell(layout.layout?.delivery_root || current.metadata?.project_layout?.delivery_root || "—", "delivery")}
    ${statCell(cs.total || 0, "agent calls")}
    ${statCell(cs.failed || 0, "contract fails", cs.failed ? "bad" : "good")}
  `);

  renderRunActions(current, continuation, artifacts);
  renderDeliveryExport(current);

  // Mission Health
  clearNode(nodes.missionSummary, `
    ${statCell(wave, "wave")}
    ${statCell(`${effLoc} / ${tgtLoc}`, "LOC")}
    ${statCell(aiCalls.items?.length || 0, "agent runs")}
    ${statCell(patchSets.items?.length || 0, "patches")}
  `);

  // Blocker
  const blocker = current.continuation?.active_blocker || current.continuation?.failure_reason || recovery?.last_failure_reason || "none";
  clearNode(nodes.missionBlocker, `
    ${statCell(blocker, "blocker", statusClass(current.status))}
    ${statCell(recovery.dead_letter_count || 0, "dead letters")}
    ${statCell(recovery.retryable_provider_failures || 0, "retryable")}
    ${statCell(provider.degraded_count || 0, "degraded")}
  `);

  // Execution Kernel
  clearNode(nodes.missionKernel, `
    ${statCell(profile.name || "—", "profile")}
    ${statCell(profile.wave_parallelism || "—", "width")}
    ${statCell(ms.next_action || "—", "next", statusClass(ms.next_action))}
    ${statCell(recovery.dead_letter_count || 0, "dead", recovery.dead_letter_count ? "bad" : "good")}
    ${statCell(provider.degraded_count || 0, "degraded", provider.degraded_count ? "bad" : "good")}
    ${statCell(shortId(ms.context?.mission_memory_hash || ""), "memory")}
    ${statCell(profile.recovery_policy || "—", "policy")}
  `);

  // Execution Plan
  clearNode(nodes.executionPlan, `
    ${statCell(ms.mission_flow?.stages?.length || 0, "stages")}
    ${statCell((ms.blockers || []).length, "blockers")}
    ${statCell(ms.graph?.package_count || 0, "packages")}
    ${statCell(ms.graph?.wave_count || 0, "waves")}
  `);

  // Implementation Plan
  const impl = ms.implementation_plan || {};
  clearNode(nodes.implementationPlan, `
    ${statCell((impl.flows || []).length, "flows")}
    ${statCell((impl.blockers || []).length, "blockers")}
    ${statCell((impl.flows?.[0]?.micro_tasks || []).length || 0, "micro")}
    ${statCell((impl.flows?.[0]?.file_plan || []).length || 0, "files")}
  `);

  // Stability Report
  const stab = ms.stability_report || {};
  clearNode(nodes.stabilityReport, `
    ${statCell((stab.dead_letter_jobs || []).length, "dead")}
    ${statCell(stab.ai_slots?.active_count || 0, "slots")}
    ${statCell(stab.provider_health?.degraded_count || 0, "degraded")}
    ${statCell((stab.blockers || []).length, "blockers")}
  `);

  // Frontend Quality
  const fq = ms.frontend_quality || {};
  clearNode(nodes.frontendQuality, `
    ${statCell(fq.ok ? "pass" : "fail", "ux", fq.ok ? "good" : "bad")}
    ${statCell(fq.frontend_ux_gate?.name || "—", "gate")}
    ${statCell(fq.frontend_ux_gate?.severity || "—", "severity")}
    ${statCell(shortId(fq.quality_report?.index_hash || ""), "hash")}
  `);

  // Contract Validation — V8 enhanced
  const viol = contractViolations.items || [];
  clearNode(nodes.contractValidation, `
    ${statCell(cs.total || 0, "total calls")}
    ${statCell(cs.failed || 0, "failed", cs.failed ? "bad" : "good")}
    ${statCell(viol.length, "violations", viol.length ? "bad" : "good")}
    ${statCell(cs.total ? Math.round(((cs.total - (cs.failed || 0)) / cs.total) * 100) + "%" : "—", "pass rate")}
  `);

  // Patch Transactions
  const pp = patchTxns.report || {};
  clearNode(nodes.patchTransactions, `
    ${statCell(pp.transaction_count || patchTxns.items?.length || 0, "txns")}
    ${statCell(pp.changed_file_count || 0, "files")}
    ${statCell(pp.conflict_count || patchTxns.conflicts?.length || 0, "conflicts", (pp.conflict_count || patchTxns.conflicts?.length) ? "bad" : "good")}
  `);

  // Test Execution
  const tp = testExec.report || {};
  clearNode(nodes.testExecution, `
    ${statCell(tp.status || "pending", "status", tp.ok ? "good" : "bad")}
    ${statCell(`${tp.executed_count || 0} / ${tp.command_count || 0}`, "executed")}
    ${statCell(tp.source || "—", "source")}
    ${statCell((tp.safety_failures || []).length, "safety fails")}
  `);

  // Code / Contract Index
  const ci = codeIdx.index || {};
  const cxi = contractIdx.index || {};
  clearNode(nodes.codeContractIndex, `
    ${statCell((ci.files || []).length, "files")}
    ${statCell(shortId(ci.index_hash || ""), "code hash")}
    ${statCell((cxi.contracts || []).length, "contracts")}
    ${statCell(shortId(cxi.index_hash || ""), "cxi hash")}
  `);

  // Agent Runs — V8: show contract_ok status
  nodes.aiCallList.innerHTML = (aiCalls.items || []).map((a) => {
    const p = a.payload || {};
    const contractOk = a.contract_ok;
    const contractBadge = contractOk === false
      ? chip("bad", "contract fail")
      : contractOk === true ? chip("good", "contract ok") : "";
    return `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(p.task_kind || p.job_type || "ai_call")}</strong>
        <small>${esc(p.model_tier || "—")} / ${esc(p.model || "—")} / ${esc(p.elapsed_ms || 0)}ms</small>
      </div>
      ${chip(p.ok ? "good" : "bad", p.degraded ? "degraded" : p.ok ? "ok" : "fail")}
      ${contractBadge}
    </div>`;
  }).join("") || '<div class="empty">No agent runs yet</div>';

  // Waves
  nodes.waveList.innerHTML = (waves.items || []).map((w) =>
    `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(w.wave_key)}</strong>
        <small>seq ${esc(w.sequence)} / pkgs ${esc(w.payload?.package_count || "—")}</small>
      </div>
      ${chip(statusClass(w.status), w.status)}
    </div>`
  ).join("") || '<div class="empty">No waves yet</div>';

  // Packages
  nodes.packageList.innerHTML = (packages.items || []).map((p) =>
    `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(p.package_key)}</strong>
        <small>${esc(p.payload?.subsystem || p.domain || "—")} / ${esc(p.role)} / ${esc(p.wave_key)}</small>
      </div>
      ${chip(statusClass(p.status), p.status)}
    </div>`
  ).join("") || '<div class="empty">No packages yet</div>';

  // Quality Gates
  nodes.qualityGateList.innerHTML = (qr.gates || []).map((g) =>
    `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(g.name)}</strong>
        <small>${esc(g.severity)} / ${esc(JSON.stringify(g.details || {}).slice(0, 120))}</small>
      </div>
      ${chip(g.ok ? "good" : "bad", g.ok ? "pass" : "fail")}
    </div>`
  ).join("") || '<div class="empty">No quality report yet</div>';

  // Repair History
  nodes.repairList.innerHTML = (repairs.items || []).map((a) =>
    `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(a.payload?.failure_reason || "repair")}</strong>
        <small>${esc(a.payload?.next_action || "—")}</small>
      </div>
      ${chip(statusClass(a.payload?.status), a.payload?.status || "—")}
    </div>`
  ).join("") || '<div class="empty">No repairs</div>';

  // Jobs
  nodes.jobList.innerHTML = (jobs.items || []).map((j) =>
    `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(j.job_type)}</strong>
        <small>${esc(j.role)} / ${esc(j.subsystem || "—")} / ${shortId(j.id)}</small>
      </div>
      ${chip(statusClass(j.status), j.status)}
    </div>`
  ).join("") || '<div class="empty">No jobs yet</div>';

  // Artifacts
  nodes.artifactList.innerHTML = (artifacts.items || []).map((a) =>
    `<div class="item-row static">
      <div class="item-meta">
        <strong>${esc(a.kind)}</strong>
        <small>${esc(a.path || a.key || "—")}</small>
      </div>
      ${chip("neutral", `${esc(a.size || 0)}b`)}
    </div>`
  ).join("") || '<div class="empty">No artifacts yet</div>';

  if (nodes.contextIndex) nodes.contextIndex.textContent = JSON.stringify(contextSnap.snapshot || {}, null, 2);
  if (nodes.continuation)  nodes.continuation.textContent  = JSON.stringify(continuation.continuation, null, 2);
}

// ─── Create Project form ─────────────────────────────────────────────

nodes.form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const orig = nodes.createRun?.textContent || "Dispatch Run";
  if (nodes.createRun) { nodes.createRun.disabled = true; nodes.createRun.textContent = "Creating…"; }
  setFeedback(nodes.createRunStatus, "Creating project…", "live");
  const f = new FormData(nodes.form);
  const payload = {
    name: f.get("name") || "", title: f.get("title") || "",
    project_path: f.get("project_path") || "", description: f.get("description") || "",
    stack_pack: f.get("stack_pack") || "auto", target_scale: f.get("target_scale") || "auto",
    effective_loc_target: Number.parseInt(f.get("effective_loc_target") || "1000", 10),
  };
  try {
    const project = await fetchJson(`${API_BASE}/projects`, { method: "POST", body: JSON.stringify(payload) });
    setFeedback(nodes.createRunStatus, "Project created. Starting run…", "live");
    state.selectedProjectId = project.project?.id || project.id;
    const run = await fetchJson(`${API_BASE}/projects/${state.selectedProjectId}/runs`, {
      method: "POST", body: JSON.stringify({ requirements_text: payload.description }),
    });
    state.selectedRunId = run.run?.id || run.id;
    nodes.form.reset();
    setFeedback(nodes.createRunStatus, `Run ${shortId(state.selectedRunId)} queued.`, "good");
    await refreshAll();
  } catch (err) {
    setHealthError(err.message);
    setFeedback(nodes.createRunStatus, err.message, "bad");
  } finally {
    if (nodes.createRun) { nodes.createRun.disabled = false; nodes.createRun.textContent = orig; }
  }
});

// ─── Event Listeners ─────────────────────────────────────────────────

nodes.refresh?.addEventListener("click", () => refreshAll().catch(setHealthError));
nodes.selectAll?.addEventListener("change", () => {
  if (nodes.selectAll.checked) { state.projects.forEach((p) => state.selectedProjectIds.add(p.id)); }
  else { state.selectedProjectIds.clear(); }
  renderProjects();
});
nodes.deleteSelected?.addEventListener("click", () => {
  deleteSelectedProjects().catch((err) => setHealthError(err.message));
});
nodes.exportForm?.addEventListener("submit", exportDelivery);
nodes.applyRecommended?.addEventListener("click", applyRecommendedModelSettings);
nodes.testModelSettings?.addEventListener("click", testModelSettings);
nodes.modelSettingsForm?.addEventListener("input", () => { state.modelSettingsDirty = true; });
nodes.modelSettingsForm?.addEventListener("submit", saveModelSettings);

// ─── Boot ─────────────────────────────────────────────────────────────

refreshAll().catch(setHealthError);
setInterval(() => refreshAll().catch(() => {}), 5000);
