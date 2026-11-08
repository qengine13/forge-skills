# Decision Checklists

## Universal Gate

- What is the decision?
- What is the baseline?
- What does 10x mean here?
- What is the reversibility cost?
- What evidence would make this a no?
- What metric moves if the decision is right?
- What should stop if we do this?
- What are the kill conditions?

## Architecture Gate

- Current bottleneck and owner.
- Shared contracts touched.
- Migration path and rollback.
- Operational complexity added.
- Test surface and observability.
- Failure isolation.
- Current external docs checked if libraries, APIs, or infra are involved.

## Trading / ML / Data Gate

- T_decision defined.
- Every feature knowable before T_decision.
- Late-arrival and backfill behavior tested.
- Embargo/purge or walk-forward/CPCV specified.
- Train/test near-duplicate and cluster contamination blocked.
- Corporate actions and vendor revisions handled as-of-time.
- Label construction excludes future data from inference features.
- Costs, slippage, spread, capacity, and confidence intervals included.

## Strategy / Business Gate

- Identity or strategy file loaded.
- ICP and offer fit checked.
- Bottleneck identified.
- Expected impact has a measurable metric.
- Complexity, cost, and timeline stated.
- Stop-doing list included.
- Risk path and review date defined.

## Launch / Money-Moving Gate

- Fail-closed default.
- Manual override path.
- Monitoring and alert owner.
- Rollback steps.
- Dry-run or paper-mode evidence.
- Known anomaly register checked if present.
- No destructive operation without explicit user approval.
