#!/usr/bin/env python3
"""Try the real memory server using a disposable, fictional project."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

SERVER = Path(__file__).resolve().parents[1] / "skills/agent-memory/scripts/run.py"


def invoke(directory: str, name: str, arguments: dict, *, writes: bool = False) -> dict:
    """Launch a fresh server process and return one checked tool result."""
    command = [sys.executable, str(SERVER), "--data-dir", directory]
    if writes:
        command.append("--allow-writes")
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    process = subprocess.run(
        command,
        input=json.dumps(message) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=True,
    )
    response = json.loads(process.stdout)
    if "error" in response or response.get("result", {}).get("isError"):
        raise RuntimeError(f"Server rejected the demo request: {response}")
    return json.loads(response["result"]["content"][0]["text"])


def main() -> int:
    """Capture, restart, recall, and discard a fictional memory store."""
    with tempfile.TemporaryDirectory(prefix="forge-memory-demo-") as directory:
        invoke(
            directory,
            "memory_capture",
            {
                "project_slug": "fictional-demo",
                "episode_id": "demo-invoice-headers",
                "summary": "Fictional demo: invoice exports include column headers.",
                "source": "explicit disposable demonstration",
            },
            writes=True,
        )
        print("1. Captured one fictional decision in a temporary store.")
        result = invoke(
            directory, "memory_search", {"project_slug": "fictional-demo", "query": "invoice"}
        )
        if [row["episode_id"] for row in result["results"]] != ["demo-invoice-headers"]:
            raise RuntimeError("The restarted server did not recall the expected demo note.")
        print("2. Restarted the server and recalled the same decision.")
        stats = invoke(directory, "memory_stats", {})
        if stats["episodes"] != 1:
            raise RuntimeError("Unexpected note count in the disposable store.")
        print("3. Verified the stored note count.")
    print("PASS: temporary store removed; no database or model service used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
