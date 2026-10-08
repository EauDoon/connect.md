"""connect.md API package."""

# Derived from the repository VERSION file; tools/release_version.py keeps it
# equal (`set`) and CI fails when it drifts (`check`). It feeds the FastAPI
# app version, and through it OpenAPI, the Agent Card, MCP serverInfo, and
# `python -m app.cli --version`.
__version__ = "0.2.4"
