# Templated prompt checklist

## Artifact set

Keep together:

- prompt-family identifier and description;
- declared input variables;
- prompt template;
- constraints and expected output schema;
- prompt version and state;
- dataset snapshot identifier;
- scorer version and threshold;
- model and runtime settings relevant to comparison;
- evaluation results and cost;
- rollback target.

Use the repository's established format and evaluator. Do not invent commands or services that are not present.

## Dataset floor

Choose a sample large and varied enough for the claim. As a starting heuristic, use at least 20 labelled examples for general tasks, at least five examples per class for classification, or at least ten judged examples for generation. Treat these as floors, not proof of statistical adequacy.

Include normal, boundary, malformed, and adversarial cases. Freeze the dataset before candidate evaluation.

## Evaluation loop

1. Render with strict missing-variable handling.
2. Measure the baseline using the frozen dataset and scorer.
3. Identify the most consequential observed failure.
4. State one change hypothesis.
5. Evaluate the candidate with identical comparison settings.
6. Retain the candidate only if it improves the declared objective without violating hard constraints.
7. Stop at the threshold, budget, iteration cap, or sustained negligible gain declared in advance.

For safety-sensitive work, separate utility evaluation from adversarial or safety evaluation when feasible.

## Promotion

Require the repository's state and approval checks. Confirm no competing promotion changed the active version. Record the previous active version or explicit first-version rollback plan. Keep only one active production version per prompt family unless the system intentionally supports experiments.
