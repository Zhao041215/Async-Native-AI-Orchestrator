# V5 AI-Agent Native Control Plane

This repository now runs a V5-only multi-agent delivery control plane.

V5 is no longer a template renderer. Project layout, source files, tests, security notes, deployment notes, and repair patches are produced by AI agents through file-manifest patch sets. The system keeps the durable queue, workers, path safety, artifacts, and quality gates.

## What You Get

- `/api/v5/*` only
- Postgres durable jobs with worker leases and retries
- role workers for requirements, architecture, planning, code, QA, security, integration, review, repair, release, and docs
- AI-generated `project-layout.json`
- AI-generated `package-dag.json`
- AI-generated file-manifest `patch_set` artifacts
- quality gates that verify AI origin, path boundaries, review evidence, test evidence, anti-template leakage, and model budget
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
docker compose -f docker-compose.v5.yml up -d --build
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

The runtime applies these files only when paths are inside the AI-generated project layout and package `allowed_paths`. It rejects path traversal, absolute paths, `.git`, `.v5`, and artifact-store writes.

## Important Endpoints

- `GET /api/v5/health`
- `GET /api/v5/system-check`
- `POST /api/v5/projects`
- `GET /api/v5/projects`
- `POST /api/v5/projects/{id}/runs`
- `GET /api/v5/runs/{id}`
- `GET /api/v5/runs/{id}/agent-runs`
- `GET /api/v5/runs/{id}/patch-sets`
- `GET /api/v5/runs/{id}/project-layout`
- `GET /api/v5/runs/{id}/dag`
- `GET /api/v5/runs/{id}/code-review`
- `GET /api/v5/runs/{id}/quality-report`
- `GET /api/v5/runs/{id}/deploy-guide`
- `POST /api/v5/runs/{id}/repair`
- `POST /api/v5/release-candidates/{id}/apply`
- `POST /api/v5/release-candidates/{id}/rollback`

## Environment

Use `V5_DATABASE_URL`; `DATABASE_URL` is accepted as fallback.

For real AI runs, configure an OpenAI-compatible provider:

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `OPENAI_MODEL`
- `OPENAI_WIRE_API`
- `OPENAI_PROVIDER_PROFILE`
- `OPENAI_API_PATH`

Without a configured LLM client, local memory-store tests can run with fake clients, but real durable runs block and record `llm_call_failed`.
