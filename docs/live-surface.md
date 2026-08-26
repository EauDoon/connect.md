# Live surface vs this source tree

This repository is the pre-launch **source** of connect.md: FastAPI in `apps/api`, Next.js in `apps/web`, canonical Markdown schemas, and agent examples.

A separately hosted live network (for example a Grok App Builder preview) is **not** this tree. It must not replace `apps/web` or `apps/api`. It may read the same product invariants:

- Markdown is canonical. Humans own publication.
- Drafts stay private.
- Contact is a request.
- Agents do not submit applications.
- No arbitrary URL fetch.
- No invented employers, titles, or metrics.
- Discovery is information, not permission.
- MCP/A2A write tools are not granted by cloning this repository. `writesOffered` is false until a running API issues scoped grants.

Agents reading a live host should start at that host's `/llms.txt` and `/api/v1/directory`. Agents reading this git tree should start at [`/llms.txt`](../llms.txt) and [`docs/agent-source-map.md`](agent-source-map.md).
