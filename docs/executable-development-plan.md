# Hosted V2 Executable Development Plan

## Current Position

The repository has completed the hard cut to hosted V2. V1 local orchestration modules and legacy task/change routes are removed.

## Non-Negotiable Constraints

- no V1 task API compatibility layer
- tenant and identity context required for project/run operations
- approvals persist before release
- `NO_GO` candidates cannot be approved
- worker jobs and audit events remain queryable
- custom OpenAI-compatible provider settings remain first-class
- Chinese console copy remains centralized and test-scanned
- medium and large automation remains gated by `项目蓝图` and release safeguards
- all release work must pass the full stability gate

## Required Stability Gate

```powershell
python -m compileall dev_orchestrator run_server.py
python -m unittest tests.test_v2_platform tests.test_governance_integrity tests.test_provider_profiles tests.test_console_localization tests.test_automation_maturity
python run_server.py --system-check
python run_server.py --acceptance-check
python run_server.py --v2-regression-check --project-path E:\test
```

## Next Execution Package

1. Validate third-party model providers through `/api/v2/model/test-connection` before starting long runs.
2. Use `项目蓝图` to block under-specified medium and large project requests.
3. Execute dependency-aware DAG waves, record continuation state, and rerun repair loops when tests or gates fail.
4. Promote a release candidate only when Must mapping, tests, quality gates, and fallback-artifact safeguards pass.
5. Implement a real Redis-backed worker runner while preserving the local adapter for deterministic tests.
