# Governance Report: Build a medium-sized operations management platform

- Task ID: `a64cd6ba-d60f-47e6-92f0-2fbb3709c909`
- Template: `web-saas`
- Status: `completed`
- Decision: `HOLD`
- Project: `medium-ops-suite-final-47b05d`

## Conclusion

Current conclusion: task `completed` with blockers in failed validations: artifact_integrity.

## Workflow

`planner -> architect -> frontend -> backend -> qa -> security-review -> reviewer -> devops`

## Budget

- `role_runs`: 10 / 56
- `llm_calls`: 0 / 120
- `conflicts`: 0 / 16
- `resumes`: 0 / 16
- `failures`: 0 / 20

## Risks

- Failed validations: `artifact_integrity`

## Approval Profile

- `app_code`: `auto_apply`
- `infra`: `auto_apply`

## Role Configuration

- Extra Roles: `security-review`
- Role Overrides:

- `frontend`: `{'prompt_suffix': 'Prioritize dashboard clarity, multi-tenant UX consistency, and operator visibility.'}`
- `reviewer`: `{'prompt_suffix': 'Pay extra attention to tenancy boundaries, admin misuse, and billing-adjacent risk.'}`

## Validation Profile

- `required_keywords_by_role`: `{'security-review': ['tenant', 'auth', 'audit'], 'reviewer': ['risk']}`

## Failed Validations

- `artifact_integrity`

## Validation Findings

- `artifact_integrity` [high/delivery-drift] Recorded delivery artifacts are missing from the live project workspace.

## Manual Review Queue

- None

## Conflict Queue

- None

## High Risk Blocks

- None