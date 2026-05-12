"""Tests for stable_json ensure_ascii=False and models."""
from __future__ import annotations

import json

from dev_orchestrator.v8.models import stable_json


def test_stable_json_preserves_chinese():
    """stable_json must not escape Chinese characters."""
    data = {"message": "你好世界", "items": ["测试"]}
    result = stable_json(data)
    assert "你好世界" in result
    assert "\\u" not in result


def test_stable_json_is_valid_json():
    data = {"a": 1, "b": "中文", "c": [1, 2, 3]}
    result = stable_json(data)
    parsed = json.loads(result)
    assert parsed["b"] == "中文"


def test_stable_json_is_deterministic():
    """Same input must produce same output (sorted keys)."""
    data1 = {"z": 1, "a": 2, "m": 3}
    data2 = {"m": 3, "z": 1, "a": 2}
    assert stable_json(data1) == stable_json(data2)
