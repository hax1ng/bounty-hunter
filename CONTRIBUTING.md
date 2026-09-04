# Contributing

Thanks for your interest in Bounty Hunter. This is a single-user Linux workbench
for **authorized** bug bounty work; contributions must respect the safety
boundaries documented in [`docs/SAFETY.md`](docs/SAFETY.md).

## Ground rules

- **No auto-submit, no unauthorized/out-of-scope helpers, no drive-by scanning.**
  Features that weaken the authorization, scope, evidence, or report-quality
  gates will not be merged. See the "What we will not add" list in the safety doc.
- Keep the Assistant chat-only. If it starts calling tools that touch a target,
  that is a bug, not a feature.
- The Reporter exports write-ups; it never submits them.

## Development setup

```bash
git clone https://github.com/hax1ng/bounty-hunter.git
cd bounty-hunter
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Before you open a PR

```bash
ruff check .        # lint (must pass)
ruff format .       # format
pytest -q           # tests (must pass)
```

- Add or update tests for any behavior change. The safety gates
  (`ScopeGuard`, evidence, dedupe-key convergence) have dedicated tests —
  keep them green.
- Match the surrounding style: typed pydantic models, structured agent outputs,
  no hunt tools on any live route.
- Keep commits focused and write a clear description of the change and its
  safety implications.

## Reporting issues

Open a GitHub issue with steps to reproduce. Please do **not** include real
target data, credentials, or anything from an active engagement.
