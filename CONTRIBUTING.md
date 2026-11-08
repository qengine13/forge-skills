# Contributing

Bring a reproducible failure, a clearer instruction, or a realistic example.

1. Describe the user task and what the skill should have done.
2. Include the smallest public, non-sensitive fixture that reproduces the issue.
3. Preserve existing authority, evidence, and compatibility boundaries.
4. Run the tests and package validation. For behavioural changes, run fresh-context scenarios.
5. Describe exactly what was tested and what remains unverified.

No fixed model routing tables. Use the shared model policy and verify runtime capabilities.
Do not include real customer data, credentials, local memory stores, or private session transcripts.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_package.py
```

Changes are contributed under the repository's Apache 2.0 license.
