# Panelists

Use the smallest panel that exposes the decision's real failure modes.

## Core Roster

- `scout`: gathers direct evidence, live docs, project files, metrics, and prior incidents.
- `red_team`: finds downside, hidden coupling, incentives, and ways the plan fails.
- `steel_man`: argues the strongest feasible version of the plan.
- `constraint_keeper`: checks the identity or strategy file, project doctrine, legal/compliance, blast radius, and hard constraints.
- `judge`: synthesizes evidence, preserves dissent, and issues the verdict.

## Specialist Roster

Add only when needed:

- `lookahead_inquisitor`: trading/ML/data leakage, point-in-time invariance, embargo, purge, backfill, corporate actions.
- `calibration_skeptic`: thresholds, validation design, sample size, confidence intervals, cost/slippage, calibration drift.
- `security_privacy`: auth, secrets, PII, permissions, compliance, destructive operations.
- `operator`: rollout, monitoring, rollback, runbooks, ownership, fatigue, maintenance.
- `frontier_scout`: current external research, current API limits, model/tool ecosystem changes.
- `customer_voice`: ICP, messaging, offer-market fit, buying friction, support burden.

## Role Rules

- Every role must cite evidence or label a claim as inference.
- Every role must state confidence and what would change its mind.
- Any role may force `INSUFFICIENT_EVIDENCE` when a missing fact can reverse the decision.
- Dissent is preserved with a trigger condition, not averaged away.
