# Governance Report: Acceptance general-product

- Task ID: `b7e9e7ed-718a-4b25-8e5f-163ed7345fa1`
- Template: `general-product`
- Status: `failed`
- Decision: `HOLD`
- Project: `acceptance-general-e2b2a0`

## Conclusion

Current conclusion: task `failed` with blockers in failed validations: qa.

## Workflow

`planner -> architect -> frontend -> backend -> qa -> reviewer -> devops`

## Budget

- `role_runs`: 7 / 20
- `llm_calls`: 0 / 40
- `conflicts`: 0 / 8
- `resumes`: 0 / 6
- `failures`: 1 / 8

## Risks

- Failed validations: `qa`

## Approval Profile

- `app_code`: `auto_apply`
- `infra`: `auto_apply`

## Role Configuration

- Extra Roles: `-`
- Role Overrides:

- None

## Validation Profile

- None

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