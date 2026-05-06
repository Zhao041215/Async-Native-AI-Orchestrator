# Governance Report: Acceptance api-platform

- Task ID: `cb10026b-bb86-49ed-ac5c-e2b02ff3a1f5`
- Template: `api-platform`
- Status: `failed`
- Decision: `HOLD`
- Project: `acceptance-api-6c2adf`

## Conclusion

Current conclusion: task `failed` with blockers in failed validations: qa.

## Workflow

`planner -> architect -> backend -> qa -> integration-auditor -> reviewer -> devops`

## Budget

- `role_runs`: 7 / 24
- `llm_calls`: 0 / 36
- `conflicts`: 0 / 6
- `resumes`: 0 / 6
- `failures`: 1 / 8

## Risks

- Failed validations: `qa`

## Approval Profile

- `app_code`: `auto_apply`
- `infra`: `auto_apply`

## Role Configuration

- Extra Roles: `integration-auditor`
- Role Overrides:

- `backend`: `{'prompt_suffix': 'Favor contract durability, integration clarity, and runtime operability.'}`
- `qa`: `{'prompt_suffix': 'Bias toward contract checks, integration failure modes, and backward compatibility evidence.'}`
- `frontend`: `{'disable_tools': ['write_file', 'make_dir', 'run_shell'], 'prompt_suffix': 'Frontend work is minimized for this template; avoid inventing UI surfaces unless explicitly required.'}`

## Validation Profile

- `required_keywords_by_role`: `{'integration-auditor': ['contract', 'adapter', 'compatibility'], 'qa': ['Follow-up']}`

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