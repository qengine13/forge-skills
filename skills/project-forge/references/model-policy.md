# Model and tool selection

Use the user's explicit model choice first. Otherwise prefer the newest stable, task-capable
model actually available through the current subscription and authorized host.

1. Inspect the host's current model inventory, authenticated model selector, or documented
   discovery tool when available. Verify availability and required capabilities; do not infer
   access from a public model announcement, a familiar name, or another user's plan.
2. Select the newest compatible included model only when release ordering and entitlement can
   be established and the host permits selection. Do not silently change provider, account,
   billing route, or subscription. Do not sort model strings as a substitute for release evidence.
3. If discovery, entitlement, ordering, or switching is unavailable, keep the session's configured
   model and report "latest eligible model unverified" when model selection matters to the task.
   Do not claim to have switched. Never invent a model identifier or a tool command.
4. A chat subscription is not proof of API credits. Extra-cost APIs, upgrades, preview models,
   external providers, and additional subscriptions require explicit authorization.
5. If the current model lacks a required capability, use an available authorized tool or explain
   the exact missing capability. Ask only when a user choice is necessary to proceed.
6. Apply this policy independently to each authorized reviewer. Use fresh contexts for independent
   review; never describe role-playing by one agent as independent agreement. Do not spawn extra
   agents solely to discover models. Keep user-specified time, token, and cost limits.
7. Record the actual model/version and relevant runtime settings when the host exposes them.
   Freeze the model for a baseline/candidate evaluation so the comparison stays meaningful.
   Upgrade between evaluations, then rerun the baseline; do not mix results across model changes.
8. Embedding models are a separate compatibility contract. Never silently upgrade one behind
   an existing vector index. Verify dimensions, rebuild/re-embed in isolation, evaluate recall,
   and obtain migration authority before replacing the active index.

These are selection instructions, not a model-discovery service. Plain Markdown cannot force
subscription access, change a host's model, or guarantee that a provider reports its newest model.
