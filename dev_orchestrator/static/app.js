const state = {
  projects: [],
  selectedProjectId: "",
  selectedRunId: "",
  workers: [],
};

const $ = (id) => document.getElementById(id);

const nodes = {
  health: $("health-pill"),
  workers: $("worker-count"),
  refresh: $("refresh"),
  form: $("project-form"),
  projectList: $("project-list"),
  runTitle: $("run-title"),
  runSummary: $("run-summary"),
  jobList: $("job-list"),
  continuation: $("continuation"),
  artifactList: $("artifact-list"),
  workerList: $("worker-list"),
};

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
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
  if (["completed", "release_ready", "go", "passed", "running"].includes(item)) return "good";
  if (["no_go", "dead_letter", "blocked", "failed", "rollback_failed"].includes(item)) return "bad";
  if (["queued", "retry", "paused"].includes(item)) return "live";
  return "neutral";
}

async function refreshAll() {
  const [health, projects, workers] = await Promise.all([
    fetchJson("/api/v4/health"),
    fetchJson("/api/v4/projects"),
    fetchJson("/api/v4/workers"),
  ]);
  nodes.health.textContent = `${health.status} / ${health.kernel}`;
  state.projects = projects.items || [];
  state.workers = workers.items || [];
  nodes.workers.textContent = `workers ${state.workers.length}`;
  renderProjects();
  renderWorkers();
  if (state.selectedRunId) {
    await renderRun(state.selectedRunId);
  }
}

function renderProjects() {
  nodes.projectList.innerHTML = "";
  if (!state.projects.length) {
    nodes.projectList.innerHTML = '<div class="empty">暂无项目</div>';
    return;
  }
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `row ${project.id === state.selectedProjectId ? "active" : ""}`;
    button.innerHTML = `
      <span>
        <strong>${esc(project.title || project.name)}</strong>
        <small>${esc(project.name)} · ${shortId(project.id)}</small>
      </span>
      <span class="chip ${statusClass(project.status)}">${esc(project.status)}</span>
    `;
    button.addEventListener("click", async () => {
      state.selectedProjectId = project.id;
      const run = await fetchJson(`/api/v4/projects/${project.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ requirements_text: project.description || project.title || project.name }),
      });
      state.selectedRunId = run.run.id;
      await refreshAll();
    });
    nodes.projectList.appendChild(button);
  }
}

function renderWorkers() {
  nodes.workerList.innerHTML = "";
  if (!state.workers.length) {
    nodes.workerList.innerHTML = '<div class="empty">暂无 worker heartbeat</div>';
    return;
  }
  for (const worker of state.workers) {
    const row = document.createElement("div");
    row.className = "row static";
    row.innerHTML = `
      <span>
        <strong>${esc(worker.role || worker.worker_id)}</strong>
        <small>pid ${esc(worker.pid || "-")} · ${esc(worker.last_heartbeat || "-")}</small>
      </span>
      <span class="chip ${statusClass(worker.status)}">${esc(worker.status)}</span>
    `;
    nodes.workerList.appendChild(row);
  }
}

async function renderRun(runId) {
  const [run, jobs, continuation, artifacts] = await Promise.all([
    fetchJson(`/api/v4/runs/${runId}`),
    fetchJson(`/api/v4/runs/${runId}/jobs`),
    fetchJson(`/api/v4/runs/${runId}/continuation`),
    fetchJson(`/api/v4/runs/${runId}/artifacts`),
  ]);
  const current = run.run;
  nodes.runTitle.textContent = `Run ${shortId(current.id)}`;
  nodes.runSummary.innerHTML = `
    <div><strong>${esc(current.status)}</strong><small>status</small></div>
    <div><strong>${esc(current.checkpoint)}</strong><small>checkpoint</small></div>
    <div><strong>${esc((current.metadata?.product_contract || {}).stack_pack || "-")}</strong><small>stack pack</small></div>
  `;
  nodes.jobList.innerHTML = (jobs.items || []).map((job) => `
    <div class="row static">
      <span><strong>${esc(job.job_type)}</strong><small>${esc(job.role)} · ${shortId(job.id)}</small></span>
      <span class="chip ${statusClass(job.status)}">${esc(job.status)}</span>
    </div>
  `).join("") || '<div class="empty">暂无 job</div>';
  nodes.continuation.textContent = JSON.stringify(continuation.continuation, null, 2);
  nodes.artifactList.innerHTML = (artifacts.items || []).map((artifact) => `
    <div class="row static">
      <span><strong>${esc(artifact.kind)}</strong><small>${esc(artifact.path || "-")}</small></span>
      <span class="chip neutral">${esc(artifact.size || 0)}b</span>
    </div>
  `).join("") || '<div class="empty">暂无 artifact</div>';
}

nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(nodes.form);
  const payload = {
    name: form.get("name") || "",
    title: form.get("title") || "",
    description: form.get("description") || "",
    stack_pack: form.get("stack_pack") || "auto",
    target_scale: form.get("target_scale") || "small",
    effective_loc_target: Number.parseInt(form.get("effective_loc_target") || "1000", 10),
  };
  const project = await fetchJson("/api/v4/projects", { method: "POST", body: JSON.stringify(payload) });
  state.selectedProjectId = project.project.id;
  const run = await fetchJson(`/api/v4/projects/${project.project.id}/runs`, {
    method: "POST",
    body: JSON.stringify({ requirements_text: payload.description }),
  });
  state.selectedRunId = run.run.id;
  nodes.form.reset();
  await refreshAll();
});

nodes.refresh.addEventListener("click", refreshAll);
refreshAll().catch((error) => {
  nodes.health.textContent = error.message;
  nodes.health.className = "pill bad";
});
setInterval(() => refreshAll().catch(() => {}), 5000);
