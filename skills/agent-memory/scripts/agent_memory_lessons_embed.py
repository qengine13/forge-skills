#!/usr/bin/env python3
"""Generate the missing lesson embeddings.

The corpus dump deliberately carries no vectors -- they are large arrays of float
noise that would swamp every diff and regenerate quickly. That makes this script
the second half of the recovery path: after `--restore`, every row is text-only,
scores zero on the semantic side, and (because a lone keyword hit needs semantic
corroboration) is largely invisible to the retriever until this runs.

Idempotent: only rows with a NULL embedding, or one from a different model, are
touched, so it is safe to re-run after any partial failure.

  agent_memory_lessons_embed.py           # fill whatever is missing
  agent_memory_lessons_embed.py --all     # re-embed everything (model change)
"""

# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import urllib.request

DSN = os.environ.get("AGENT_MEMORY_POSTGRES_DSN", "postgresql:///forge_agent_memory")
EMBED_URL = os.environ.get("AGENT_MEMORY_EMBED_URL", "http://localhost:11434/api/embed")
MODEL = os.environ.get("AGENT_MEMORY_EMBED_MODEL", "nomic-embed-text")
BATCH_LOG_EVERY = 50


def embed(text: str) -> list[float]:
    req = urllib.request.Request(
        EMBED_URL,
        data=json.dumps({"model": MODEL, "input": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["embeddings"][0]


def pending(all_rows: bool) -> list[tuple[str, str, list[str]]]:
    where = "" if all_rows else (
        f"WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM '{MODEL}'"
    )
    proc = subprocess.run(
        ["psql", DSN, "-tAF", "\x1f", "-c",
         f"SELECT lesson_id, lesson, triggers::text FROM agent_memory.lessons {where}"],
        capture_output=True, text=True, check=True,
    )
    out = []
    for ln in proc.stdout.strip().split("\n"):
        if not ln:
            continue
        parts = ln.split("\x1f")
        if len(parts) >= 3:
            out.append((parts[0], parts[1], json.loads(parts[2] or "[]")))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build lesson embeddings using the configured local model.")
    parser.add_argument("--all", action="store_true", help="Rebuild every embedding after an intentional model change")
    args = parser.parse_args()
    rows = pending(args.all)
    if not rows:
        print("nothing to embed -- all lessons carry a current vector")
        return 0

    stmts = ["BEGIN;"]
    for i, (lid, lesson, triggers) in enumerate(rows, 1):
        # Embed the lesson together with its triggers so the vocabulary an agent
        # would actually type sits inside the vector, not just the prose.
        vec = embed(lesson + " || " + " ".join(triggers))
        blob = struct.pack(f"<{len(vec)}f", *vec).hex()
        stmts.append(
            f"UPDATE agent_memory.lessons SET embedding='\\x{blob}', "
            f"embedding_model='{MODEL}' WHERE lesson_id='{lid}';"
        )
        if i % BATCH_LOG_EVERY == 0:
            print(f"  embedded {i}/{len(rows)}", file=sys.stderr)
    stmts.append("COMMIT;")

    subprocess.run(["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1"],
                   input="\n".join(stmts), text=True, check=True)
    print(f"embedded {len(rows)} lessons with {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
