# V6 100k AI-Native Control Plane

This repository now runs a V6 100k-oriented AI-native mission kernel with the V6 API kept as the compatibility surface.

The system is no longer a template renderer. Project layout, source files, tests, security notes, deployment notes, and repair patches are produced by AI agents through file-manifest patch sets. The runtime keeps durable jobs, worker leases, path safety, artifacts, recovery state, provider health, layered mission memory, and 100k-scale quality gates.

## What You Get

- `/api/v6/*` compatibility endpoints plus `/api/v6/*` mission-kernel endpoints
- four scale profiles (`small`, `medium`, `large`, `xlarge_100k`) that all use the 100k AI-native kernel
- Postgres durable jobs with worker leases and retries
- role workers for requirements, architecture, planning, code, QA, security, integration, review, repair, release, and docs
- provider resilience with transient error classification, retry state, and health snapshots
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

## Important Endpoints

- `GET /api/v6/health`
- `GET /api/v6/health`
- `GET /api/v6/scale-profiles`
- `GET /api/v6/provider-health`
- `GET /api/v6/runs/{id}/mission-state`
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
- `POST /api/v6/runs/{id}/repair`
- `POST /api/v6/release-candidates/{id}/apply`
- `POST /api/v6/release-candidates/{id}/rollback`

## Environment

Use `V6_DATABASE_URL`; `DATABASE_URL` is accepted as fallback.

For real AI runs, configure an OpenAI-compatible provider:

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `OPENAI_MODEL`
- `OPENAI_WIRE_API`
- `OPENAI_PROVIDER_PROFILE`
- `OPENAI_API_PATH`

Without a configured LLM client, local memory-store tests can run with fake clients. Real durable runs classify provider failures; transient model disconnects enter `recovering` and retry from the job checkpoint before they are finally blocked.
