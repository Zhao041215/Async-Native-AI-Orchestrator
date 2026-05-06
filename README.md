# Hosted V2 Control Plane

This repository now runs a V2-only hosted multi-user control plane for governed agent delivery.

V1 task orchestration, legacy `/api/tasks`, legacy `/api/changes`, template delivery bundles, and the old local console have been removed. The supported product surface is `/api/v2/*`.

## What You Get

- tenant-scoped projects and runs
- hosted dev-session identity bootstrap
- requirement extraction and acceptance contracts
- Chief-generated DAG work packages
- isolated git worktrees and patch-set capture
- executable test evidence and V2 quality gates
- GO/NO_GO release candidates
- persisted approvals
- tenant audit events
- worker job lifecycle records
- Chinese-first V2 browser console
- custom OpenAI-compatible provider profiles

## Quick Start

```powershell
python run_server.py
```

Open:

```text
http://127.0.0.1:8787
```

## Verification

```powershell
python -m compileall dev_orchestrator run_server.py
python -m unittest tests.test_v2_platform tests.test_governance_integrity
python run_server.py --system-check
python run_server.py --acceptance-check
python run_server.py --v2-regression-check --project-path E:\test
```

## CLI

```powershell
python run_server.py --system-check
python run_server.py --acceptance-check
python run_server.py --llm-contract-check
python run_server.py --v2-regression-check --project-path E:\test
python run_server.py --v2-run-once --project-name hr-ai-v2 --title "HR AI Platform" --description "Must support resume parsing, tenant isolation, SSO, audit logs, and zh-CN frontend labels."
```

`--v2-run-once` follows `orchestrator_config.json`. If `llm.use_mock=false`, provide `OPENAI_API_KEY` or `DEV_ORCHESTRATOR_API_KEY`; otherwise the run is intentionally blocked with a `NO_GO` runtime-config finding.

## Custom Model Providers

Custom third-party APIs are a first-class V2 feature when they are OpenAI-compatible or near-compatible HTTP APIs.

Supported provider profiles:

- `openai-chat-completions`
- `openai-responses`
- `custom-chat-compatible`
- `custom-responses-compatible`

Configurable fields include `api_base`, `api_path`, `api_key`, `model`, `wire_api`, `auth_header`, `auth_scheme`, `extra_headers`, `timeout_seconds`, `retry_attempts`, `max_tokens`, and `temperature`. Environment variables take precedence for secrets and common endpoint settings:

- `DEV_ORCHESTRATOR_API_KEY` or `OPENAI_API_KEY`
- `DEV_ORCHESTRATOR_API_BASE` or `OPENAI_API_BASE`
- `DEV_ORCHESTRATOR_MODEL` or `OPENAI_MODEL`
- `DEV_ORCHESTRATOR_PROVIDER_PROFILE`
- `DEV_ORCHESTRATOR_API_PATH`
- `DEV_ORCHESTRATOR_AUTH_HEADER`
- `DEV_ORCHESTRATOR_AUTH_SCHEME`
- `DEV_ORCHESTRATOR_EXTRA_HEADERS`

API keys are masked in `GET /api/config` and are not persisted in plaintext by `POST /api/config`.

The Chinese console exposes a provider settings panel and test button. You can also call:

- `GET /api/v2/model/profiles`
- `POST /api/v2/model/test-connection`

## Medium And Large Automation

Every run now starts with a `项目蓝图` stage. The blueprint scores requirement completeness and decomposition, identifies subsystem boundaries, estimates risk, and blocks under-specified projects with repair prompts before costly execution begins.

Runs expose automation metadata: `decomposition_score`, `autonomy_level`, `repair_round_count`, `quality_gate_history`, and `continuation_state`. Projects default to `supervised_auto`; supported modes are `guided`, `supervised_auto`, and `full_auto_candidate`.

Large-project release safeguards keep `GO` unavailable unless Must requirements map to work packages, required gates pass, executable tests exist, and fallback or normalized artifacts are absent.

## HTTP API

- `GET /api/health`
- `GET /api/system-check`
- `GET /api/acceptance-check`
- `GET /api/config`
- `POST /api/config`
- `GET /api/v2/model/profiles`
- `POST /api/v2/model/test-connection`
- `POST /api/v2/auth/dev-session`
- `GET /api/v2/auth/dev-session`
- `GET /api/v2/tenants/current`
- `GET /api/v2/audit-events`
- `GET /api/v2/worker-jobs`
- `GET /api/v2/durable-jobs`
- `GET /api/v2/benchmarks/enterprise-saas`
- `GET /api/v2/projects`
- `POST /api/v2/projects`
- `POST /api/v2/projects/{id}/requirements`
- `POST /api/v2/projects/{id}/blueprint/validate`
- `POST /api/v2/projects/{id}/runs`
- `GET /api/v2/projects/{id}`
- `GET /api/v2/projects/{id}/work-packages`
- `GET /api/v2/projects/{id}/coverage`
- `GET /api/v2/projects/{id}/quality-gates`
- `GET /api/v2/runs/{id}`
- `GET /api/v2/runs/{id}/continuation`
- `GET /api/v2/runs/{id}/artifacts`
- `GET /api/v2/runs/{id}/events`
- `GET /api/v2/runs/{id}/agent-runs`
- `GET /api/v2/runs/{id}/patches`
- `GET /api/v2/runs/{id}/tests`
- `GET /api/v2/runs/{id}/release-patch`
- `POST /api/v2/runs/{id}/pause`
- `POST /api/v2/runs/{id}/resume`
- `POST /api/v2/runs/{id}/cancel`
- `POST /api/v2/repair-tasks/{id}/run`
- `GET /api/v2/release-candidates/{id}`
- `POST /api/v2/release-candidates/{id}/apply`
- `POST /api/v2/release-candidates/{id}/auto-apply`
- `POST /api/v2/release-candidates/{id}/rollback`
- `POST /api/v2/approvals`

Legacy V1 routes return `410 Gone`.

## Durable Workers

Runs are persisted in `v2_durable_jobs` and can be executed by local worker processes instead of the in-process adapter:

```bash
python run_server.py --worker --role qa --tenant local-workspace
python run_server.py --worker --role planner --tenant local-workspace --once
python run_server.py --worker-supervisor --tenant local-workspace
```

The worker path claims jobs with a lease, refreshes heartbeat/lease while executing, requeues expired leases, and moves exhausted retries to `dead_letter`. The local adapter still supports synchronous tests and quick demos, but it also executes through durable job claim/finish state.

## Hosted Defaults

`orchestrator_config.json` uses hosted-local defaults:

- `runtime.deployment_mode`: `hosted-multi-user-local`
- `runtime.queue_mode`: `local-adapter`
- `runtime.sandbox_mode`: `tenant-governed-worktree`
- `identity.tenant_mode`: `required-tenant`
- `production.worker_model`: `hosted-worker`
- API keys remain environment-only and are not persisted.
