"""Tests for deploy doc generator."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dev_orchestrator.v8.deploy_doc import generate_deploy_doc


def _make_arch_artifact(tech_choices=None, layout=None):
    return {
        "kind": "architecture_design",
        "content": {
            "technology_choices": tech_choices or [
                {"name": "FastAPI", "category": "backend", "rationale": "async support"}
            ],
            "project_layout": layout or {
                "source_root": "src",
                "delivery_root": "dist",
                "directories": [{"path": "src/api", "purpose": "API routes"}],
                "entrypoints": [{"path": "src/main.py", "type": "server"}],
                "validation_commands": ["pytest tests/"],
            },
        },
    }


def _make_release_artifact():
    return {
        "kind": "release_notes",
        "content": {
            "deploy_steps": ["安装依赖: pip install -r requirements.txt", "启动服务: python main.py"],
            "validation_steps": ["访问 /health 端点", "运行冒烟测试"],
            "rollback_plan": "回滚到上一个 git tag",
        },
    }


def test_generate_deploy_doc_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        artifacts = [_make_arch_artifact(), _make_release_artifact()]
        path = generate_deploy_doc(
            target_dir=target,
            project={"title": "测试项目"},
            run_metadata={},
            artifacts=artifacts,
        )
        assert path.exists()
        assert path.name == "部署说明.md"


def test_deploy_doc_contains_project_name():
    with tempfile.TemporaryDirectory() as tmp:
        path = generate_deploy_doc(
            target_dir=Path(tmp),
            project={"title": "人事管理系统"},
            run_metadata={},
            artifacts=[_make_arch_artifact(), _make_release_artifact()],
        )
        content = path.read_text(encoding="utf-8")
        assert "人事管理系统" in content


def test_deploy_doc_no_unicode_escapes():
    """Chinese text must appear as-is, not as \\uXXXX escapes."""
    with tempfile.TemporaryDirectory() as tmp:
        path = generate_deploy_doc(
            target_dir=Path(tmp),
            project={"title": "中文项目"},
            run_metadata={},
            artifacts=[_make_arch_artifact(), _make_release_artifact()],
        )
        content = path.read_text(encoding="utf-8")
        assert "\\u" not in content
        assert "中文项目" in content


def test_deploy_doc_tech_stack_included():
    with tempfile.TemporaryDirectory() as tmp:
        path = generate_deploy_doc(
            target_dir=Path(tmp),
            project={"title": "proj"},
            run_metadata={},
            artifacts=[_make_arch_artifact(), _make_release_artifact()],
        )
        content = path.read_text(encoding="utf-8")
        assert "FastAPI" in content


def test_deploy_doc_fallback_with_empty_artifacts():
    """Should generate a valid doc even with no artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        path = generate_deploy_doc(
            target_dir=Path(tmp),
            project={"title": "空项目"},
            run_metadata={},
            artifacts=[],
        )
        content = path.read_text(encoding="utf-8")
        assert "空项目" in content
        assert "部署说明" in content
