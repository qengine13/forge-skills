#!/usr/bin/env python3
"""Validate an evidence-bound decision without calling a model or service."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

LIST_FIELDS = (
    "evidence_provided",
    "evidence_demanded",
    "outstanding_objections",
    "kill_conditions",
    "next_3_steps",
)
DECISIONS = {
    "PASS": {"accept", "accept_with_modification"},
    "FAIL": {"reject"},
    "INSUFFICIENT_EVIDENCE": {"defer"},
}


def validate_payload(payload: object) -> list[str]:
    """Return violations of the verdict contract; an empty list means valid.

    Example:
        validate_payload([]) == ['verdict must be a JSON object']
    """
    if not isinstance(payload, dict):
        return ["verdict must be a JSON object"]
    errors = []
    if not isinstance(payload.get("panelist"), str) or not payload["panelist"].strip():
        errors.append("panelist must be a nonempty string")
    verdict = payload.get("verdict")
    if not isinstance(verdict, str) or verdict not in DECISIONS:
        errors.append("verdict must be PASS, FAIL, or INSUFFICIENT_EVIDENCE")
    elif (
        not isinstance(payload.get("decision"), str)
        or payload["decision"] not in DECISIONS[verdict]
    ):
        errors.append("decision must agree with verdict")
    confidence = payload.get("confidence")
    if (
        type(confidence) not in (int, float)
        or not 0 <= confidence <= 1
        or not math.isfinite(confidence)
    ):
        errors.append("confidence must be a finite number from 0 to 1, not a boolean")
    for key in LIST_FIELDS:
        value = payload.get(key)
        if not isinstance(value, list) or any(
            not isinstance(x, str) or not x.strip() for x in value
        ):
            errors.append(f"{key} must be a list of nonempty strings")
    steps = payload.get("next_3_steps")
    if isinstance(steps, list) and not 1 <= len(steps) <= 3:
        errors.append("next_3_steps must contain one to three actions")
    if verdict == "PASS":
        if payload.get("outstanding_objections") or payload.get("evidence_demanded"):
            errors.append("PASS cannot retain unresolved objections or demanded evidence")
        if not payload.get("kill_conditions") or not payload.get("evidence_provided"):
            errors.append("PASS requires evidence and reversal conditions")
    return errors


def read_verdict(path: Path) -> dict:
    """Read and validate one file, raising ValueError for invalid input.

    Example:
        verdict = read_verdict(Path('verdict.json'))
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_payload(payload)
    if errors:
        raise ValueError(f"{path.name}: " + "; ".join(errors))
    return payload


def main() -> int:
    """Validate a stored decision; exit zero means valid format, not approval.

    Example:
        python3 validate_verdict.py verdict.json
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        payload = read_verdict(args.file)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"INVALID: {exc}\n")
    print(f"VALID: {payload['verdict']} (valid format does not verify its evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
