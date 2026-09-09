# Guest drafting tools

Guest documents remain in React memory. No browser storage, account, or upload is required.

## Recovery files

Open **Session recovery** in either editing mode to download the current source and up to five named checkpoints as a local JSON file. Invalid work in progress can be backed up without passing the Markdown download gate. Each source is limited to 128 KiB. This file contains your document text, so keep it privately. Downloading it does not validate a document, publish it, clear the reload warning, or save anything automatically.

To reopen it, choose the recovery JSON file, inspect its document type and checkpoint names, then confirm replacement. Parsing and all bounds succeed before any draft state changes. Unsupported versions, duplicate checkpoint names, invalid UTF-8, and oversized sources fail without replacing your work. If the draft changes during review, reopen the file before restoring. Restoration replaces the complete checkpoint set; ordinary Markdown editing normalization still applies to the active source.
