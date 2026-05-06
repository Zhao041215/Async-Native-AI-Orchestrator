from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "dev_orchestrator" / "static"


ALLOWED_ENGLISH = {
    "api",
    "base",
    "chief",
    "dag",
    "diff",
    "git",
    "go",
    "html",
    "http",
    "https",
    "json",
    "key",
    "no",
    "only",
    "path",
    "profile",
    "sso",
    "tokens",
    "ui",
    "v1",
    "v2",
    "wire",
    "worker",
    "workers",
    "zh",
}


class ConsoleLocalizationTests(unittest.TestCase):
    def test_static_shell_is_chinese_first(self) -> None:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('lang="zh-CN"', html)
        self.assertIn("托管 V2 控制台", html)
        self.assertNotIn("Chief-led delivery", html)
        self.assertNotIn("Create project and dispatch run", html)
        self.assertNotIn("Select a project", html)

    def test_text_dictionary_covers_console_copy(self) -> None:
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        required = [
            "托管 V2 控制台",
            "第三方 API 配置",
            "环境变量",
            "连接成功",
            "连接失败",
            "项目蓝图",
            "审批 GO 候选",
            "暂无审计事件",
        ]
        for text in required:
            self.assertIn(text, app_js)

    def test_no_obvious_english_user_labels_remain(self) -> None:
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        text_body = re.search(r"const TEXT = \{(?P<body>.*?)\};", app_js, re.S).group("body")
        label_body = re.search(r"const LABELS = \{(?P<body>.*?)\};", app_js, re.S).group("body")
        visible_values = re.findall(r":\s*\"([^\"]*)\"", text_body + "\n" + label_body)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        title_values = re.findall(r"<title>(.*?)</title>", html, re.S)
        button_text = re.findall(r">(?!\s*<)([^<]*[\u4e00-\u9fff][^<]*)<", html)
        visible = "\n".join(visible_values + title_values + button_text)
        words = {word.lower() for word in re.findall(r"\b[A-Za-z][A-Za-z-]{2,}\b", visible)}
        offenders = sorted(word for word in words if word not in ALLOWED_ENGLISH and not word.startswith("gpt"))

        self.assertFalse(offenders, offenders[:30])


if __name__ == "__main__":
    unittest.main()
