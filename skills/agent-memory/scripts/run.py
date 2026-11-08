#!/usr/bin/env python3
"""Start an isolated, local stdio memory server; no third-party packages required."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Dedicated private memory directory, outside any Git checkout",
    )
    parser.add_argument(
        "--allow-writes", action="store_true", help="Enable capture and reviewed maintenance tools"
    )
    parser.add_argument(
        "--allow-model-calls",
        action="store_true",
        help="Allow explicitly requested optional model/embedding operations",
    )
    parser.add_argument(
        "--postgres-dsn", help="Optional dedicated PostgreSQL database; initialize it before use"
    )
    parser.add_argument(
        "--claude-config",
        action="store_true",
        help="Print a config snippet instead of starting the server",
    )
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    if args.claude_config:
        command = [str(Path(__file__).resolve()), "--data-dir", str(directory)]
        if args.allow_writes:
            command.append("--allow-writes")
        if args.allow_model_calls:
            command.append("--allow-model-calls")
        if args.postgres_dsn:
            parser.error(
                "Configure PostgreSQL credentials separately; do not print them into project config"
            )
        print(
            json.dumps(
                {"mcpServers": {"forge-memory": {"command": sys.executable, "args": command}}},
                indent=2,
            )
        )
        return 0
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Ignore ambient parent-process memory store and database settings.
    for name in list(os.environ):
        if name.startswith("AGENT_MEMORY_"):
            del os.environ[name]
    os.environ["FORGE_MEMORY_HOME"] = str(directory)
    os.environ["FORGE_MEMORY_MANAGED"] = "1"
    os.environ["AGENT_MEMORY_LEDGER"] = str(directory / "episodes.jsonl")
    os.environ["AGENT_MEMORY_INDEX"] = str(directory / "episode_embeddings.sqlite")
    os.environ["FORGE_MEMORY_ALLOW_MODEL_CALLS"] = "1" if args.allow_model_calls else "0"
    if args.postgres_dsn:
        os.environ["AGENT_MEMORY_USE_POSTGRES"] = "1"
        os.environ["AGENT_MEMORY_POSTGRES_DSN"] = args.postgres_dsn
    import agent_memory_mcp as server

    original = server.handle
    readonly = {
        "memory_search",
        "memory_recent",
        "memory_stats",
        "memory_guard",
        "project_truth",
        "memory_history_scan",
        "memory_trends",
    }

    def guarded(message):
        if message.get("method") != "tools/call":
            return original(message)
        params = message.get("params", {})
        if not isinstance(params, dict):
            return original(message)
        name = params.get("name")
        if not isinstance(name, str):
            return server.error_response(message.get("id"), -32602, "tool name must be a string")
        arguments = params.get("arguments", {})
        if isinstance(arguments, dict):
            if "dsn" in arguments and arguments["dsn"] != args.postgres_dsn:
                return server.response(
                    message.get("id"),
                    server.text_result(
                        "Per-call database overrides are refused; configure --postgres-dsn at startup.",
                        True,
                    ),
                )
            for flag in (
                "reviewed",
                "semantic",
                "force",
                "no_postgres",
                "write_postgres",
                "capture",
            ):
                if flag in arguments and not isinstance(arguments[flag], bool):
                    return server.response(
                        message.get("id"),
                        server.text_result(f"{flag} must be a JSON boolean.", True),
                    )
        if name not in readonly and not args.allow_writes:
            return server.response(
                message.get("id"),
                server.text_result(
                    "Read-only server. Restart with --allow-writes only after authorizing memory changes.",
                    True,
                ),
            )
        if isinstance(arguments, dict) and arguments.get("semantic") and not args.allow_model_calls:
            return server.response(
                message.get("id"),
                server.text_result("Optional model calls require --allow-model-calls.", True),
            )
        # Serialize mutations across multiple host processes sharing this data directory.
        if name not in readonly:
            try:
                with sqlite3.connect(directory / "write-lock.sqlite", timeout=10) as lock:
                    lock.execute("BEGIN IMMEDIATE")
                    return original(message)
            except (sqlite3.Error, OSError):
                return server.response(
                    message.get("id"),
                    server.text_result(
                        "Memory store is busy or unavailable; retry after the other operation finishes.",
                        True,
                    ),
                )
        return original(message)

    server.handle = guarded
    return server.serve()


if __name__ == "__main__":
    raise SystemExit(main())
