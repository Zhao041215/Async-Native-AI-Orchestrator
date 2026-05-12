"""Tests for artifact writer — verifies ensure_ascii=False behavior."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from dev_orchestrator.v8.artifacts import ArtifactWriter


def test_artifact_write_preserves_chinese():
    """Artifacts written to disk must not escape Chinese characters."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            writer = ArtifactWriter(base_dir=Path(tmp))
            content = {"summary": "这是中文内容", "items": ["测试", "验证"]}
            path = await writer.write_json("proj-1", "run-1", "job-1", "test_kind", "test.json", content)
            raw = Path(path).read_text(encoding="utf-8")
            assert "这是中文内容" in raw, "Chinese characters must not be escaped in artifact files"
            assert "\\u" not in raw, "No unicode escapes should appear in artifact output"

    asyncio.run(_run())


def test_artifact_write_json_roundtrip():
    """Written artifact must be valid JSON and round-trip correctly."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            writer = ArtifactWriter(base_dir=Path(tmp))
            content = {"key": "value", "nested": {"chinese": "中文"}}
            path = await writer.write_json("proj-1", "run-1", "job-1", "test_kind", "test.json", content)
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            assert loaded["nested"]["chinese"] == "中文"

    asyncio.run(_run())
