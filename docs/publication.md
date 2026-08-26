# Publication contract

Humans own publication. This file is the source contract. A separately hosted live surface must honor it. Cloning this repository does not grant write tools.

## Draft vs public

- A draft is private. It does not appear in Discover, sitemap, or `/api/v1/directory`.
- Publish is an explicit human action. Saving is not publishing.
- Unpublish conceals the public projection. The draft remains with the owner.
- Visibility in stored Markdown must match the published state after publish or unpublish.

## Handle

- The owner chooses a handle before the first save.
- Placeholder handles (`your-handle`, `handle`, `member`, `untitled`) cannot be published.
- After the first save the handle is locked. The public URL is `/p/{handle}`.

## After publish

The owner must be able to reach:

- the human page `/p/{handle}`
- canonical Markdown (`/p/{handle}.md` or `/api/v1/profiles/{handle}/markdown`)
- public JSON (`/api/v1/profiles/{handle}`)

Public JSON and directory catalogs must not leak `userId` or dump full Markdown in the catalog.

## What agents may do

- Read public GET routes and canonical Markdown.
- Treat directory `contract.writesOffered` as `false` unless a running API has issued scoped grants.

## What agents must not do

- Publish, unpublish, or edit another person's document.
- Submit applications.
- Send contact as if it were accepted.
- Fetch arbitrary URLs.
- Invent employers, titles, dates, or metrics.

Contact remains a request. Applications remain a signed-in human with a public profile.

See [live-surface.md](live-surface.md) and [llms.txt](../llms.txt).
