import { documentMetrics } from "@/lib/document-metrics";
import { reviewPrivacy } from "@/lib/privacy-review";
import { reviewWriting } from "@/lib/writing-review";
import { type DocumentKind } from "@/lib/markdown";
import { validateDraft } from "@/lib/validation";

export async function buildReviewReport(markdown: string, kind: DocumentKind) {
  const metrics = documentMetrics(markdown);
  if (metrics.limited) throw new Error("Reduce the draft to 128 KiB before creating a review report.");
  if (!globalThis.crypto?.subtle) throw new Error("A secure browser context is required to fingerprint the draft. Download the Markdown file instead.");
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(markdown));
  const digest = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const validation = validateDraft(markdown, kind);
  const privacy = reviewPrivacy(markdown);
  const writing = reviewWriting(markdown);
  const findingLines = (findings: Array<{ line: number; message: string }>) => findings.length
    ? findings.map((finding) => `- Line ${finding.line}: ${finding.message}`).join("\n")
    : "No configured patterns were found.";
  return `# Local draft review\n\nThis report describes one browser-local draft. It is not factual verification, a security clearance, or proof that a file was saved or published. The source document is not included.\n\n## Exact source\n\n- Document kind: ${kind}\n- SHA-256 (UTF-8 source): ${digest}\n- Source bytes: ${metrics.bytes}\n- Approximate body words: ${metrics.words}\n- Headings: ${metrics.headings.length}\n\nThe fingerprint applies only to these exact bytes. Recreate this report after editing. Keep the Markdown download separately.\n\n## Schema validation\n\n- Errors: ${validation.filter((issue) => issue.level === "error").length}\n- Warnings: ${validation.filter((issue) => issue.level === "warning").length}\n\nReview detailed validation messages in the editor. Counts do not verify claims.\n\n## Sharing review\n\n${findingLines(privacy.findings)}\n\n${privacy.limited ? "This section is limited to the first 20 findings." : "Pattern checks are incomplete. Inspect the full source before sharing."}\n\n## Writing review\n\n${findingLines(writing.findings)}\n\n${writing.limited ? "This section is limited to the first 20 suggestions." : "Suggestions are advisory and do not judge your experience."}\n`;
}
