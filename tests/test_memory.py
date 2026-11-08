"""Exercise the shipped stdio server, with disposable stores and no live services."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[1] / "skills/agent-memory/scripts/run.py"


def call(name, arguments=None, ident=1):
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


class MemoryTests(unittest.TestCase):
    def run_server(self, directory, messages, writes=False, env=None):
        command = [sys.executable, str(SERVER), "--data-dir", str(directory)]
        if writes:
            command.append("--allow-writes")
        result = subprocess.run(
            command,
            input="".join(json.dumps(m) + "\n" for m in messages),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def payload(self, response):
        self.assertFalse(response["result"].get("isError"), response)
        return json.loads(response["result"]["content"][0]["text"])

    def test_empty_store_protocol_and_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            results = self.run_server(
                directory,
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    call("memory_search", {"query": "anything"}),
                    call("memory_stats"),
                ],
            )
            self.assertEqual(results[0]["result"]["serverInfo"]["name"], "forge-agent-memory")
            self.assertGreater(len(results[1]["result"]["tools"]), 10)
            self.assertEqual(self.payload(results[2])["results"], [])
            self.assertEqual(self.payload(results[3])["episodes"], 0)

    def test_capture_restart_search_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            episode = {
                "summary": "Prefer blue invoice exports",
                "project_slug": "demo",
                "episode_id": "old",
            }
            self.payload(self.run_server(directory, [call("memory_capture", episode)], True)[0])
            update = {
                "summary": "Prefer green invoice exports",
                "project_slug": "demo",
                "episode_id": "new",
                "supersedes": ["old"],
            }
            self.payload(self.run_server(directory, [call("memory_capture", update)], True)[0])
            results = self.run_server(
                directory,
                [
                    call("memory_search", {"query": "invoice", "project_slug": "demo"}),
                    call("memory_recent", {"project_slug": "demo"}),
                ],
            )
            self.assertEqual(
                [r["episode_id"] for r in self.payload(results[0])["results"]], ["new"]
            )
            self.assertEqual(len(self.payload(results[1])["episodes"]), 2)

    def test_readonly_and_inherited_database_are_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(
                os.environ,
                AGENT_MEMORY_POSTGRES_DSN="postgresql:///DO_NOT_CONNECT",
                AGENT_MEMORY_LEDGER="/DO_NOT_WRITE",
            )
            results = self.run_server(
                directory,
                [
                    call("memory_capture", {"summary": "blocked"}),
                    call("memory_search", {"query": "nothing"}),
                    call("memory_trends"),
                ],
                env=env,
            )
            self.assertTrue(results[0]["result"]["isError"])
            self.assertEqual(self.payload(results[1])["backend"], "jsonl")
            self.assertTrue(results[2]["result"]["isError"])
            self.assertFalse((Path(directory) / "episodes.jsonl").exists())

    def test_bad_input_does_not_kill_server(self):
        with tempfile.TemporaryDirectory() as directory:
            results = self.run_server(
                directory,
                [
                    call([], ident=1),
                    call("memory_capture", {"summary": "x", "tags": "not an array"}, 2),
                    call("memory_stats", ident=3),
                ],
                True,
            )
            self.assertIn("error", results[0])
            self.assertTrue(results[1]["result"]["isError"])
            self.assertEqual(self.payload(results[2])["episodes"], 0)

    def test_duplicate_and_foreign_supersession_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            original = {"summary": "blue decision", "project_slug": "one", "episode_id": "first"}
            results = self.run_server(
                directory,
                [
                    call("memory_capture", original),
                    call("memory_capture", original),
                    call(
                        "memory_capture",
                        {
                            "summary": "bad replacement",
                            "project_slug": "two",
                            "supersedes": ["first"],
                        },
                    ),
                ],
                True,
            )
            self.payload(results[0])
            self.assertTrue(results[1]["result"]["isError"])
            self.assertTrue(results[2]["result"]["isError"])

    def test_configuration_generation_has_no_storage_side_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "new"
            result = subprocess.run(
                [sys.executable, str(SERVER), "--data-dir", str(directory), "--claude-config"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("forge-memory", json.loads(result.stdout)["mcpServers"])
            self.assertFalse(directory.exists())

    def test_string_review_and_database_override_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            results = self.run_server(
                directory,
                [
                    call("memory_dream_apply", {"plan": "missing", "reviewed": "false"}),
                    call("memory_trends", {"dsn": "postgresql:///foreign"}),
                ],
                True,
            )
            self.assertTrue(all(r["result"]["isError"] for r in results))
            self.assertIn("JSON boolean", results[0]["result"]["content"][0]["text"])
            self.assertIn("overrides are refused", results[1]["result"]["content"][0]["text"])

    def test_models_disabled_before_any_subprocess(self):
        import importlib
        from unittest.mock import patch

        sys.path.insert(0, str(SERVER.parent))
        heal = importlib.import_module("agent_memory_heal")
        with (
            patch.dict(os.environ, {"FORGE_MEMORY_ALLOW_MODEL_CALLS": "0"}),
            patch.object(heal.subprocess, "run") as run,
        ):
            with self.assertRaises(heal.AgentMemoryHealError):
                heal._claude_runner("test")
            run.assert_not_called()

    def test_api_auth_never_falls_back_to_paid_call(self):
        import importlib
        from unittest.mock import patch

        sys.path.insert(0, str(SERVER.parent))
        heal = importlib.import_module("agent_memory_heal")
        auth = subprocess.CompletedProcess(
            [], 0, json.dumps({"loggedIn": True, "authMethod": "api_key"}), ""
        )
        with (
            patch.dict(
                os.environ,
                {
                    "FORGE_MEMORY_ALLOW_MODEL_CALLS": "1",
                    "ANTHROPIC_API_KEY": "fictional-test-value",
                },
            ),
            patch.object(heal.subprocess, "run", return_value=auth) as run,
        ):
            with self.assertRaises(heal.AgentMemoryHealError):
                heal._claude_runner("test")
            self.assertEqual(run.call_count, 1)
            self.assertNotIn("ANTHROPIC_API_KEY", run.call_args.kwargs["env"])

    def test_busy_store_does_not_drop_next_request(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            lock = sqlite3.connect(Path(directory) / "write-lock.sqlite")
            try:
                lock.execute("BEGIN IMMEDIATE")
                results = self.run_server(
                    directory,
                    [call("memory_capture", {"summary": "blocked by lock"}), call("memory_stats")],
                    True,
                )
                self.assertIn("busy", results[0]["result"]["content"][0]["text"])
                self.assertEqual(self.payload(results[1])["episodes"], 0)
            finally:
                lock.close()


class HistoryPortabilityTests(unittest.TestCase):
    def test_explicit_home_is_used_for_history_and_import(self):
        sys.path.insert(0, str(SERVER.parent))
        import agent_memory_history_scan as history
        import agent_memory_retro_import as retro

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            folder = (
                home / ".claude/projects" / (history.claude_projects_prefix(home) + "fictional")
            )
            folder.mkdir(parents=True)
            session = folder / "demo-session.jsonl"
            session.write_text(
                json.dumps(
                    {"type": "user", "message": {"role": "user", "content": "Fictional demo"}}
                )
                + "\n",
                encoding="utf-8",
            )
            candidates = history.scan_claude_projects(home, None, 10)
            self.assertEqual(candidates[0].project_hint, "fictional")
            self.assertEqual(
                retro.find_claude_session_path(home, "demo-session", "fictional"), session
            )
            self.assertEqual(retro.project_slug_from_claude_project_dir(folder, home), "fictional")


class OperatorHelpTests(unittest.TestCase):
    def test_help_needs_no_backend_or_model(self):
        for script in sorted(SERVER.parent.glob("*.py")):
            if script.name == "memory_paths.py":
                continue
            with self.subTest(script=script.name):
                result = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    env=dict(os.environ, PATH=""),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())
