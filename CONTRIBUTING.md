# Contributing

Thanks for your interest in this project. Pull requests are welcome.

## Ground rules

- Keep changes scoped. One logical change per pull request.
- Match the existing code style and tooling. Do not reformat unrelated lines.
- Do not commit secrets, credentials, or personal data. Use the example env
  file as the template for local configuration.
- Be respectful in review. Assume good intent and explain your reasoning.

## Before opening a pull request

1. Tests: add or update tests that cover your change. The full test suite
   must pass locally before you request review.
2. CI: confirm the continuous integration pipeline is green on your branch.
   A pull request that fails CI will not be merged.
3. Description: write a short summary of the problem, the fix, and any
   follow up work you noticed but did not address in this pull request.

## Local checks

The repository root is not a project. There is no root `package.json`,
`pyproject.toml`, `pytest.ini`, or `setup.cfg`, so these commands have to run
from the directory continuous integration uses for each job, which is the
`working-directory` set on that job in `.github/workflows/ci.yml`.

From `apps/api`:

    python -m pip install --require-hashes -r requirements-test.lock
    ruff check .
    mypy app
    pytest -q -m "not integration" tests

From `apps/web`:

    npm ci
    npm run lint
    npm run typecheck
    npm test

Those are the checks that gate a pull request. CI also builds the pinned API
image, audits the API lockfile, audits the web lockfile, and runs the web
production harness under Playwright; those need a Docker daemon or a browser
install and are not a prerequisite for requesting review.

`apps/api/README.md` documents the API's own run and verify steps, including
`alembic upgrade head`.

## Reporting issues

Open a GitHub issue for bugs, feature requests, or questions. For security
related reports, follow SECURITY.md instead of filing a public issue.
