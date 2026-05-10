"""V7 Scale Inference - auto-detect project scale from signals."""
from __future__ import annotations

from typing import Any

SCALE_ORDER = {"small": 0, "medium": 1, "large": 2, "xlarge_100k": 3}


def choose_larger_scale(current: str, candidate: str) -> str:
    c = normalize_scale_name(current)
    d = normalize_scale_name(candidate)
    return d if SCALE_ORDER.get(d, 0) > SCALE_ORDER.get(c, 0) else c


def normalize_scale_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {"": "medium", "auto": "medium", "tiny": "small", "s": "small", "m": "medium", "l": "large", "xlarge": "xlarge_100k", "xl": "xlarge_100k", "100k": "xlarge_100k"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in SCALE_ORDER else "medium"


def infer_initial_scale(config: dict[str, Any] | None, description: str = "") -> dict[str, Any]:
    config = config or {}
    requested = str(config.get("target_scale") or "auto").strip().lower() or "auto"
    if requested not in {"", "auto"}:
        return {"stage": "initial", "requested_scale": requested, "selected_scale": normalize_scale_name(requested), "confidence": "explicit", "auto": False}
    signals = _text_signals(description)
    selected = _scale_from_score(signals["score"])
    return {"stage": "initial", "requested_scale": "auto", "selected_scale": selected, "confidence": "inferred", "auto": True, "signals": signals}


def infer_scale_from_requirements(analysis: dict[str, Any], text: str) -> dict[str, Any]:
    goals = analysis.get("goals") or []
    constraints = analysis.get("constraints") or []
    score = len(goals) * 2 + len(constraints)
    selected = _scale_from_score(score)
    return {"stage": "requirements", "selected_scale": selected, "goal_count": len(goals), "constraint_count": len(constraints)}


def infer_scale_from_architecture(design: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    dirs = layout.get("directories") or []
    modules = design.get("module_boundaries") or []
    score = len(dirs) + len(modules) * 2
    selected = _scale_from_score(score)
    return {"stage": "architecture", "selected_scale": selected, "directory_count": len(dirs), "module_count": len(modules)}


def infer_scale_from_package_plan(plan: dict[str, Any]) -> dict[str, Any]:
    packages = plan.get("packages") or []
    waves = plan.get("waves") or []
    score = len(packages) * 3 + len(waves) * 2
    selected = _scale_from_score(score)
    return {"stage": "package_plan", "selected_scale": selected, "package_count": len(packages), "wave_count": len(waves)}


def merge_inference_history(history: list[dict[str, Any]] | None, entry: dict[str, Any]) -> list[dict[str, Any]]:
    return (history or []) + [entry]


def _text_signals(text: str) -> dict[str, Any]:
    lower = text.lower()
    score = 0
    if any(w in lower for w in ("enterprise", "production", "scale", "distributed")):
        score += 4
    if any(w in lower for w in ("microservice", "kubernetes", "k8s", "cluster")):
        score += 3
    if any(w in lower for w in ("api", "database", "auth", "authentication")):
        score += 2
    if any(w in lower for w in ("simple", "prototype", "demo", "toy", "hello world")):
        score -= 2
    return {"score": max(0, score)}


def _scale_from_score(score: int) -> str:
    if score >= 12:
        return "xlarge_100k"
    if score >= 7:
        return "large"
    if score >= 3:
        return "medium"
    return "small"
