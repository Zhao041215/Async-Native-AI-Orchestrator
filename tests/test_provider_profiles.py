from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from dev_orchestrator.config import (
    AppConfig,
    IdentityConfig,
    LLMConfig,
    ProductionScaffoldConfig,
    RuntimeConfig,
    ServerConfig,
    ensure_paths,
    load_config,
    update_config,
)
from dev_orchestrator.llm_client import OpenAICompatibleClient
from dev_orchestrator.server import Application, build_handler


def build_config(root: Path, llm: LLMConfig | None = None) -> AppConfig:
    config = AppConfig(
        root_dir=root.resolve(),
        config_path=(root / "orchestrator_config.json").resolve(),
        server=ServerConfig(),
        llm=llm or LLMConfig(use_mock=True),
        runtime=RuntimeConfig(
            workspace_root="workspace/projects",
            db_path="workspace/provider-test.db",
            logs_path="logs",
        ),
        identity=IdentityConfig(),
        production=ProductionScaffoldConfig(),
    )
    ensure_paths(config)
    return config


class CaptureHandler(BaseHTTPRequestHandler):
    captures: list[dict] = []
    response_payload: dict = {"choices": [{"message": {"content": "ok"}}]}
    status_code: int = 200
    delay_seconds: float = 0

    def do_POST(self) -> None:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.captures.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "payload": json.loads(body),
            }
        )
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.response_payload).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return


class LocalModelServer:
    def __init__(self) -> None:
        CaptureHandler.captures = []
        CaptureHandler.response_payload = {"choices": [{"message": {"content": "ok"}}]}
        CaptureHandler.status_code = 200
        CaptureHandler.delay_seconds = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()


class ProviderProfileTests(unittest.TestCase):
    def test_chat_completions_payload_and_custom_auth_header(self) -> None:
        with LocalModelServer() as base_url:
            config = LLMConfig(
                use_mock=False,
                api_base=base_url,
                api_key="secret",
                model="third-party-chat",
                provider_profile="custom-chat-compatible",
                auth_header="X-API-Key",
                auth_scheme="",
                extra_headers={"X-Tenant": "demo"},
            )

            result = OpenAICompatibleClient(config).chat("sys", [{"role": "user", "content": "hi"}])

            self.assertEqual(result, "ok")
            capture = CaptureHandler.captures[-1]
            self.assertEqual(capture["path"], "/chat/completions")
            headers = {key.lower(): value for key, value in capture["headers"].items()}
            self.assertEqual(headers["x-api-key"], "secret")
            self.assertEqual(headers["x-tenant"], "demo")
            self.assertEqual(capture["payload"]["model"], "third-party-chat")
            self.assertIn("messages", capture["payload"])
            self.assertIn("max_tokens", capture["payload"])

    def test_responses_payload_and_custom_path_override(self) -> None:
        with LocalModelServer() as base_url:
            CaptureHandler.response_payload = {"output_text": "connected"}
            config = LLMConfig(
                use_mock=False,
                api_base=base_url,
                api_key="secret",
                model="third-party-responses",
                provider_profile="custom-responses-compatible",
                api_path="/v1/custom-responses",
            )

            result = OpenAICompatibleClient(config).chat("sys", [{"role": "user", "content": "hi"}])

            self.assertEqual(result, "connected")
            capture = CaptureHandler.captures[-1]
            self.assertEqual(capture["path"], "/v1/custom-responses")
            self.assertIn("input", capture["payload"])
            self.assertIn("max_output_tokens", capture["payload"])

    def test_retry_failure_records_all_attempts(self) -> None:
        with LocalModelServer() as base_url:
            CaptureHandler.status_code = 500
            config = LLMConfig(
                use_mock=False,
                api_base=base_url,
                api_key="secret",
                retry_attempts=2,
                retry_backoff_seconds=0,
            )

            result = OpenAICompatibleClient(config).test_connection()

            self.assertFalse(result["ok"])
            self.assertEqual(len(CaptureHandler.captures), 2)
            self.assertIn("HTTP 500", result["error"])
            result.clear()

    def test_timeout_failure(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=socket.timeout("slow")):
            config = LLMConfig(
                use_mock=False,
                api_base="https://example.invalid/v1",
                api_key="secret",
                retry_attempts=1,
                timeout_seconds=1,
            )

            result = OpenAICompatibleClient(config).test_connection()

            self.assertFalse(result["ok"])
            self.assertIn("Timed out", result["error"])

    def test_masked_config_serialization_and_env_precedence(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "provider-tests" / uuid.uuid4().hex).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            persisted = build_config(
                root,
                LLMConfig(
                    use_mock=False,
                    api_base="https://persisted.example/v1",
                    api_key="persisted",
                    model="persisted-model",
                    extra_headers={"X-Trace": "keep", "X-API-Key": "drop"},
                ),
            )
            persisted.config_path.write_text(json.dumps(persisted.to_persisted_dict(), indent=2), encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "DEV_ORCHESTRATOR_API_KEY": "env-key",
                    "DEV_ORCHESTRATOR_API_BASE": "https://env.example/v1",
                    "DEV_ORCHESTRATOR_MODEL": "env-model",
                    "DEV_ORCHESTRATOR_PROVIDER_PROFILE": "custom-chat-compatible",
                },
                clear=False,
            ):
                loaded = load_config(root)

            public = loaded.to_dict()["llm"]
            on_disk = json.loads(persisted.config_path.read_text(encoding="utf-8"))["llm"]

            self.assertEqual(loaded.llm.api_key, "env-key")
            self.assertEqual(loaded.llm.api_base, "https://env.example/v1")
            self.assertEqual(loaded.llm.model, "env-model")
            self.assertEqual(loaded.llm.provider_profile, "custom-chat-compatible")
            self.assertEqual(public["api_key"], "***")
            self.assertEqual(on_disk["api_key"], "")
            self.assertNotIn("X-API-Key", on_disk["extra_headers"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_saved_api_key_uses_local_secret_store_and_survives_reload(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "provider-tests" / uuid.uuid4().hex).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            config = build_config(root, LLMConfig(use_mock=False, api_base="https://persisted.example/v1", api_key=""))
            config.config_path.write_text(json.dumps(config.to_persisted_dict(), indent=2), encoding="utf-8")

            update_config(
                config,
                {
                    "llm": {
                        "api_base": "https://deepkey.top/v1",
                        "api_key": "new-secret",
                        "model": "gpt-5.4",
                        "wire_api": "chat_completions",
                        "provider_profile": "custom-chat-compatible",
                    }
                },
            )
            loaded = load_config(root)
            on_disk = json.loads(config.config_path.read_text(encoding="utf-8"))["llm"]
            secret_file = root / "workspace" / "secrets" / "local-llm.json"

            self.assertEqual(loaded.llm.api_key, "new-secret")
            self.assertEqual(loaded.to_dict()["llm"]["api_key"], "***")
            self.assertEqual(on_disk["api_key"], "")
            self.assertTrue(secret_file.exists())
            self.assertEqual(json.loads(secret_file.read_text(encoding="utf-8"))["api_key"], "new-secret")

            update_config(config, {"llm": {"api_key": "***", "model": "gpt-5.4"}})
            self.assertEqual(load_config(root).llm.api_key, "new-secret")
            with mock.patch.dict(os.environ, {"DEV_ORCHESTRATOR_API_KEY": "stale-env-secret"}, clear=False):
                self.assertEqual(load_config(root).llm.api_key, "new-secret")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_connection_reuses_saved_key_when_payload_is_masked(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "provider-tests" / uuid.uuid4().hex).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            with LocalModelServer() as base_url:
                config = build_config(root, LLMConfig(use_mock=False, api_base=base_url, api_key=""))
                config.config_path.write_text(json.dumps(config.to_persisted_dict(), indent=2), encoding="utf-8")
                secret_file = root / "workspace" / "secrets" / "local-llm.json"
                secret_file.parent.mkdir(parents=True, exist_ok=True)
                secret_file.write_text(json.dumps({"api_key": "stored-secret"}), encoding="utf-8")
                app = Application(root)
                handler = build_handler(app)

                class Dummy(handler):
                    def __init__(self) -> None:
                        pass

                captured = {}

                def write_json(payload, code=200):
                    captured["payload"] = payload
                    captured["code"] = int(code)

                test_dummy = Dummy()
                test_dummy.path = "/api/v2/model/test-connection"
                test_dummy.headers = {}
                test_dummy._read_json = lambda: {
                    "llm": {
                        "api_base": base_url,
                        "api_key": "***",
                        "model": "third-party-chat",
                        "wire_api": "chat_completions",
                        "provider_profile": "custom-chat-compatible",
                    }
                }
                test_dummy._write_json = write_json

                test_dummy.do_POST()

                self.assertEqual(captured["code"], 200)
                self.assertTrue(captured["payload"]["ok"])
                headers = {key.lower(): value for key, value in CaptureHandler.captures[-1]["headers"].items()}
                self.assertEqual(headers["authorization"], "Bearer stored-secret")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_profiles_and_test_connection_routes(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "provider-tests" / uuid.uuid4().hex).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            config = build_config(root, LLMConfig(use_mock=False, api_base="", api_key=""))
            config.config_path.write_text(json.dumps(config.to_persisted_dict(), indent=2), encoding="utf-8")
            app = Application(root)
            handler = build_handler(app)

            class Dummy(handler):
                def __init__(self) -> None:
                    pass

            profiles_dummy = Dummy()
            profiles_dummy.path = "/api/v2/model/profiles"
            profiles_dummy.headers = {}
            captured = {}

            def write_json(payload, code=200):
                captured["payload"] = payload
                captured["code"] = int(code)

            profiles_dummy._write_json = write_json
            profiles_dummy.do_GET()

            self.assertEqual(captured["code"], 200)
            self.assertIn("custom-responses-compatible", {item["id"] for item in captured["payload"]["items"]})

            test_dummy = Dummy()
            test_dummy.path = "/api/v2/model/test-connection"
            test_dummy.headers = {}
            test_dummy._read_json = lambda: {"llm": {"api_base": "", "api_key": ""}}
            test_dummy._write_json = write_json
            test_dummy.do_POST()

            self.assertEqual(captured["code"], 400)
            self.assertFalse(captured["payload"]["ok"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
