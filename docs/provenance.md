# Provenance and public-edition changes

[← Collection](../README.md)

This is a curated public edition of Avichayil's operating skills, developed with AI assistance.
The package imports selected instruction files, the Anti-Slop Gates engine, and the Agent Memory runtime, not private repository
history, credentials, conversation transcripts, or memory stores.

| Public package | Source family | Public-edition changes |
|---|---|---|
| Plan Hygiene | Codex adaptation of a source skill | Shared model policy; explicit-only memory capture |
| Completion Hygiene | Codex adaptation of a source skill | Shared model policy; explicit-only memory capture |
| Project Forge | Codex adaptation of a source skill | Portable paths; bundled review fallback; live-state precedence |
| Prompt Forge | Codex adaptation of a source skill | Shared model policy; evaluation model freezing |
| Anti-Slop Gates | Owner's skills collection | Portable instructions; standalone review checklist; original engine retained |
| Agent Memory | Owner's memory-server source | Isolated launcher; explicit write/model controls; subscription CLI adjudication; portable defaults |
| Crucible Forge | Codex Crucible Forge adaptation | Provider-neutral name; tested offline validator/aggregation; no provider dispatcher |

Crucible uses an offline validator and aggregator. Multi-agent review is available through the
host's authorized delegation tools. The aggregator accepts independently obtained responses and
preserves objections; it neither calls providers nor proves reviewer independence.

Confidence is a reviewer estimate, not a calibrated probability.

Apache 2.0 follows the source collection's existing license. See [NOTICE](../NOTICE) and [LICENSE](../LICENSE).
Modified files are documented here and in NOTICE. Examples are fictional. Required attribution remains in NOTICE; it does not imply test coverage.

Gauntlet Loop is external, not included or presented as authored here. Its attribution credits
Matt Shumer for the technique and Jay E / RoboNuggets for packaging.
