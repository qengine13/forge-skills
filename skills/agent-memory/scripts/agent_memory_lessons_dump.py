#!/usr/bin/env python3
"""Dump and restore the cross-project lessons corpus.

Export a portable JSON backup and restore it into the configured database.
Keep the export private: it contains your lesson text and source references.

Embeddings are deliberately excluded. They are large arrays of float noise, they would
swamp every diff, and they are regenerable from the text quickly.

  dump:    agent_memory_lessons_dump.py > lessons_corpus.json
  restore: agent_memory_lessons_dump.py --restore lessons_corpus.json
"""

# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

DSN = os.environ.get("AGENT_MEMORY_POSTGRES_DSN", "postgresql:///forge_agent_memory")

FIELDS = [
    "lesson_id", "lesson", "category", "triggers", "cost", "confidence",
    "volatility", "source_project", "source_ref", "superseded_by", "verified_at",
]


def dump() -> list[dict]:
    sql = (
        "SELECT json_agg(row_to_json(t) ORDER BY t.category, t.lesson_id) FROM "
        f"(SELECT {', '.join(FIELDS)} FROM agent_memory.lessons) t"
    )
    proc = subprocess.run(["psql", DSN, "-tAc", sql],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout.strip() or "[]")


def restore(path: str) -> int:
    rows = json.load(open(path, encoding="utf-8"))
    # Two passes. superseded_by is a self-referential FK, so a lesson whose
    # replacement appears later in the file would fail the constraint and roll
    # back the whole restore. Insert every row unlinked, then wire the links once
    # all targets exist.
    stmts = ["BEGIN;"]
    links = []
    for r in rows:
        cols, vals = [], []
        for f in FIELDS:
            v = r.get(f)
            if v is None or f == "superseded_by":
                continue
            cols.append(f)
            if f == "triggers":
                v = json.dumps(v)
            vals.append("'" + str(v).replace("'", "''") + "'")
        stmts.append(
            f"INSERT INTO agent_memory.lessons ({','.join(cols)}) "
            f"VALUES ({','.join(vals)}) ON CONFLICT (lesson_id) DO NOTHING;"
        )
        if r.get("superseded_by"):
            sid = str(r["lesson_id"]).replace("'", "''")
            tgt = str(r["superseded_by"]).replace("'", "''")
            links.append(
                f"UPDATE agent_memory.lessons SET superseded_by='{tgt}' "
                f"WHERE lesson_id='{sid}';"
            )
    stmts.extend(links)
    stmts.append("COMMIT;")
    subprocess.run(["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1"],
                   input="\n".join(stmts), text=True, check=True)
    # Embeddings are not carried in the dump; a restored row is text-searchable
    # immediately and becomes semantically searchable after re-embedding.
    print(f"restored {len(rows)} lessons -- re-run the embedder to rebuild vectors",
          file=sys.stderr)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export private lessons, or explicitly restore a backup.")
    parser.add_argument("--restore", metavar="FILE", help="Restore a reviewed private JSON export")
    args = parser.parse_args()
    if args.restore:
        restore(args.restore)
    else:
        json.dump(dump(), sys.stdout, indent=1, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
