from __future__ import annotations


def profile_markdown(*, visibility: str = "private", headline: str = "Backend engineer") -> str:
    return f"""---
schema: connect.md/profile
schema_version: 1
handle: ada-lovelace
name: Ada Lovelace
headline: {headline}
location: Singapore
skills:
  - Python
  - Systems design
visibility: {visibility}
---
# Ada Lovelace

## About

Builds reliable systems.

## Experience

### connect.md

Designed an API.

## Skills

- Python
- Systems design
"""


def resume_markdown() -> str:
    return """---
schema: connect.md/resume
schema_version: 1
slug: ada-lovelace-resume
name: Ada Lovelace
title: Backend engineer
headline: Builds reliable systems
location: Singapore
skills:
  - Python
visibility: private
---
# Ada Lovelace

## Summary

Backend engineer.

## Experience

### connect.md

Built an API.

## Education

Independent study.

## Skills

- Python
"""
