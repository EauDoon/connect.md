# connect.md - baseline regression audit (2026-09-23)

Branch: ` imp/portfolio-triage-phase9-2026-09-23 `
Default branch captured at clone: ` main `
Python: Python 3.12.10

## Test results (log-verified)

| target | setup | total | pass | skip | fail | error | exit |
|---|---|---:|---:|---:|---:|---:|---:|
| pytest(not integration) | pytest --no-header -rN -q --tb=line -m "not integration" apps/api/tests. PyJWT missing from system Python; conftest failed to import. Heavy deps (asyncpg, sqlalchemy, fastapi, markitdown, pypdf) not installed per the heavy-install skip rule. | 0 | 0 | 0 | 0 | 0 | 4 |
| **total** | | **0** | **0** | **0** | **0** | **0** | |

## Notes

- Per the portfolio triage plan, this commit is a no-op audit-clear baseline. No source, test, or schema files were modified.
- No PR opened by this pass; the human will open the PR on the GitHub web UI.
- Default branch is untouched. Branch push: ` connect.md ` @ ` imp/portfolio-triage-phase9-2026-09-23 `.

## How counts were captured

Counts were re-read directly from the unittest/pytest summary lines (e.g. ` Ran N tests in Ts ` + ` FAILED (errors=E, skipped=S) `) rather than the upstream parser that missed ` OK (skipped=N) ` formatting. Each target was re-invoked in isolation to confirm the count.

## Verdict

Skipped. Suite could not be collected because apps/api heavy deps are not installed in the runner environment. Add heavy-install rule next pass if needed.

