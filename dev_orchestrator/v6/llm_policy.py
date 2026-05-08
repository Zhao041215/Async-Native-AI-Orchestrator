from __future__ import annotations

from dataclasses import asdict, dataclass, replace


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

    def with_retry_attempts(self, attempts: int) -> "AITaskBudget":
        return replace(self, retry_attempts=max(1, int(attempts or self.retry_attempts)))


AI_TASK_BUDGETS: dict[str, AITaskBudget] = {
    "requirements_analysis": AITaskBudget("requirements_analysis", 9000, 1600, 90, "medium", 3, "tier_strong", False, True),
    "architecture_design": AITaskBudget("architecture_design", 12000, 2400, 120, "high", 3, "tier_strong", False, True),
    "package_planning": AITaskBudget("package_planning", 10000, 2200, 90, "medium", 3, "tier_strong", False, True),
    "code_generation": AITaskBudget("code_generation", 16000, 4500, 150, "high", 3, "tier_strong", False, True),
    "test_generation": AITaskBudget("test_generation", 12000, 2600, 120, "medium", 3, "tier_standard", False, True),
    "security_review": AITaskBudget("security_review", 12000, 1800, 90, "medium", 3, "tier_strong", False, True),
    "integration_merge": AITaskBudget("integration_merge", 16000, 3600, 150, "high", 3, "tier_strong", False, True),
    "code_review": AITaskBudget("code_review", 18000, 2600, 120, "high", 3, "tier_strong", False, True),
    "failure_analysis": AITaskBudget("failure_analysis", 14000, 3200, 120, "high", 3, "tier_strong", False, True),
    "release_notes": AITaskBudget("release_notes", 12000, 2200, 75, "medium", 2, "tier_standard", True, True),
    "context_summary": AITaskBudget("context_summary", 8000, 1000, 60, "low", 2, "tier_fast", True, False),
}

AI_TASK_BUDGETS["repair"] = AI_TASK_BUDGETS["failure_analysis"]
AI_TASK_BUDGETS["review"] = AI_TASK_BUDGETS["code_review"]


def get_ai_task_budget(task_kind: str) -> AITaskBudget:
    return AI_TASK_BUDGETS.get(task_kind, AI_TASK_BUDGETS["code_generation"])


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
