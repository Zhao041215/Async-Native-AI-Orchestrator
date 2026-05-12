"""V8 Deploy Doc Generator — template-driven, no AI calls.

Reads architecture and release_notes artifacts from the run and generates
a Chinese deployment guide (部署说明.md) in the target directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_deploy_doc(
    *,
    target_dir: Path,
    project: dict[str, Any],
    run_metadata: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> Path:
    """Generate 部署说明.md in target_dir. Returns the written path."""
    arch = _find_artifact(artifacts, "architecture_design") or {}
    release = _find_artifact(artifacts, "release_notes") or {}
    requirements = run_metadata.get("requirements") or {}
    layout = run_metadata.get("project_layout") or arch.get("project_layout") or {}
    tech_choices = arch.get("technology_choices") or []
    deploy_steps = release.get("deploy_steps") or []
    validation_steps = release.get("validation_steps") or []
    rollback_plan = release.get("rollback_plan") or ""
    scale_profile = run_metadata.get("scale_profile") or {}

    project_name = project.get("title") or project.get("name") or "项目"
    description = project.get("description") or requirements.get("summary") or ""
    source_root = layout.get("source_root") or "src"
    delivery_root = layout.get("delivery_root") or "dist"
    directories = layout.get("directories") or []
    validation_commands = layout.get("validation_commands") or []
    entrypoints = layout.get("entrypoints") or []

    tech_lines = "\n".join(
        f"- **{t.get('name', '')}** ({t.get('category', '')}): {t.get('rationale', '')}"
        for t in tech_choices
    ) or "- 请参考项目代码"

    dir_lines = "\n".join(
        f"- `{d.get('path', '')}` — {d.get('purpose', '')}"
        for d in directories
    ) or f"- `{source_root}/` — 源代码\n- `{delivery_root}/` — 构建产物"

    entry_lines = "\n".join(
        f"- `{e.get('path', '')}` ({e.get('type', '')})"
        for e in entrypoints
    ) or "- 请参考项目 README"

    deploy_lines = "\n".join(
        f"{i + 1}. {step}" for i, step in enumerate(deploy_steps)
    ) or "1. 安装依赖\n2. 配置环境变量\n3. 启动服务"

    validate_lines = "\n".join(
        f"{i + 1}. {step}" for i, step in enumerate(validation_steps)
    ) or "1. 访问服务健康检查端点\n2. 运行冒烟测试"

    cmd_lines = "\n".join(f"```\n{cmd}\n```" for cmd in validation_commands) or ""

    scale_name = scale_profile.get("name") or "medium"
    target_loc = scale_profile.get("target_loc_hint") or ""
    loc_note = f"（目标规模：{scale_name}，约 {target_loc} 行代码）" if target_loc else f"（规模：{scale_name}）"

    doc = f"""# {project_name} — 部署说明

> 本文档由 AI Agent Orchestrator V8 自动生成 {loc_note}

## 项目概述

{description or "请参考需求文档。"}

## 技术栈

{tech_lines}

## 目录结构

{dir_lines}

## 入口点

{entry_lines}

## 环境要求

- 请参考项目根目录的 `package.json` / `requirements.txt` / `Cargo.toml` 等依赖文件
- 配置 `.env` 文件（参考 `.env.example`）

## 安装步骤

{deploy_lines}

## 验证步骤

{validate_lines}

{("## 验证命令\n\n" + cmd_lines) if cmd_lines else ""}

## 回滚方案

{rollback_plan or "请参考版本控制历史，回滚到上一个稳定版本。"}

---

*由 AI Agent Orchestrator V8 生成*
"""

    out_path = target_dir / "部署说明.md"
    out_path.write_text(doc.strip() + "\n", encoding="utf-8")
    return out_path


def _find_artifact(artifacts: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for a in reversed(artifacts):
        if a.get("kind") == kind:
            content = a.get("content", "")
            if isinstance(content, str) and content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(content, dict):
                return content
    return None
