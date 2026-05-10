# V6 100k AI-Native Control Plane

This repository now runs a V6.4 100k-oriented AI-native mission kernel with `/api/v6/*` as the only control surface.

The system is no longer a template renderer. Project layout, source files, tests, security notes, deployment notes, and repair patches are produced by AI agents through file-manifest patch sets. The runtime keeps durable jobs, worker leases, path safety, artifacts, recovery state, provider health, layered mission memory, and 100k-scale quality gates.

## What You Get

- `/api/v6/*` mission-kernel and control-plane endpoints
- single-active-version policy: when a new release lands, retired version code, docs, routes, and entrypoints are removed
- `auto` target-scale inference plus four concrete scale profiles (`small`, `medium`, `large`, `xlarge_100k`) that all use the 100k AI-native kernel
- Postgres durable jobs with worker leases and retries
- role workers for requirements, architecture, planning, code, QA, security, integration, review, repair, release, and docs
- provider resilience with transient error classification, retry state, and health snapshots
- profile-driven parallel worker supervision, durable AI call slots, provider backpressure, and long-call worker heartbeats
- managed runtime storage lifecycle with export receipts, dry-run garbage collection, archive, prune, quotas, and path-safe cleanup
- layered mission memory for project charter, architecture, subsystem, package, and change memory
- AI-generated `project-layout.json`
- AI-generated `package-dag.json`
- AI-generated file-manifest `patch_set` artifacts
- quality gates that verify AI origin, path boundaries, DAG health, package ownership, contract density, review evidence, test evidence, anti-template leakage, provider resilience, recovery checkpoints, and model budget
- release candidates, deploy guide, rollback manifest, pause/resume/repair

## Quick Start

```powershell
python run_server.py --api
python run_server.py --worker-supervisor
```

Open:

```text
http://127.0.0.1:8787
```

## Docker

```powershell
docker compose -f docker-compose.v6.yml up -d --build
```

## Verification

```powershell
python -m unittest discover -s tests
python run_server.py --system-check
python run_server.py --llm-contract-check
```

## Core CLI

```powershell
python run_server.py --api
python run_server.py --worker --role backend
python run_server.py --worker --role requirements --once
python run_server.py --worker-supervisor
python run_server.py --system-check
python run_server.py --purge-retired-records
python run_server.py --storage-report
python run_server.py --gc-workspace
python run_server.py --pressure-test
```

`--pressure-test` runs the real-AI 100k pressure harness and writes a report under `logs/pressure/`.
It requires a live provider, Postgres, and a long runtime window.
When running from the host while the persisted config points at the compose-only `postgres` hostname, pass a host URL explicitly:

```powershell
python run_server.py --pressure-test --pressure-database-url postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator
```

## AI Contract

Agents return strict JSON. Code-producing agents use this file-manifest protocol:

```json
{
  "agent": "backend",
  "status": "GO",
  "summary": "Implemented the bounded package.",
  "files": [
    {
      "path": "src/example.py",
      "action": "create",
      "content": "print('AI generated')\n"
    }
  ],
  "commands": [],
  "evidence": [],
  "risks": []
}
```

The runtime applies these files only when paths are inside the AI-generated project layout and package `allowed_paths`. It rejects path traversal, absolute paths, `.git`, `.v6`, and artifact-store writes.

## Runtime Storage Lifecycle

The internal V6 runtime workspace is managed cache, not a permanent user delivery directory. Generated worktrees, artifacts, worker status, pressure reports, and logs are measured by the storage lifecycle manager. Delivery exports are user assets and are never deleted by automatic cleanup.

Default policy:

- exported worktrees are retained for 7 days before they become prune candidates
- artifacts and diagnostic evidence are retained for 30 days
- logs are retained for 14 days and pressure reports for 7 days
- cleanup defaults to dry-run; pass `--gc-apply` or `dry_run=false` only after reviewing the plan
- active runs, leased jobs, unresolved dead-letter jobs, unsafe paths, and user export targets are protected

Useful commands:

```powershell
python run_server.py --storage-report
python run_server.py --gc-workspace
python run_server.py --gc-workspace --gc-apply
```

## Important Endpoints

- `GET /api/v6/health`
- `GET /api/v6/health`
- `GET /api/v6/scale-profiles`
- `GET /api/v6/provider-health`
- `GET /api/v6/storage-report`
- `POST /api/v6/storage/gc`
- `GET /api/v6/runs/{id}/mission-state`
- `GET /api/v6/runs/{id}/storage`
- `GET /api/v6/system-check`
- `POST /api/v6/projects`
- `GET /api/v6/projects`
- `POST /api/v6/projects/{id}/runs`
- `GET /api/v6/runs/{id}`
- `GET /api/v6/runs/{id}/agent-runs`
- `GET /api/v6/runs/{id}/patch-sets`
- `GET /api/v6/runs/{id}/project-layout`
- `GET /api/v6/runs/{id}/dag`
- `GET /api/v6/runs/{id}/code-review`
- `GET /api/v6/runs/{id}/quality-report`
- `GET /api/v6/runs/{id}/deploy-guide`
- `POST /api/v6/runs/{id}/archive`
- `POST /api/v6/runs/{id}/prune`
- `POST /api/v6/runs/{id}/repair`
- `POST /api/v6/release-candidates/{id}/apply`
- `POST /api/v6/release-candidates/{id}/rollback`

## Environment

Use `V6_DATABASE_URL`; `DATABASE_URL` is accepted as fallback.

Runtime storage can be overridden with:

- `AI_AGENT_RUNTIME_ROOT`
- `AI_AGENT_WORKSPACE_MAX_BYTES`
- `AI_AGENT_GC_DRY_RUN_DEFAULT`
- `AI_AGENT_EXPORTED_WORKTREE_RETENTION_DAYS`
- `AI_AGENT_ARTIFACT_RETENTION_DAYS`
- `AI_AGENT_LOG_RETENTION_DAYS`

For real AI runs, configure an OpenAI-compatible provider:

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `OPENAI_MODEL`
- `OPENAI_WIRE_API`
- `OPENAI_PROVIDER_PROFILE`
- `OPENAI_API_PATH`

Without a configured LLM client, local memory-store tests can run with fake clients. Real durable runs classify provider failures; transient model disconnects enter `recovering` and retry from the job checkpoint before they are finally blocked.

## Parallel Execution And Purge Policy

The supervisor defaults to the selected scale profile. `xlarge_100k` starts multiple package workers for backend, frontend, QA, security, docs, and data work, while durable AI slots cap the actual provider and per-run concurrency. This lets independent package waves move faster without sending unbounded requests to the model provider.

When users cannot judge project size, choose `auto`. V6 starts from a conservative `medium` orchestration profile, records scale-inference evidence, and may upgrade after requirements, architecture, or package planning. Auto scale only upgrades; it does not silently downgrade an existing run.

The repository is V6-only. `system-check` includes a purge gate that scans source, docs, tests, logs, and workspace files for retired generation residue. Historical tasks should be exported outside this repository before cleanup; current control-plane views and database projections should only expose V6 records.

Storage lifecycle is part of the hosted-readiness check. `system-check` verifies that managed runtime roots exist, retention policy is valid, and workspace usage has not crossed the blocking quota.
