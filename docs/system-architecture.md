# Hosted V2 System Architecture

## Goal

The platform is now a hosted V2 control plane for governed agent delivery. V1 local task orchestration has been removed.

Primary priorities:

- tenant-scoped execution
- explicit identity
- auditable run artifacts
- real patch and test evidence
- persisted approval before release
- worker job visibility
- Chinese-first operator console
- first-class custom OpenAI-compatible provider support
- gated automation for medium and large projects

## Core Flow

1. A tenant-scoped user creates a V2 project.
2. Requirements are parsed into atoms, contracts, subsystems, and DAG work packages.
3. The Chief creates a `项目蓝图` with requirement completeness, subsystem boundaries, risk, and decomposition score.
4. Under-specified medium or large projects are blocked with repair prompts before execution.
5. The Chief creates a run and worker job.
6. Agents work in dependency-aware DAG waves in isolated git worktrees and produce patch sets.
7. The Chief integrates patches, runs tests, records quality gate history, and creates a release candidate.
8. `GO` candidates can be approved and applied; `NO_GO` candidates cannot be approved.
9. Audit events record project creation, run dispatch, approval, and blocked approval attempts.

## Hosted Boundaries

- Identity: `X-Local-User` maps to a tenant-scoped user.
- Tenant: `X-Tenant-Id` selects the active tenant.
- Queue: `local-adapter` records worker jobs locally; `redis` is the production-compatible target.
- Storage: SQLite local baseline with hosted tables for tenants, users, approvals, audit events, and worker jobs.
- Secrets: API keys are loaded from environment variables and not persisted.
- Model providers: `openai-chat-completions`, `openai-responses`, `custom-chat-compatible`, and `custom-responses-compatible`.
- Automation metadata: runs store `decomposition_score`, `autonomy_level`, `repair_round_count`, `quality_gate_history`, and `continuation_state`.

## Public Surface

Supported APIs are `/api/health`, `/api/system-check`, `/api/acceptance-check`, `/api/config`, `/api/v2/model/profiles`, `/api/v2/model/test-connection`, and `/api/v2/*`.

Legacy `/api/tasks` and `/api/changes` return `410 Gone`.

## Release Safeguards

A large-project release cannot be `GO` unless Must requirements map to work packages, required subsystem gates pass, executable tests exist, and fallback or normalized artifacts are absent. Failed safeguards are saved on the release candidate as `NO_GO` blockers.
