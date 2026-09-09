export async function copyLocalMarkdown(markdown: string) {
  if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable. Download the .md file or select text in the editor instead.");
  try { await navigator.clipboard.writeText(markdown); }
  catch { throw new Error("The browser denied clipboard access. Download the .md file or select text in the editor instead."); }
}
