# Authored prompt checklist

Use this for skills, system prompts, instruction documents, and other text whose behaviour is evaluated through realistic invocations.

## Package and discovery

- Use only metadata keys accepted by the target runtime.
- Put trigger phrases and exclusions in the discovery description when that runtime uses description-based invocation.
- Keep the public name unique and stable.
- Add runtime interface metadata only when the runtime supports it.

## Instruction body

- Lead with purpose and source-of-truth precedence.
- State required inputs.
- Use ordered imperative steps for dependent work.
- Name outputs and stop conditions.
- State approval and mutation boundaries.
- Route deep domain detail to focused references.
- Keep references one level from the primary file.
- Prefer scripts and existing assets over retyped procedures.

## Validation suite

Define the expected result before each run:

| Scenario | Expected trigger | Expected actions | Expected output or stop |
|---|---|---|---|
| Explicit invocation | yes | follow full workflow | required artifact |
| Natural-language trigger | yes | follow relevant workflow | required artifact |
| Nearby unrelated task | no | answer normally | no skill overhead |
| Missing critical input | yes | retrieve or ask minimally | no invention |
| Unauthorized mutation | yes | stop at boundary | request authority |
| Highest-risk adversarial case | depends | preserve hard constraint | safe, evidence-backed result |

Pass raw tasks and target artifacts to test agents. Do not tell them which instruction you expect to fail. Record divergences and make the smallest coherent patch.
