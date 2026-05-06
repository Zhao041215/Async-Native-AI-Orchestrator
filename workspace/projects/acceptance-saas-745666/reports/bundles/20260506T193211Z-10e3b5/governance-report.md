# Governance Report: Acceptance web-saas

- Task ID: `257a782e-8ace-45aa-a145-428ad37d7ce4`
- Template: `web-saas`
- Status: `failed`
- Decision: `HOLD`
- Project: `acceptance-saas-745666`

## Conclusion

Current conclusion: task `failed` with blockers in failed validations: qa.

## Workflow

`planner -> architect -> frontend -> backend -> qa -> security-review -> reviewer -> devops`

## Budget

- `role_runs`: 7 / 56
- `llm_calls`: 0 / 120
- `conflicts`: 0 / 16
- `resumes`: 0 / 16
- `failures`: 1 / 20

## Risks

- Failed validations: `qa`

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

- `qa`

## Validation Findings

- `qa` [high/evidence-missing] QA report does not contain passing compile and unit execution evidence.
- `qa` [high/verification-failed] QA report contains failed executed checks.

## Manual Review Queue

- None

## Conflict Queue

- None

## High Risk Blocks

- None