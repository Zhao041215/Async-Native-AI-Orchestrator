"""V8 Deploy Doc Generator — template-driven, no AI calls.

Reads architecture and release_notes artifacts from the run and generates
a Chinese deployment guide (部署说明.md) plus one-click startup scripts
(start.sh / start.bat) in the target directory.
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
    """Generate 部署说明.md (and start.sh / start.bat) in target_dir. Returns the doc path."""
    arch = _find_artifact(artifacts, "architecture_design") or {}
    release = _find_artifact(artifacts, "release_notes") or {}
    requirements = run_metadata.get("requirements") or {}
    layout = run_metadata.get("project_layout") or arch.get("project_layout") or {}
    tech_choices = arch.get("technology_choices") or []
    deploy_steps = release.get("deploy_steps") or []
    validation_steps = release.get("validation_steps") or []
    rollback_plan = release.get("rollback_plan") or ""
    env_vars: list[dict[str, Any]] = release.get("env_vars") or []
    ports: list[dict[str, Any]] = release.get("ports") or []
    startup_commands: list[str] = release.get("startup_commands") or []
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

    # --- Environment variables table ---
    env_section = ""
    if env_vars:
        rows = "\n".join(
            f"| `{v.get('name', '')}` | {v.get('description', '')} | `{v.get('example', '')}` | {'是' if v.get('required') else '否'} |"
            for v in env_vars
        )
        env_section = f"""## 环境变量

| 变量名 | 说明 | 示例值 | 必填 |
|--------|------|--------|------|
{rows}

> 复制 `.env.example` 为 `.env` 并填写实际值。
"""
    else:
        env_section = """## 环境变量

- 复制 `.env.example` 为 `.env` 并填写实际值
- 请参考项目根目录的 `package.json` / `requirements.txt` / `Cargo.toml` 等依赖文件
"""

    # --- Ports table ---
    ports_section = ""
    if ports:
        rows = "\n".join(
            f"| {p.get('port', '')} | {p.get('service', '')} | {p.get('protocol', 'http')} |"
            for p in ports
        )
        ports_section = f"""## 端口说明

| 端口 | 服务 | 协议 |
|------|------|------|
{rows}
"""

    # --- Startup commands block ---
    startup_section = ""
    if startup_commands:
        cmds = "\n".join(startup_commands)
        startup_section = f"""## 一键启动

```bash
{cmds}
```
"""

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

{env_section}
{ports_section}
## 安装步骤

{deploy_lines}

{startup_section}
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

    # P1-7: Generate one-click startup scripts
    _write_startup_scripts(target_dir, startup_commands, project_name)

    return out_path


def _write_startup_scripts(
    target_dir: Path,
    startup_commands: list[str],
    project_name: str,
) -> None:
    """Generate start.sh (Linux/Mac) and start.bat (Windows) in target_dir."""
    if not startup_commands:
        # Provide sensible defaults when AI didn't generate startup commands
        startup_commands = [
            "# 请根据项目技术栈选择以下命令之一：",
            "# Node.js:   npm install && npm start",
            "# Python:    pip install -r requirements.txt && python main.py",
            "# Docker:    docker-compose up --build",
        ]

    sh_lines = [
        "#!/bin/bash",
        f"# 一键启动脚本 — {project_name}",
        "# 由 AI Agent Orchestrator V8 自动生成",
        "set -e",
        "",
        "# 复制环境变量文件（如果尚未存在）",
        "if [ ! -f .env ] && [ -f .env.example ]; then",
        "  cp .env.example .env",
        "  echo '已从 .env.example 创建 .env，请编辑填写实际配置'",
        "fi",
        "",
    ] + startup_commands

    bat_lines = [
        "@echo off",
        f"REM 一键启动脚本 — {project_name}",
        "REM 由 AI Agent Orchestrator V8 自动生成",
        "",
        "REM 复制环境变量文件（如果尚未存在）",
        "IF NOT EXIST .env IF EXIST .env.example (",
        "  copy .env.example .env",
        "  echo 已从 .env.example 创建 .env，请编辑填写实际配置",
        ")",
        "",
    ] + [
        # Convert bash-style comments to batch comments
        f"REM {cmd[1:].strip()}" if cmd.startswith("#") else cmd
        for cmd in startup_commands
        if cmd.strip()
    ]

    (target_dir / "start.sh").write_text("\n".join(sh_lines) + "\n", encoding="utf-8")
    (target_dir / "start.bat").write_text("\r\n".join(bat_lines) + "\r\n", encoding="utf-8")


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
