# Hosted V2 Release Baseline

Current release:

`v2.0.0-hosted-control-plane`

## Baseline

- V2-only public API surface
- tenant-scoped projects and runs
- persisted users, tenants, approvals, audit events, and worker jobs
- Chief-controlled DAG execution with git worktree isolation
- GO/NO_GO release candidates
- V2-native acceptance checks
- hosted-local defaults for identity, queue, sandbox, and worker boundaries

## Required Checks

```powershell
python -m compileall dev_orchestrator run_server.py
python -m unittest tests.test_v2_platform tests.test_governance_integrity
python run_server.py --system-check
python run_server.py --acceptance-check
python run_server.py --v2-regression-check --project-path E:\test
```
