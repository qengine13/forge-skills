#!/usr/bin/env python3
"""Conservatively combine saved panel decisions; no model calls or credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_verdict import LIST_FIELDS, read_verdict, validate_payload


def aggregate(panels: list[dict]) -> dict:
    """Preserve all objections and let any unresolved evidence prevent approval.

    Example:
        result = aggregate([read_verdict(Path('a.json')), read_verdict(Path('b.json'))])
    """
    if len(panels) < 2:
        raise ValueError("At least two panel decisions are required; use the validator for one.")
    for panel in panels:
        errors = validate_payload(panel)
        if errors:
            raise ValueError("; ".join(errors))
    if len({p["panelist"] for p in panels}) != len(panels):
        raise ValueError(
            "Panelist identifiers must be distinct; identifiers alone do not prove independence."
        )
    votes = {p["verdict"] for p in panels}
    unresolved = any(p["outstanding_objections"] or p["evidence_demanded"] for p in panels)
    if len(votes) > 1:
        verdict, decision = "INSUFFICIENT_EVIDENCE", "defer"
    elif "FAIL" in votes:
        verdict, decision = "FAIL", "reject"
    elif "INSUFFICIENT_EVIDENCE" in votes or unresolved:
        verdict, decision = "INSUFFICIENT_EVIDENCE", "defer"
    else:
        verdict = "PASS"
        decision = (
            "accept_with_modification"
            if any(p["decision"] == "accept_with_modification" for p in panels)
            else "accept"
        )
    result = {
        "panelist": "crucible_forge",
        "verdict": verdict,
        "decision": decision,
        "confidence": min(p["confidence"] for p in panels),
        "panels": panels,
    }
    for key in LIST_FIELDS:
        result[key] = list(dict.fromkeys(item for p in panels for item in p[key]))
    result["next_3_steps"] = result["next_3_steps"][:3]
    errors = validate_payload(result)
    if errors:
        raise ValueError("Invalid aggregate: " + "; ".join(errors))
    return result


def main() -> int:
    """Print the decision; exit 0 for approval, 1 for nonapproval, 2 for bad input.

    Example:
        python3 aggregate_verdicts.py reviewer-a.json reviewer-b.json --out verdict.json
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, help="Create a new result file; never overwrite one")
    args = parser.parse_args()
    try:
        result = aggregate([read_verdict(p) for p in args.files])
        rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.out:
            with args.out.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"INVALID: {exc}\n")
    print(rendered, end="")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
