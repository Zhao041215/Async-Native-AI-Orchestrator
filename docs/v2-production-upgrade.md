# V2 Production Upgrade

## Position

V2 is a new control-plane core, not a patch on the V1 fallback workflow.

The first implemented slice adds:

- Chief-controlled project and run APIs under `/api/v2`
- requirement atom extraction and acceptance contracts
- DAG work package generation with parallel waves
- production skeleton generation for Docker Compose, Postgres, Redis, API, and worker boundaries
- quality gates that block PRD leakage, fallback artifacts, generic template code, weak QA, repeated code, and low domain relevance
- repair tasks instead of fake fallback delivery
- release candidates that return `NO_GO` when quality gates fail
- environment-only secret handling for API keys
- Chinese-first hosted console
- custom provider profiles for OpenAI-compatible third-party APIs
- pre-run `项目蓝图` automation scoring and continuation metadata

## Commands

```powershell
python -m compileall dev_orchestrator run_server.py
python -m unittest tests.test_v2_platform tests.test_governance_integrity tests.test_provider_profiles tests.test_console_localization tests.test_automation_maturity
python run_server.py --v2-regression-check --project-path E:\test
python run_server.py --v2-run-once --project-name hr-ai-v2 --title "HR AI Platform" --description "Must support resume parsing, HR policy Q&A RAG, attrition prediction, tenant isolation, SSO, audit logs, and zh-CN frontend labels."
```

## V2 API

- `POST /api/v2/projects`
- `GET /api/v2/model/profiles`
- `POST /api/v2/model/test-connection`
- `POST /api/v2/projects/{id}/requirements`
- `POST /api/v2/projects/{id}/runs`
- `GET /api/v2/runs/{id}/events`
- `GET /api/v2/projects/{id}/work-packages`
- `GET /api/v2/projects/{id}/coverage`
- `GET /api/v2/projects/{id}/quality-gates`
- `POST /api/v2/repair-tasks/{id}/run`
- `GET /api/v2/release-candidates/{id}`
- `POST /api/v2/approvals`

## Hard Rules

- Invalid model JSON blocks the agent contract; it never generates fallback artifacts.
- A project with fallback/normalized evidence cannot produce a GO release candidate.
- Must requirements require traceability before release.
- `E:\test` is a regression fixture and must be rejected.
- API keys are loaded from environment variables and are not persisted to `orchestrator_config.json`.
- Under-specified medium or large projects are blocked before execution with actionable repair prompts.
- Large-project `GO` requires Must mapping, passing subsystem gates, executable tests, and no fallback/normalized artifacts.
