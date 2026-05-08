const state = {
  projects: [],
  selectedProjectId: "",
  selectedRunId: "",
  selectedProject: null,
  selectedProjectRuns: [],
  selectedProjectIds: new Set(),
  workers: [],
};

let API_BASE = "/api/v5";
const $ = (id) => document.getElementById(id);

const nodes = {
  health: $("health-pill"),
  workers: $("worker-count"),
  refresh: $("refresh"),
  createRun: $("create-run"),
  selectAllProjects: $("select-all-projects"),
  deleteSelectedProjects: $("delete-selected-projects"),
  form: $("project-form"),
  projectList: $("project-list"),
  runTitle: $("run-title"),
  runSummary: $("run-summary"),
  runActions: $("run-actions"),
  v5Summary: $("v5-summary"),
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

async function fetchJson(path, options = {}) {
  const pathText = String(path || "");
  let candidate = pathText;
  if (pathText.startsWith("/api/v5/")) {
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
  if (["queued", "retry", "paused", "leased"].includes(item)) return "live";
  return "neutral";
}

function latestArtifactByKind(items, kind) {
  const matches = (items || []).filter((item) => item.kind === kind);
  return matches[matches.length - 1] || null;
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
  const [health, projects, workers] = await Promise.all([
    fetchJson(`${API_BASE}/health`),
    fetchJson(`${API_BASE}/projects`),
    fetchJson(`${API_BASE}/workers?limit=12`),
  ]);
  nodes.health.textContent = `${health.status} / ${health.kernel}`;
  nodes.health.className = "pill good";
  state.projects = projects.items || [];
  state.workers = workers.items || [];
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
  nodes.v5Summary.innerHTML = "";
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
  const projectPath = project.project_path || "-";
  nodes.runTitle.textContent = `${project.title || project.name} - Project view`;
  nodes.runSummary.innerHTML = `
    <div><strong>${esc(project.status || "-")}</strong><small>project status</small></div>
    <div><strong>${esc(technologyGuidance)}</strong><small>technology guidance</small></div>
    <div><strong>${esc(targetScale)}</strong><small>target scale</small></div>
  `;
  nodes.runActions.innerHTML = '<div class="empty">No run selected</div>';
  nodes.v5Summary.innerHTML = `
    <div><strong>${esc(projectPath)}</strong><small>project path</small></div>
    <div><strong>${esc(runs.length)}</strong><small>run count</small></div>
    <div><strong>${esc(latestRun?.status || "none")}</strong><small>latest run</small></div>
    <div><strong>${esc(latestRun?.checkpoint || "-")}</strong><small>latest checkpoint</small></div>
  `;
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
  const [run, jobs, continuation, artifacts, aiCalls, waves, packages, quality, contextIndex, repairs, layout, patchSets] = await Promise.all([
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
  nodes.v5Summary.innerHTML = `
    <div><strong>${esc(currentWave)}</strong><small>current wave</small></div>
    <div><strong>${esc(effectiveLoc)} / ${esc(targetLoc)}</strong><small>effective LOC</small></div>
    <div><strong>${esc(aiCalls.items?.length || 0)}</strong><small>agent runs</small></div>
    <div><strong>${esc(patchSets.items?.length || 0)}</strong><small>patch sets</small></div>
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

nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const originalLabel = nodes.createRun?.textContent || "Create and Run";
  if (nodes.createRun) {
    nodes.createRun.disabled = true;
    nodes.createRun.textContent = "Creating...";
  }
  const form = new FormData(nodes.form);
  const payload = {
    name: form.get("name") || "",
    title: form.get("title") || "",
    project_path: form.get("project_path") || "",
    description: form.get("description") || "",
    stack_pack: form.get("stack_pack") || "auto",
    target_scale: form.get("target_scale") || "small",
    effective_loc_target: Number.parseInt(form.get("effective_loc_target") || "1000", 10),
  };
  try {
    const project = await fetchJson(`${API_BASE}/projects`, { method: "POST", body: JSON.stringify(payload) });
    state.selectedProjectId = project.project.id;
    const run = await fetchJson(`${API_BASE}/projects/${project.project.id}/runs`, {
      method: "POST",
      body: JSON.stringify({ requirements_text: payload.description }),
    });
    state.selectedRunId = run.run.id;
    nodes.form.reset();
    await refreshAll();
  } catch (error) {
    nodes.health.textContent = error.message;
    nodes.health.className = "pill bad";
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
refreshAll().catch((error) => {
  nodes.health.textContent = error.message;
  nodes.health.className = "pill bad";
});
setInterval(() => refreshAll().catch(() => {}), 5000);
