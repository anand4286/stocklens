# Contributing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock -r requirements-build.lock
pip install --no-deps --no-build-isolation -e . && pre-commit install
pytest && ruff check src tests && bandit -q -r src -c pyproject.toml
```

**Changing dependencies:** edit the ranges in `pyproject.toml`, then run
`scripts/lock.sh` (needs [uv](https://docs.astral.sh/uv/)) and commit the
regenerated `requirements*.lock` files. Every install path verifies packages
against those hashes, so a dependency change without a relock fails CI.

**Workflow:** `main` is protected — branch, open a pull request, and merge once
CI, CodeQL and the secret scan are green.

Ground rules, all enforced by tests where possible:

1. **Missing is not neutral.** A new signal family returns `score=None` when it lacks data, and has a test proving it abstains.
2. **Every number carries its rule.** A score needs a `reason` a reader could disagree with; a constant needs a comment saying where it came from, or that it was picked.
3. **No inline script or style** in `web/`; build DOM with the `h()` helper.
4. **Agent tools stay read-only and closed:** strict schemas, `additionalProperties: false`, no symbol/URL/query inputs.
5. **No secrets in code, tests or fixtures.** Gitleaks runs in CI and pre-commit.
6. One concern per commit; the message says what changed and how it was checked.
