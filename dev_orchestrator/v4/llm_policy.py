from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AITaskBudget:
    task_kind: str
    max_input_chars: int
    max_output_tokens: int
    timeout_seconds: int
    reasoning_effort: str
    retry_attempts: int
    model_tier: str
    allow_degraded: bool
    critical: bool

    def to_dict(self) -> dict:
        return asdict(self)


AI_TASK_BUDGETS: dict[str, AITaskBudget] = {
    "chief_plan": AITaskBudget(
        task_kind="chief_plan",
        max_input_chars=4500,
        max_output_tokens=700,
        timeout_seconds=60,
        reasoning_effort="medium",
        retry_attempts=1,
        model_tier="tier_strong",
        allow_degraded=False,
        critical=True,
    ),
    "contract_design": AITaskBudget(
        task_kind="contract_design",
        max_input_chars=5000,
        max_output_tokens=900,
        timeout_seconds=75,
        reasoning_effort="medium",
        retry_attempts=1,
        model_tier="tier_strong",
        allow_degraded=False,
        critical=True,
    ),
    "package_plan": AITaskBudget(
        task_kind="package_plan",
        max_input_chars=3500,
        max_output_tokens=550,
        timeout_seconds=60,
        reasoning_effort="low",
        retry_attempts=1,
        model_tier="tier_standard",
        allow_degraded=False,
        critical=False,
    ),
    "code_generation": AITaskBudget(
        task_kind="code_generation",
        max_input_chars=5000,
        max_output_tokens=1200,
        timeout_seconds=90,
        reasoning_effort="medium",
        retry_attempts=1,
        model_tier="tier_strong",
        allow_degraded=False,
        critical=True,
    ),
    "code_review": AITaskBudget(
        task_kind="code_review",
        max_input_chars=6500,
        max_output_tokens=1000,
        timeout_seconds=90,
        reasoning_effort="medium",
        retry_attempts=1,
        model_tier="tier_strong",
        allow_degraded=False,
        critical=True,
    ),
    "test_generation": AITaskBudget(
        task_kind="test_generation",
        max_input_chars=5000,
        max_output_tokens=700,
        timeout_seconds=75,
        reasoning_effort="medium",
        retry_attempts=1,
        model_tier="tier_standard",
        allow_degraded=False,
        critical=False,
    ),
    "failure_analysis": AITaskBudget(
        task_kind="failure_analysis",
        max_input_chars=6000,
        max_output_tokens=900,
        timeout_seconds=75,
        reasoning_effort="medium",
        retry_attempts=1,
        model_tier="tier_strong",
        allow_degraded=False,
        critical=True,
    ),
    "release_notes": AITaskBudget(
        task_kind="release_notes",
        max_input_chars=4500,
        max_output_tokens=650,
        timeout_seconds=45,
        reasoning_effort="low",
        retry_attempts=1,
        model_tier="tier_fast",
        allow_degraded=True,
        critical=False,
    ),
    "context_summary": AITaskBudget(
        task_kind="context_summary",
        max_input_chars=4500,
        max_output_tokens=650,
        timeout_seconds=45,
        reasoning_effort="low",
        retry_attempts=1,
        model_tier="tier_fast",
        allow_degraded=True,
        critical=False,
    ),
}

AI_TASK_BUDGETS["chief"] = AI_TASK_BUDGETS["chief_plan"]
AI_TASK_BUDGETS["package"] = AI_TASK_BUDGETS["package_plan"]
AI_TASK_BUDGETS["repair"] = AI_TASK_BUDGETS["failure_analysis"]
AI_TASK_BUDGETS["review"] = AI_TASK_BUDGETS["code_review"]


def get_ai_task_budget(task_kind: str) -> AITaskBudget:
    return AI_TASK_BUDGETS.get(task_kind, AI_TASK_BUDGETS["package_plan"])


def list_ai_task_budgets() -> list[dict]:
    seen: set[str] = set()
    budgets = []
    for budget in AI_TASK_BUDGETS.values():
        if budget.task_kind in seen:
            continue
        seen.add(budget.task_kind)
        budgets.append(budget.to_dict())
    return budgets


def is_core_task(task_kind: str) -> bool:
    return get_ai_task_budget(task_kind).critical
