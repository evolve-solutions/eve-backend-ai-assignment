# contract-check

Checks a project against the parts of the contract that can be checked without grading it. It checks shape, not correctness: a project that passes can still fail every grading condition.

Run it from the root of your repository with [`uv`](https://docs.astral.sh/uv/) (Python 3.12):

```bash
# Static rules: compose.yaml (sections 1, 2 and 9 of contract/runtime.md) and grading.toml (section 5).
# Needs Docker with Compose, because it also checks the configuration Compose resolves.
uv run --project contract-check contract-check --compose-dir .

# HTTP shapes of a running instance. It creates reservations, so use a throwaway database.
uv run --project contract-check contract-check --base-url http://127.0.0.1:8000

# The same, without the /agent checks (they call the LLM your service is configured with).
uv run --project contract-check contract-check --base-url http://127.0.0.1:8000 --skip-agent

# A corpus and inventory pair you built for your own tests.
uv run --project contract-check contract-check --corpus my-corpus.jsonl --inventory my-inventory.json
```

Add `--json` for a machine-readable report. The exit code is 0 when nothing is wrong and 1 when it found violations; each violation names the rule id used in `contract/runtime.md` section 9.

The rules themselves live in `contract/static_rules.py` and `contract/data_rules.py`; `contract-check` loads them from the `contract/` directory next to it, so the checker and the contract always match.
