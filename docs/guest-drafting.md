# Guest drafting tools

Guest documents remain in React memory. No browser storage, account, or upload is required.

## Recovery files

Open **Session recovery** in either editing mode to download the current source and up to five named checkpoints as a local JSON file. Invalid work in progress can be backed up without passing the Markdown download gate. Each source is limited to 128 KiB. This file contains your document text, so keep it privately. Downloading it does not validate a document, publish it, clear the reload warning, or save anything automatically.

To reopen it, choose the recovery JSON file, inspect its document type and checkpoint names, then confirm replacement. Parsing and all bounds succeed before any draft state changes. Unsupported versions, duplicate checkpoint names, invalid UTF-8, and oversized sources fail without replacing your work. If the draft changes during review, reopen the file before restoring. Restoration replaces the complete checkpoint set; ordinary Markdown editing normalization still applies to the active source.

## Undo a replacement

After importing, resetting, changing document kind, or restoring a checkpoint, **Session recovery** offers one previous draft for undo, limited to 128 KiB. Undo replaces subsequent edits after confirmation, restores source and kind only, and detaches any saved-server association. Checkpoints remain unchanged. This previous source disappears on reload or account-boundary reset. Forget it deliberately when no longer needed; the browser keeps warning about this in-memory copy until then.

## Checkpoint names

Use **Rename** beside a checkpoint to update its name while retaining its exact source, type, order, and comparison selection. Names must remain distinct (case-insensitively), with 1 to 60 characters. Cancellation and validation errors preserve the old name.

Use **Download source** beside a checkpoint to keep its exact captured Markdown without restoring it or changing the current draft. A checkpoint may contain invalid work in progress; this action is a source backup, not the validated document export. It does not mark the current draft as downloaded.

## Find and replace

Markdown Mode offers case-sensitive literal search across the whole source, including frontmatter. Previous/Next select exact text in the plain editor. Replace one match or confirm replacement of all matches. A 128 KiB source limit, 256-character query limit, 1,000-match cap, and precomputed output-size bound prevent partial or oversized bulk edits. Keep a checkpoint before bulk replacement.

## Name an export

The download gate accepts an optional local filename and shows the sanitized `.md` name before downloading. This never changes frontmatter identifiers. Names use portable lowercase ASCII characters, with an 80-character stem and protection against reserved Windows device names. A failed browser download request leaves the source and the prior download receipt intact, with copy/recovery alternatives.

## Act on review findings

Line links in Sharing and Writing review open the same draft in Markdown Mode, select the corresponding source line, and focus the plain-text editor. The request stays in memory; document text is never put into URLs. Review checks remain advisory and do not redact, approve, or block source content.

## Focus the workspace

In Markdown Mode, choose **Source only**, **Preview and checks**, or **Split view**. Source and preview always derive from the same draft. Layout and code/plain-editor preferences survive Guided/Markdown navigation within the page session; reload and account resets clear them. Following a review line returns to split view and focuses the plain editor so the requested source cannot remain hidden.
