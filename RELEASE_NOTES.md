# Release Notes

## Version

`v2.0.0-hosted-control-plane`

## Delivery Status

This release is a hard cut from V1 to hosted V2.

- V1 runtime modules and legacy task/change APIs were removed.
- The browser console is now V2-only and Chinese-first.
- Projects, runs, approvals, audit events, and worker jobs are tenant scoped.
- Release approval is persisted and rejects blocked or `NO_GO` candidates.
- Acceptance checks now exercise V2 GO and NO_GO flows instead of V1 QA artifacts.
- System checks enforce hosted identity, tenant, queue, sandbox, and worker defaults.
- Custom third-party OpenAI-compatible APIs are first-class through provider profiles.
- A model configuration panel supports profile selection, masked key entry, connection testing, timeout, retry, max-token, and temperature settings.
- V2 runs now include `项目蓝图`, decomposition score, autonomy level, repair round count, quality gate history, and continuation state.
- Large-project safeguards block `GO` unless Must mapping, subsystem quality gates, tests, and fallback-artifact checks pass.

## Supported Surface

- `/api/health`
- `/api/system-check`
- `/api/acceptance-check`
- `/api/config`
- `/api/v2/model/profiles`
- `/api/v2/model/test-connection`
- `/api/v2/*`

Legacy `/api/tasks` and `/api/changes` return `410 Gone`.

## Stability Gate

The release is expected to pass:

- `python -m compileall dev_orchestrator run_server.py`
- `python -m unittest tests.test_v2_platform tests.test_governance_integrity`
- `python run_server.py --system-check`
- `python run_server.py --acceptance-check`
- `python run_server.py --v2-regression-check --project-path E:\test`
