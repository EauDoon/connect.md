# Guest drafting tools

Guest documents remain in React memory. No browser storage, account, or upload is required.

## Recovery files

Open **Session recovery** in either editing mode to download the current source and up to five named checkpoints as a local JSON file. Invalid work in progress can be backed up without passing the Markdown download gate. Each source is limited to 128 KiB. This file contains your document text, so keep it privately. Downloading it does not validate a document, publish it, clear the reload warning, or save anything automatically.

To reopen it, choose the recovery JSON file, inspect its document type and checkpoint names, then confirm replacement. Parsing and all bounds succeed before any draft state changes. Unsupported versions, duplicate checkpoint names, invalid UTF-8, and oversized sources fail without replacing your work. If the draft changes during review, reopen the file before restoring. Restoration replaces the complete checkpoint set; ordinary Markdown editing normalization still applies to the active source.

## Undo a replacement

After importing, resetting, changing document kind, or restoring a checkpoint, **Session recovery** offers one previous draft for undo, limited to 128 KiB. Undo replaces subsequent edits after confirmation, restores source and kind only, and detaches any saved-server association. Checkpoints remain unchanged. This previous source disappears on reload or account-boundary reset. Forget it deliberately when no longer needed; a previous source that differs from its reusable starter keeps the reload warning active.

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

## Compare an updated export

After a Markdown download, **Changes since last Markdown download** compares the current bytes with the exact source used for that download request, including changes of document kind. The display is bounded to 80 lines and 12,000 characters per side; it is a changed-region comparison rather than a full multi-hunk diff. Only another Markdown download updates this baseline. Clipboard copies, checkpoint downloads, and recovery/review files do not move it. The app cannot verify that the browser actually saved a file on disk.

Search can optionally ignore case or require whole words. Word boundaries include Unicode letters, marks, numbers, and underscores. Literal punctuation remains literal.

Choose **Body only** to exclude opening YAML frontmatter from matching and replacement. An unclosed frontmatter block yields no body matches; fix the delimiter before replacing.

Replacement actions first open a bounded before/after review. Apply the reviewed result or cancel without changes. Edits to the source or search settings disable an old review until it is regenerated.

Applied search replacements now keep the prior source in **Session recovery**, using the same single-step undo as imports. Undo preserves checkpoints but replaces later edits; keep named checkpoints for multiple versions.

In **Document outline and length**, enter a source line and choose **Go to line**. This selects the exact line, including frontmatter or blank lines, in the plain-text editor. Out-of-range requests preserve your selection and explain the error.

Filter headings by case-insensitive text to reach sections beyond the first 60 displayed entries. Counts reflect all matching headings, and clearing the filter restores the full outline without editing the draft.

Writing review now flags skipped heading levels and repeated heading labels, with source-line navigation. Fenced examples and frontmatter do not create outline findings. These are suggestions, not schema errors.

Writing review inspects simple inline Markdown links for empty destinations, unsupported schemes, relative/local addresses, and fragment links. Fragment navigation is not wired in the local preview. No address is fetched; reference-style links, nested link syntax, inline code, and destination availability still require manual review.

Choose **Review excerpt** beside an outline heading to inspect and download that section with its child sections. Excerpts exclude frontmatter and neighboring sections, retain normalized Markdown, and use an `excerpt-` filename. They are not complete validated documents. Draft edits disable a stale excerpt download until you select it again; excerpts do not update the full-document download receipt or clear reload warnings.

**Download unfinished source** in Session recovery saves the exact current source as a clearly named `-unfinished.md` file even when validation fails. It is bounded to 128 KiB and excludes checkpoints. Paste it directly into the source editor to resume, because validated import can reject unfinished files. This backup does not clear validation errors, change the validated download receipt, or dismiss reload protection.
