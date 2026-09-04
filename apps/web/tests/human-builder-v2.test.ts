import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function humanModeSource() {
  return [
    "../components/human-builder.tsx",
    "../components/human-buffered-fields.tsx",
    "../components/human-guided-fields.tsx",
  ]
    .map((relativePath) =>
      readFileSync(new URL(relativePath, import.meta.url), "utf8"),
    )
    .join("\n");
}

describe("Human Mode v2 accessibility surface", () => {
  it("keeps canonical draft and journey authority in the builder while delegating field infrastructure", () => {
    const builder = readFileSync(
      new URL("../components/human-builder.tsx", import.meta.url),
      "utf8",
    );
    const fields = [
      readFileSync(
        new URL("../components/human-buffered-fields.tsx", import.meta.url),
        "utf8",
      ),
      readFileSync(
        new URL("../components/human-guided-fields.tsx", import.meta.url),
        "utf8",
      ),
    ].join("\n");

    expect(builder).toContain('from "@/components/human-buffered-fields"');
    expect(builder).toContain('from "@/components/human-guided-fields"');
    expect(builder).not.toContain("function BufferedInput(");
    expect(builder).not.toContain("function BufferedTextarea(");
    expect(builder).not.toContain("function GuidedEntriesEditor(");
    expect(builder).not.toContain("function StructuredV2Fields(");
    for (const authorityMarker of [
      "patchHumanFields(",
      "setHumanStage(",
      "setMarkdown(",
      "shouldConfirmDraftReplacement(",
      "validateDraft(",
      "PublishPanel",
    ]) {
      expect(builder).toContain(authorityMarker);
      expect(fields).not.toContain(authorityMarker);
    }
  });

  it("keeps critical orientation visible before client-side motion starts", () => {
    const source = humanModeSource();
    expect(source).toContain('<motion.div initial={false} animate="visible"');
    expect(source).not.toContain('initial={reducedMotion ? false : "hidden"}');
  });

  it("uses labelled, keyboard-native controls for every guided v2 signal", () => {
    const source = humanModeSource();
    expect(source).toContain("<fieldset");
    expect(source).toContain("<legend");
    expect(source).toContain("<details");
    expect(source).toContain("More professional signals");
    for (const label of ["Discovery", "Availability", "Representation and contact", "Occupations", "Industries", "Languages", "Proficiency for new languages", "Seniority", "Work modes", "Open to", "Organizations", "Contact disclosure", "Public representation"]) expect(source).toContain(label);
    expect(source).toContain('type="checkbox"');
    expect(source).toContain('type="date"');
    expect(source).toContain("focus-visible:outline");
    expect(source).toContain("GuidedEntriesEditor");
    expect(source).toContain('kind === "experience" ? "role" : "education"');
    expect(source).toContain("One outcome per line—no Markdown needed");
    expect(source).toContain("Advanced Markdown preserved");
    expect(source).toContain("swapGuidedEntries");
    expect(source).toContain('aria-live="polite"');
    expect(source).toContain("focusAfterChange");
    expect(source).toContain("BufferedTextarea");
    expect(source).toContain("Journey position");
    expect(source).not.toContain("Journey progress");
    expect(source).toContain('type="radio"');
    expect(source).toContain('name="human-document-kind"');
    expect(source).toContain("checked={selected}");
    expect(source).not.toContain('role="radio"');
    expect(source).not.toContain('role="radiogroup"');
    expect(source).not.toContain("aria-checked");
    expect(source).toContain("shouldConfirmDraftReplacement(markdown, kind, savedDocument)");
    expect(source).toContain("Unsaved content and saved-document identity will be replaced.");
    expect(source).toContain("selectDocumentKind(option.kind)");
    expect(source).toContain('<fieldset className="sm:col-span-2">');
    expect(source).toContain('<legend className="mb-1.5 block text-sm font-medium text-white">Work modes</legend>');
    expect(source).toContain('className="min-h-11 px-2"');
  });

  it("renders exactly one progressive chapter with explicit chapter navigation", () => {
    const source = humanModeSource();
    expect(source).toContain('aria-label="Human Mode chapter navigation"');
    expect(source).toContain("<ol className=");
    expect(source).toContain('className="grid min-w-0 grid-cols-1 gap-2 min-[300px]:grid-cols-2 sm:grid-cols-4"');
    expect(source).toContain("min-[360px]:min-h-24");
    expect(source).toContain("sm:min-h-20");
    expect(source).toContain("min-[360px]:block");
    expect(source).not.toContain('block truncate text-sm font-semibold text-white">{step.label}');
    expect(source).not.toContain('block truncate text-xs text-mist">{step.detail}');
    expect(source).toContain("<motion.div key={activeStage}");
    expect(source).toContain("function ChapterNavigation");
    expect(source).toContain("Review document");
    expect(source).toContain("Download document");
    expect(source).toContain("id={`human-stage-${stage}`}");
    for (const stage of ["foundation", "shape", "review", "release"]) {
      expect(source).toContain(`activeStage === "${stage}" && <JourneyChapter`);
    }
    expect(source).not.toContain("return <motion.section");
    expect(source).not.toContain("active ? 1 : 0.82");
  });

  it("moves focus once to the active heading and makes the reduced-motion transition immediate", () => {
    const source = humanModeSource();
    expect(source).toContain("pendingStageFocusRef");
    expect(source).toContain("document.getElementById(`human-stage-${activeStage}-title`)");
    expect(source).toContain('className="mt-5 scroll-mt-24');
    expect(source).toContain("heading?.focus({ preventScroll: true });");
    expect(source).toContain("pendingStageFocusRef.current = null;");
    const scrollIndex = source.indexOf("heading?.scrollIntoView");
    const focusIndex = source.indexOf("heading?.focus({ preventScroll: true });");
    expect(scrollIndex).toBeGreaterThan(-1);
    expect(focusIndex).toBeGreaterThan(scrollIndex);
    expect(source.match(/heading\?\.focus\(\{ preventScroll: true \}\);/g)).toHaveLength(1);
    expect(source).toContain("initial={reducedMotion ? false : { y: 12 }}");
    expect(source).toContain("animate={reducedMotion ? undefined : { y: 0 }}");
    expect(source).not.toContain("key={activeStage} initial={reducedMotion ? false : { opacity:");
    expect(source).toContain('duration: reducedMotion ? 0 : 0.22');
  });

  it("keeps mobile review navigation compact and buffers narrative commits before a stage changes", () => {
    const source = humanModeSource();
    expect(source).toContain('aria-label="Compact stage controls"');
    expect(source).toContain("top-16");
    expect(source).toContain("md:hidden");
    expect(source).toContain("BufferedCommitRegistry.Provider");
    expect(source).toContain("flushBufferedFields();");
    expect(source).toContain('<BufferedTextarea id="narrative"');
    expect(source).not.toContain('<Textarea id="narrative"');
    expect(source).toContain("patchHumanFields(canonicalMarkdownRef.current, kind, patchFields)");
    expect(source).toContain("Advanced Markdown preserved");
    expect(source).toContain("Unknown frontmatter and unedited sections stay intact.");
    expect(source).toContain("isEmptyDraft(markdown)");
    expect(source).toContain("replaceMarkdown(starterFor(kind))");
    expect(source).toContain("Human Mode is locked because the draft is empty.");
    expect(source).toContain("Restore the starter template or paste a complete Markdown file in Markdown Mode.");
    expect(source).toContain("Reset starter");
  });

  it("preserves reduced-motion scrolling and keyboard-native stage controls", () => {
    const source = humanModeSource();
    expect(source).toContain('behavior: reducedMotion ? "auto" : "smooth"');
    expect(source).toContain('aria-current={active ? "step" : undefined}');
    expect(source).toContain('onClick={() => activateStage(step.id)}');
    expect(source).toContain('<button type="button"');
  });

  it("surfaces the agent-draft paste path before Foundation navigation without auto-publishing or persistence", () => {
    const source = humanModeSource();
    const calloutIndex = source.indexOf('aria-label="Agent draft paste guidance"');
    const navigationIndex = source.indexOf('<ChapterNavigation stage="foundation"');
    expect(calloutIndex).toBeGreaterThan(-1);
    expect(calloutIndex).toBeLessThan(navigationIndex);
    expect(source).toContain('href="/md"');
    expect(source).toContain("Have an existing or agent-produced draft?");
    expect(source).toContain("Open its local .md file above, or paste the complete Markdown in direct mode.");
    expect(source).toContain("It stays in this browser session until you download it.");
    expect(source).toContain("Downloading is the only release action in this standalone site.");
    expect(source).toContain("nothing is uploaded");
    expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/u);
  });
});
