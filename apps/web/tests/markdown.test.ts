import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";

import { frontmatterParseIssue, humanFieldsFromMarkdown, patchHumanFields, profileStarter, resumeStarter, splitFrontmatter, switchDocumentKind } from "../lib/markdown";
import { hasValidationErrors, validateDraft } from "../lib/validation";
import { apiRequest, createApiKey, fetchPublicResumeMarkdown, ingestMetadataFromResponse, listApiKeys, loadDocument, type DocumentResponse, markdownFromIngestResponse, presentApiKeyError, presentSaveError, revokeApiKey, saveDocument, searchIndexingStateFromHeader } from "../lib/api";
import { maskOwnedDraftSnapshot, requiresDraftReset, resolvedDraftSubject, shouldMaskOwnedDraft, SIGNED_OUT_DRAFT_SUBJECT } from "../lib/draft-security";
import { isImportResultCurrent, shouldConfirmDraftReplacement } from "../lib/draft-replacement";
import { MarkdownPreview } from "../components/markdown-preview";
import { discardedSuccessfulSaveMessage, priorAccountSuccessfulSaveMessage, reconcileSaveResponse, savedDocumentIdentity, uncertainSaveOutcomeMessage, type SaveSnapshot } from "../lib/save-reconciliation";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("canonical Markdown draft helpers", () => {
  const v2Profile = `---
schema: connect.md/profile
schema_version: 2
handle: ari-chen
name: Ari Chen
headline: Product leader
occupations:
  - scheme: connectmd-occupation
    id: product-manager
    label: Product manager
industries: []
location:
  scheme: geonames
  id: '1880252'
  label: Singapore
skills:
  - scheme: connectmd-skill
    id: payments
    label: Payments
languages: []
seniority:
  scheme: connectmd-seniority
  id: executive
  label: Executive
work_modes: [hybrid]
availability:
  status: not_disclosed
open_to: []
organizations: []
public_representation:
  status: self
contact:
  disclosure: platform_only
visibility: private
---
# Ari Chen

## About

Product leader.

## Experience

Experience.

## Skills

- Payments
`;
  const v1Profile = `---
schema: connect.md/profile
schema_version: 1
handle: legacy-profile
name: Legacy Profile
headline: A readable v1 draft
location: City, Country
skills:
  - Strategy
visibility: private
---
# Legacy Profile

## About

Legacy profile.

## Experience

Legacy experience.

## Skills

- Strategy
`;

  it("updates Human Mode fields in the canonical Markdown buffer", () => {
    const updated = patchHumanFields(profileStarter, "profile", {
      name: "Ari Chen",
      handle: "ari-chen",
      skills: ["Design", "Research"],
      narrative: "I turn complex systems into useful tools."
    });
    const parsed = splitFrontmatter(updated);

    expect(parsed.attributes.name).toBe("Ari Chen");
    expect(parsed.attributes.schema).toBe("connect.md/profile");
    expect(parsed.attributes.handle).toBe("ari-chen");
    expect(parsed.attributes.skills).toEqual([
      { scheme: "connectmd-user-skill", id: "design", label: "Design" },
      { scheme: "connectmd-user-skill", id: "research", label: "Research" }
    ]);
    expect(parsed.body).toContain("# Ari Chen");
    expect(parsed.body).toContain("I turn complex systems into useful tools.");
    expect(updated).toContain("## Experience");
  });

  it("hydrates the same fields back from Markdown", () => {
    const draft = patchHumanFields(profileStarter, "profile", { name: "Ari Chen", handle: "ari-chen", location: "Singapore" });
    expect(humanFieldsFromMarkdown(draft, "profile")).toMatchObject({ name: "Ari Chen", handle: "ari-chen", location: "Singapore" });
  });

  it("validates and safely edits canonical nested schema-v2 drafts", () => {
    expect(hasValidationErrors(validateDraft(v2Profile, "profile"))).toBe(false);
    const updated = patchHumanFields(v2Profile, "profile", {
      location: "London, United Kingdom",
      skills: ["Payments", "Strategy"]
    });
    const parsed = splitFrontmatter(updated);
    expect(parsed.attributes.schema_version).toBe(2);
    expect(parsed.attributes.location).toEqual({
      scheme: "connectmd-user-location",
      id: "london-united-kingdom",
      label: "London, United Kingdom"
    });
    expect(parsed.attributes.skills).toEqual([
      { scheme: "connectmd-skill", id: "payments", label: "Payments" },
      { scheme: "connectmd-user-skill", id: "strategy", label: "Strategy" }
    ]);
    expect(hasValidationErrors(validateDraft(updated, "profile"))).toBe(false);
  });

  it("trims Human Mode structured scalars and skill items while keeping name and H1 identical", () => {
    const updated = patchHumanFields(profileStarter, "profile", { name: "  Ada Lovelace  ", headline: "  Computing pioneer  ", skills: [" Mathematics ", " Logic "] });
    const parsed = splitFrontmatter(updated);

    expect(parsed.attributes).toMatchObject({ name: "Ada Lovelace", headline: "Computing pioneer", skills: [{ label: "Mathematics" }, { label: "Logic" }] });
    expect(parsed.body).toContain("# Ada Lovelace\n");
  });

  it("fails closed when Human Mode receives malformed YAML", () => {
    const malformed = profileStarter.replace("name: Your Name", "name: [unterminated");
    expect(frontmatterParseIssue(malformed)).toContain("invalid");
    expect(() => patchHumanFields(malformed, "profile", { name: "Ada" })).toThrow("cannot edit this draft");
  });

  it("reports client-side schema issues before publish", () => {
    const invalid = profileStarter.replace("handle: your-handle", "handle: Invalid Handle");
    const issues = validateDraft(invalid, "profile");
    expect(hasValidationErrors(issues)).toBe(true);
    expect(issues.map((issue) => issue.message).join(" ")).toContain("lowercase handle");
  });

  it("enforces structured field bounds, surrounding whitespace, and exact Markdown-safe names", () => {
    const invalid = v1Profile
      .replace("name: Your Name", "name: 'Ada #'")
      .replace("name: Legacy Profile", "name: 'Ada #'")
      .replace("# Legacy Profile", "# Ada #")
      .replace("headline: A readable v1 draft", `headline: '${"x".repeat(281)}'`)
      .replace("location: City, Country", "location: ' Singapore '")
      .replace("  - Strategy", `  - '${"s".repeat(81)}'`);
    const messages = validateDraft(invalid, "profile").map((issue) => issue.message).join(" ");

    expect(messages).toContain("name cannot end with #");
    expect(messages).toContain("headline must be 280 characters or fewer");
    expect(messages).toContain("location cannot have leading or trailing whitespace");
    expect(messages).toContain("Each skill must be 80 characters or fewer");

    const lineBreakName = v1Profile.replace("name: Legacy Profile", "name: \"Ada\\nLovelace\"");
    expect(validateDraft(lineBreakName, "profile").map((issue) => issue.message).join(" ")).toContain("name cannot contain line breaks");

    const lineBreakFields = resumeStarter
      .replace("schema_version: 2", "schema_version: 1")
      .replace(/occupations:[\s\S]*?visibility: private\n/u, "location: City, Country\nskills:\n  - Strategy\nvisibility: private\n")
      .replace("title: Professional title", "title: \"Product\\nleader\"")
      .replace("headline: Your professional headline", "headline: \"Systems\\nleader\"")
      .replace("location: City, Country", "location: \"Singapore\\nRemote\"")
      .replace("  - Strategy", "  - \"Systems\\nDesign\"");
    const lineBreakMessages = validateDraft(lineBreakFields, "resume").map((issue) => issue.message).join(" ");
    expect(lineBreakMessages).toContain("title cannot contain line breaks");
    expect(lineBreakMessages).toContain("headline cannot contain line breaks");
    expect(lineBreakMessages).toContain("location cannot contain line breaks");
    expect(lineBreakMessages).toContain("skills cannot contain line breaks");

    const tooManySkills = v1Profile.replace("skills:\n  - Strategy", `skills:\n${Array.from({ length: 51 }, (_, index) => `  - Skill ${index}`).join("\n")}`);
    expect(validateDraft(tooManySkills, "profile").map((issue) => issue.message).join(" ")).toContain("skills can contain at most 50 items");
  });

  it("accepts the API ingestion draft_markdown response field", () => {
    expect(markdownFromIngestResponse({ draft_markdown: profileStarter })).toBe(profileStarter);
    expect(ingestMetadataFromResponse({ warnings: ["Review dates", 4], provenance: { parser: "pdf", ignored: 3 } })).toEqual({ warnings: ["Review dates"], provenance: { parser: "pdf" } });
  });

  it("renders safe links without loading remote images", () => {
    const markdown = profileStarter.replace("## Skills\n\n- Unspecified skill\n", "## Skills\n\n- Unspecified skill\n\n[Example](https://example.test)\n\n![Tracker](https://images.example.test/pixel.png)\n");
    const html = renderToStaticMarkup(createElement(MarkdownPreview, { markdown }));

    expect(html).not.toContain("<img");
    expect(html).not.toContain("images.example.test");
    expect(html).toContain('rel="ugc nofollow noreferrer"');
  });

  it("shifts embedded heading semantics while preserving each source Markdown level", () => {
    const markdown = "# Title\n\n## Section\n\n### Detail\n\n###### Fine print";
    const defaultHtml = renderToStaticMarkup(createElement(MarkdownPreview, { markdown }));
    const embeddedHtml = renderToStaticMarkup(createElement(MarkdownPreview, { markdown, omitTitle: true, headingOffset: 2 }));

    expect(defaultHtml).toContain('<h1 data-markdown-heading-level="1">Title</h1>');
    expect(defaultHtml).toContain('<h2 data-markdown-heading-level="2">Section</h2>');
    expect(embeddedHtml).not.toContain("Title");
    expect(embeddedHtml).toContain('<h4 data-markdown-heading-level="2">Section</h4>');
    expect(embeddedHtml).toContain('<h5 data-markdown-heading-level="3">Detail</h5>');
    expect(embeddedHtml).toContain('<h6 data-markdown-heading-level="6">Fine print</h6>');
    expect(embeddedHtml).not.toMatch(/<h[12][ >]/u);
  });

  it("binds every embedded preview to its surrounding heading level", () => {
    const embeddedPreviewOffsets = [
      ["../components/application-snapshot.tsx", 4],
      ["../components/conversation-thread.tsx", 2],
      ["../components/human-builder.tsx", 3],
      ["../components/markdown-editor.tsx", 2],
      ["../components/moderation-appeal-review-queue.tsx", 2],
      ["../components/moderation-case-review-queue.tsx", 2],
      ["../components/post-composer.tsx", 2],
      ["../components/professional-post-card.tsx", 1],
    ] as const;

    for (const [relativePath, headingOffset] of embeddedPreviewOffsets) {
      const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
      expect(source, relativePath).toMatch(new RegExp(`<MarkdownPreview[\\s\\S]*?headingOffset=\\{${headingOffset}\\}`, "u"));
    }

    for (const relativePath of ["../components/public-document-page.tsx", "../components/public-post-page.tsx"] as const) {
      const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
      expect(source, relativePath).toContain("<MarkdownPreview");
      expect(source, relativePath).toContain("omitTitle");
      expect(source, relativePath).not.toContain("headingOffset=");
    }

    const styles = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
    expect(styles).toContain('.markdown-prose [data-markdown-heading-level="1"]');
    expect(styles).not.toContain(".markdown-prose h1");
  });

  it("creates a valid resume starter with every required field and heading in order", () => {
    const parsed = splitFrontmatter(resumeStarter);
    const headings = [...parsed.body.matchAll(/^##\s+(.+?)\s*$/gm)].map((match) => match[1]);

    expect(parsed.attributes).toMatchObject({ schema: "connect.md/resume", slug: "your-name-resume", title: "Professional title" });
    expect(headings).toEqual(["Summary", "Experience", "Education", "Skills"]);
    expect(hasValidationErrors(validateDraft(resumeStarter, "resume"))).toBe(false);
  });

  it("switches a canonical profile to a valid resume without losing section content", () => {
    const profile = profileStarter
      .replace("schema_version: 2\n", "schema_version: 2\nid: 3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f\nowner_id: user_example\nversion: 4\nupdated_at: '2026-08-03T00:00:00Z'\n")
      .replace("Write a concise introduction that makes it easy to understand your work.", "First paragraph.\n\nSecond paragraph.\n\n- Durable point")
      .replace("Your professional headline", "Product leader");
    const resume = switchDocumentKind(profile, "resume");
    const parsed = splitFrontmatter(resume);
    const headings = [...parsed.body.matchAll(/^##\s+(.+?)\s*$/gm)].map((match) => match[1]);

    expect(parsed.attributes).toMatchObject({ schema: "connect.md/resume", slug: "your-handle-resume", title: "Product leader" });
    expect(parsed.attributes).not.toHaveProperty("id");
    expect(parsed.attributes).not.toHaveProperty("owner_id");
    expect(headings).toEqual(["Summary", "Experience", "Education", "Skills"]);
    expect(resume).toContain("First paragraph.\n\nSecond paragraph.\n\n- Durable point");
    expect(resume).toContain("### Current role");
    expect(hasValidationErrors(validateDraft(resume, "resume"))).toBe(false);
  });

  it("round-trips a multiline Human Mode narrative through the canonical section", () => {
    const narrative = "Opening paragraph.\n\n```md\n## Skills\n```\n\nSecond paragraph.\n\n- First point\n- Second point";
    const updated = patchHumanFields(profileStarter, "profile", { narrative });
    const fields = humanFieldsFromMarkdown(updated, "profile");

    expect(fields.narrative).toBe(narrative);
    expect(updated).not.toContain("Write a concise introduction that makes it easy to understand your work.");
    expect(updated.indexOf("- Second point")).toBeLessThan(updated.indexOf("## Experience"));
  });

  it("synchronizes Human Mode skills into frontmatter and the required body list", () => {
    const updated = patchHumanFields(profileStarter, "profile", { skills: ["Systems Design", "Research"] });
    const parsed = splitFrontmatter(updated);

    expect(parsed.attributes.skills).toEqual([
      { scheme: "connectmd-user-skill", id: "systems-design", label: "Systems Design" },
      { scheme: "connectmd-user-skill", id: "research", label: "Research" }
    ]);
    expect(parsed.body).toContain("## Skills\n\n- Systems Design\n- Research");
    expect(parsed.body).toContain("Describe the impact, scope, and outcomes of your work.");
    expect(parsed.body).not.toContain("- Strategy");
  });

  it("synchronizes guided Experience and Education fields without changing other sections", () => {
    const custom = resumeStarter.replace("## Skills\n\n- Unspecified skill", "## Projects\n\nA freeform project.\n\n## Skills\n\n- Unspecified skill");
    const experience = "### Principal · Example Co\n\n- Shipped a durable system.";
    const education = "### Example University\n\nComputer Science · 2024";
    const updated = patchHumanFields(custom, "resume", { experience, education });
    const fields = humanFieldsFromMarkdown(updated, "resume");

    expect(fields.experience).toBe(experience);
    expect(fields.education).toBe(education);
    expect(updated).toContain("## Projects\n\nA freeform project.");
    expect(updated.indexOf("## Experience")).toBeLessThan(updated.indexOf("## Education"));
    expect(updated.indexOf("## Education")).toBeLessThan(updated.indexOf("## Skills"));
  });

  it("uses valid private v2 starters while preserving v1 editing compatibility", () => {
    expect(splitFrontmatter(profileStarter).attributes).toMatchObject({ schema_version: 2, visibility: "private", availability: { status: "not_disclosed" }, public_representation: { status: "not_disclosed" }, contact: { disclosure: "none" }, work_modes: [] });
    expect(hasValidationErrors(validateDraft(profileStarter, "profile"))).toBe(false);
    const publicStarterIssues = validateDraft(profileStarter.replace("visibility: private", "visibility: public"), "profile").map((issue) => issue.message).join(" ");
    expect(publicStarterIssues).toContain("Replace the default Unspecified occupation before public publication");
    const publicReady = patchHumanFields(profileStarter, "profile", { visibility: "public", occupations: ["Researcher"], location: "Singapore", skills: ["Research"] });
    expect(hasValidationErrors(validateDraft(publicReady, "profile"))).toBe(false);
    const legacy = patchHumanFields(v1Profile, "profile", { skills: ["Strategy", "Research"], location: "Singapore" });
    expect(splitFrontmatter(legacy).attributes).toMatchObject({ schema_version: 1, location: "Singapore", skills: ["Strategy", "Research"] });
    expect(hasValidationErrors(validateDraft(legacy, "profile"))).toBe(false);
  });

  it("preserves existing reference identities for unchanged v2 labels and creates deterministic user references for new labels", () => {
    const unchanged = patchHumanFields(v2Profile, "profile", { occupations: ["Product manager"], skills: ["Payments"] });
    expect(splitFrontmatter(unchanged).attributes.occupations).toEqual([{ scheme: "connectmd-occupation", id: "product-manager", label: "Product manager" }]);
    expect(splitFrontmatter(unchanged).attributes.skills).toEqual([{ scheme: "connectmd-skill", id: "payments", label: "Payments" }]);
    const updated = patchHumanFields(v2Profile, "profile", { occupations: ["Product manager", "Research lead"], industries: ["Financial services"], languages: ["English"], languageProficiency: "professional", seniority: "Senior leader", openTo: ["Advisory"], organizations: ["Example Co"], organizationRelationship: "founder", representationStatus: "authorized_representative", representative: "Example Representation", contactDisclosure: "public", contactType: "email", contactValue: "hello@example.test", contactLabel: "Work email", availabilityStatus: "available_from", availableFrom: "2026-09-01" });
    const attributes = splitFrontmatter(updated).attributes;
    expect(attributes.occupations).toEqual(expect.arrayContaining([{ scheme: "connectmd-user-occupation", id: "research-lead", label: "Research lead" }]));
    expect(attributes.languages).toEqual([{ scheme: "connectmd-user-language", id: "english", label: "English", proficiency: "professional" }]);
    expect(attributes.organizations).toEqual([{ scheme: "connectmd-user-organization", id: "example-co", label: "Example Co", relationship: "founder" }]);
    expect(attributes.public_representation).toMatchObject({ status: "authorized_representative", representative: { scheme: "connectmd-user-representative", id: "example-representation", label: "Example Representation" } });
    expect(attributes.contact).toEqual({ disclosure: "public", channels: [{ type: "email", value: "hello@example.test", label: "Work email" }] });
    expect(hasValidationErrors(validateDraft(updated, "profile"))).toBe(false);
  });

  it("patches only guided v2 fields without removing unknown frontmatter or sections", () => {
    const custom = v2Profile
      .replace("visibility: private", "import_context:\n  source: user-upload\n  confidence: low\nvisibility: private")
      .replace("## Skills", "## Projects\n\nAn independent section.\n\n## Skills");
    const updated = patchHumanFields(custom, "profile", { headline: "Updated signal" });
    expect(splitFrontmatter(updated).attributes).toMatchObject({ headline: "Updated signal", import_context: { source: "user-upload", confidence: "low" } });
    expect(updated).toContain("## Projects\n\nAn independent section.");
  });

  it("keeps v2 structured fields through a mode-kind round trip and reports v2 list bounds", () => {
    const enriched = patchHumanFields(v2Profile, "profile", { industries: ["Financial services"], languages: ["English"], languageProficiency: "professional", workModes: ["hybrid", "remote"], organizations: ["Example Co"], organizationRelationship: "member" });
    const roundTrip = switchDocumentKind(switchDocumentKind(enriched, "resume"), "profile");
    const attributes = splitFrontmatter(roundTrip).attributes;
    expect(attributes).toMatchObject({ schema: "connect.md/profile", schema_version: 2, industries: [{ label: "Financial services" }], languages: [{ label: "English", proficiency: "professional" }], work_modes: ["hybrid", "remote"], organizations: [{ label: "Example Co", relationship: "member" }] });
    expect(roundTrip).toContain("## About");
    const overBound = patchHumanFields(v2Profile, "profile", { occupations: Array.from({ length: 21 }, (_, index) => `Occupation ${index}`) });
    expect(validateDraft(overBound, "profile").map((issue) => issue.message).join(" ")).toContain("occupations can contain at most 20 items");
  });

  it("requires an explicit proficiency for new languages and gives user labels collision-safe IDs", () => {
    const missingProficiency = patchHumanFields(v2Profile, "profile", { languages: ["English"] });
    expect(validateDraft(missingProficiency, "profile").map((issue) => issue.message).join(" ")).toContain("languages[0].proficiency is invalid");

    const fields = { skills: ["研究", "研究者", "Design", "design"] };
    const first = splitFrontmatter(patchHumanFields(v2Profile, "profile", fields)).attributes.skills;
    const second = splitFrontmatter(patchHumanFields(v2Profile, "profile", fields)).attributes.skills;
    expect(first).toEqual(second);
    expect(first).toEqual(expect.arrayContaining([expect.objectContaining({ label: "研究" }), expect.objectContaining({ label: "研究者" }), expect.objectContaining({ label: "Design" })]));
    expect(first).not.toEqual(expect.arrayContaining([expect.objectContaining({ label: "design" })]));
    const userIds = (first as Array<{ scheme: string; id: string }>).filter((reference) => reference.scheme === "connectmd-user-skill").map((reference) => reference.id);
    expect(new Set(userIds).size).toBe(userIds.length);
    expect(userIds).not.toContain("your-name");
  });

  it("does not accept required headings hidden in fences or HTML comments", () => {
    const hidden = profileStarter.replace("## About\n\n", "## Overview\n\n```md\n## About\n```\n\n<!--\n## About\n-->\n\n");
    const issues = validateDraft(hidden, "profile");

    expect(hasValidationErrors(issues)).toBe(true);
    expect(issues.map((issue) => issue.message).join(" ")).toContain("Required headings must appear exactly once");
  });

  it("rejects duplicate real H1 headings while ignoring a fenced H1", () => {
    const fenced = profileStarter.replace("## About", "```md\n# Hidden title\n```\n\n## About");
    const duplicate = profileStarter.replace("## About", "# Another title\n\n## About");

    expect(hasValidationErrors(validateDraft(fenced, "profile"))).toBe(false);
    expect(validateDraft(duplicate, "profile").map((issue) => issue.message).join(" ")).toContain("exactly one level-one heading");
  });

  it("rejects headings that do not use the API's exact one-space source syntax", () => {
    const malformed = [
      profileStarter.replace("# Your Name", "#  Your Name"),
      profileStarter.replace("# Your Name", "#\tYour Name"),
      profileStarter.replace("## About", "##  About")
    ];

    for (const markdown of malformed) {
      expect(validateDraft(markdown, "profile").map((issue) => issue.message).join(" ")).toContain("exactly one space after the # markers");
    }
  });
});

describe("draft subject isolation", () => {
  it("waits through initial auth loading and associates the first resolved subject without a reset", () => {
    expect(resolvedDraftSubject(true, false, null)).toBeNull();
    expect(requiresDraftReset(null, resolvedDraftSubject(true, true, "user_a"))).toBe(false);
  });

  it("requires a reset on sign-out or account switch", () => {
    expect(requiresDraftReset("user:user_a", SIGNED_OUT_DRAFT_SUBJECT)).toBe(true);
    expect(requiresDraftReset("user:user_a", "user:user_b")).toBe(true);
    expect(requiresDraftReset("user:user_a", "user:user_a")).toBe(false);
  });

  it("masks an owned draft during post-initial auth loading but not initial loading", () => {
    expect(shouldMaskOwnedDraft(null, true, false, null)).toBe(false);
    expect(shouldMaskOwnedDraft("user:user_a", true, false, null)).toBe(true);
  });

  it("cannot build a B-account request from A-account bytes during an ownership transition", () => {
    const masked = shouldMaskOwnedDraft("user:user_a", true, true, resolvedDraftSubject(true, true, "user_b"));
    const snapshot = maskOwnedDraftSnapshot(masked, { kind: "profile", markdown: "A private canonical draft" });
    const dispatchedBody = snapshot ? JSON.stringify({ markdown: snapshot.markdown }) : null;

    expect(masked).toBe(true);
    expect(snapshot).toBeNull();
    expect(dispatchedBody).toBeNull();
    expect(priorAccountSuccessfulSaveMessage()).not.toContain("A private canonical draft");
  });
});

describe("guarded draft replacement", () => {
  it("confirms edited drafts and rejects stale or unmounted import results", () => {
    expect(shouldConfirmDraftReplacement(profileStarter, "profile", null)).toBe(false);
    expect(shouldConfirmDraftReplacement(profileStarter.replace("Your Name", "Ada"), "profile", null)).toBe(true);
    expect(isImportResultCurrent("profile", 3, "profile", 3, true, false)).toBe(true);
    expect(isImportResultCurrent("profile", 3, "resume", 3, true, false)).toBe(false);
    expect(isImportResultCurrent("profile", 3, "profile", 4, true, false)).toBe(false);
    expect(isImportResultCurrent("profile", 3, "profile", 3, false, true)).toBe(false);
  });
});

describe("canonical document saves", () => {
  const canonicalProfile = profileStarter
    .replace("schema_version: 2\n", "schema_version: 2\nid: 3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f\nowner_id: user_example\nversion: 2\nupdated_at: '2026-08-03T00:00:00Z'\n")
    .replace("visibility: private", "visibility: public");
  const profileResponse: DocumentResponse = {
    id: "3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f",
    kind: "profile",
    identifier: "your-handle",
    visibility: "public",
    version: 2,
    etag: "profile-etag-v2",
    updated_at: "2026-08-03T00:00:00Z",
    markdown: canonicalProfile,
    markdown_url: "/v1/profiles/your-handle.md"
  };

  it("preserves same-kind edits made during save while recording the returned identity for the next PUT", () => {
    const snapshot: SaveSnapshot = { subject: "user_a", kind: "profile", revision: 4, lineage: 2, identifier: "your-handle", markdown: profileStarter, existingIdentity: null };
    const newerMarkdown = profileStarter.replace("Your professional headline", "A newer local headline");
    const result = reconcileSaveResponse(snapshot, { subject: "user_a", kind: "profile", revision: 5, lineage: 2, identifier: "your-handle", markdown: newerMarkdown, existing: null }, profileResponse);

    expect(result.disposition).toBe("preserve");
    expect(result.markdown).toContain("A newer local headline");
    expect(splitFrontmatter(result.markdown).attributes).toMatchObject({
      id: profileResponse.id,
      owner_id: "user_example",
      version: 2,
      updated_at: profileResponse.updated_at
    });
    expect(result.savedDocument).toBe(profileResponse);
    expect(savedDocumentIdentity(result.savedDocument)).toBe(`profile:${profileResponse.id}:your-handle`);
    expect(result.savedDocument?.markdown).toBe(canonicalProfile);
  });

  it("rebases stale vN server fields so an edit-during-update can save successfully as vN+2", async () => {
    const responseV3: DocumentResponse = {
      ...profileResponse,
      version: 3,
      etag: "profile-etag-v3",
      updated_at: "2026-08-03T00:01:00Z",
      markdown: canonicalProfile.replace("\nversion: 2\n", "\nversion: 3\n").replace("2026-08-03T00:00:00Z", "2026-08-03T00:01:00Z")
    };
    const editedV2 = canonicalProfile.replace("Your professional headline", "Newer in-flight edit");
    const snapshot: SaveSnapshot = { subject: "user_a", kind: "profile", revision: 4, lineage: 2, identifier: "your-handle", markdown: canonicalProfile, existingIdentity: savedDocumentIdentity(profileResponse) };
    const reconciled = reconcileSaveResponse(snapshot, { subject: "user_a", kind: "profile", revision: 5, lineage: 2, identifier: "your-handle", markdown: editedV2, existing: profileResponse }, responseV3);
    const responseV4: DocumentResponse = { ...responseV3, version: 4, etag: "profile-etag-v4", updated_at: "2026-08-03T00:02:00Z", markdown: editedV2.replace("\nversion: 2\n", "\nversion: 4\n").replace("2026-08-03T00:00:00Z", "2026-08-03T00:02:00Z") };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(responseV4), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    expect(reconciled.disposition).toBe("preserve");
    const savedV4 = await saveDocument("profile", reconciled.markdown, async () => "token", () => true, reconciled.savedDocument);
    const outbound = JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).markdown as string;
    const outboundAttributes = splitFrontmatter(outbound).attributes;

    expect(fetchMock.mock.calls[0][0]).toBe("/v1/profiles/your-handle");
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("If-Match")).toBe(responseV3.etag);
    expect(outbound).toContain("Newer in-flight edit");
    expect(outboundAttributes).toMatchObject({
      id: profileResponse.id,
      owner_id: "user_example",
      version: 3,
      updated_at: responseV3.updated_at
    });
    expect(savedV4.version).toBe(4);
  });

  it("retains stale canonical concurrency fields and surfaces the API 409", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail: { message: "stale document version" } }), { status: 409, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    let caught: unknown;

    try {
      await saveDocument("profile", canonicalProfile, async () => "token", () => true, profileResponse);
    } catch (error) {
      caught = error;
    }

    const outbound = JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).markdown as string;
    expect(splitFrontmatter(outbound).attributes).toMatchObject({
      id: profileResponse.id,
      owner_id: "user_example",
      version: 2,
      updated_at: profileResponse.updated_at
    });
    expect(presentSaveError(caught)).toContain("save was rejected");
    expect(presentSaveError(caught)).toContain("stale document version");
  });

  it("discards a save response when the active document kind changes", () => {
    const snapshot: SaveSnapshot = { subject: "user_a", kind: "profile", revision: 4, lineage: 2, identifier: "your-handle", markdown: profileStarter, existingIdentity: null };
    const result = reconcileSaveResponse(snapshot, { subject: "user_a", kind: "resume", revision: 5, lineage: 3, identifier: "your-name-resume", markdown: resumeStarter, existing: null }, profileResponse);

    expect(result).toEqual({ disposition: "discard", markdown: resumeStarter, savedDocument: null });
    expect(discardedSuccessfulSaveMessage(profileResponse)).toBe("The original profile your-handle was saved as version 2; the active draft was not replaced.");
    expect(uncertainSaveOutcomeMessage()).toContain("save may have completed");
  });

  it("does not attach an in-flight POST result after a handle change or guarded import", () => {
    const snapshot: SaveSnapshot = { subject: "user_a", kind: "profile", revision: 4, lineage: 2, identifier: "your-handle", markdown: profileStarter, existingIdentity: null };
    const changedHandle = profileStarter.replaceAll("your-handle", "another-handle");
    const handleResult = reconcileSaveResponse(snapshot, { subject: "user_a", kind: "profile", revision: 5, lineage: 2, identifier: "another-handle", markdown: changedHandle, existing: null }, profileResponse);
    const imported = profileStarter.replace("Your professional headline", "Imported profile");
    const importResult = reconcileSaveResponse(snapshot, { subject: "user_a", kind: "profile", revision: 5, lineage: 3, identifier: "your-handle", markdown: imported, existing: null }, profileResponse);

    expect(handleResult).toEqual({ disposition: "discard", markdown: changedHandle, savedDocument: null });
    expect(importResult).toEqual({ disposition: "discard", markdown: imported, savedDocument: null });
  });

  it("creates with POST, then updates a profile by identifier with PUT", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify(profileResponse), { status: 200, headers: { "Content-Type": "application/json", "X-Connectmd-Search": "queued" } }));
    vi.stubGlobal("fetch", fetchMock);
    const randomUUID = vi.fn()
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000001")
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000002");
    vi.stubGlobal("crypto", { randomUUID });

    const created = await saveDocument("profile", profileStarter, async () => "token", () => true, null);
    const updated = await saveDocument("profile", created.markdown, async () => "token", () => true, created);
    const [createUrl, createOptions] = fetchMock.mock.calls[0];
    const [updateUrl, updateOptions] = fetchMock.mock.calls[1];

    expect([createUrl, createOptions?.method]).toEqual(["/v1/profiles", "POST"]);
    expect([updateUrl, updateOptions?.method]).toEqual(["/v1/profiles/your-handle", "PUT"]);
    expect(new Headers(createOptions?.headers).get("Idempotency-Key")).toBe("00000000-0000-4000-8000-000000000001");
    expect(new Headers(createOptions?.headers).get("If-Match")).toBeNull();
    expect(new Headers(updateOptions?.headers).get("Idempotency-Key")).toBe("00000000-0000-4000-8000-000000000002");
    expect(new Headers(updateOptions?.headers).get("If-Match")).toBe(profileResponse.etag);
    expect(randomUUID).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(updateOptions?.body))).toEqual({ markdown: canonicalProfile });
    expect(updated.markdown).toBe(canonicalProfile);
    expect(created.searchIndexing).toBe("queued");
  });

  it("maps search indexing headers without implying readiness when status is absent or invalid", () => {
    expect(searchIndexingStateFromHeader("ready")).toBe("ready");
    expect(searchIndexingStateFromHeader("queued")).toBe("queued");
    expect(searchIndexingStateFromHeader("degraded")).toBe("degraded");
    expect(searchIndexingStateFromHeader(null)).toBe("unknown");
    expect(searchIndexingStateFromHeader("unexpected")).toBe("unknown");
  });

  it("rejects a document response without the required etag", async () => {
    const { etag: _etag, ...missingEtag } = profileResponse;
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(missingEtag), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(saveDocument("profile", profileStarter, async () => "token", () => true, null)).rejects.toMatchObject({ code: "server" });
  });

  it("creates no idempotency key when an account change prevents save dispatch", async () => {
    const randomUUID = vi.fn();
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("crypto", { randomUUID });
    vi.stubGlobal("fetch", fetchMock);
    let current = true;

    await expect(saveDocument("profile", profileStarter, async () => { current = false; return "token"; }, () => current, null)).rejects.toMatchObject({ code: "unauthorized" });
    expect(randomUUID).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a lost save response as uncertain after dispatch", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new Error("connection reset")));
    let caught: unknown;
    try {
      await saveDocument("profile", profileStarter, async () => "token", () => true, null);
    } catch (error) {
      caught = error;
    }

    expect(presentSaveError(caught)).toContain("save may have completed");
    expect(presentSaveError(caught)).toContain("Verify the original document");
    expect(presentSaveError(caught)).not.toContain("No draft was published");
  });

  it("updates a resume by its canonical slug", async () => {
    const resumeResponse: DocumentResponse = { ...profileResponse, kind: "resume", identifier: "your-name-resume", visibility: "private", markdown: resumeStarter, markdown_url: "/v1/resumes/your-name-resume.md" };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(resumeResponse), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await saveDocument("resume", resumeStarter, async () => "token", () => true, resumeResponse);
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/resumes/your-name-resume");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
  });

  it("fetches canonical public resume Markdown by encoded slug", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "https://api.example.test/");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(resumeStarter, { status: 200, headers: { "Content-Type": "text/markdown" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchPublicResumeMarkdown("ari chen-resume")).resolves.toBe(resumeStarter);
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example.test/v1/resumes/ari%20chen-resume.md");
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Accept")).toBe("text/markdown");
  });

  it("loads an owned canonical document with Clerk authorization", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: profileResponse.id, kind: "profile", versions: [] }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(profileResponse), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadDocument("profile", "your-handle", async () => "token", () => true)).resolves.toEqual(profileResponse);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/v1/profiles/your-handle/versions", "/v1/profiles/your-handle"]);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer token");
  });

  it("does not dispatch an owned-document read without a nonempty Clerk bearer", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    for (const token of [null, "", "   "] as const) {
      await expect(loadDocument("profile", "your-handle", async () => token, () => true)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch the canonical document read after its owner subject changes", async () => {
    let current = true;
    const fetchMock = vi.fn<typeof fetch>().mockImplementationOnce(async () => {
      current = false;
      return new Response(JSON.stringify({ id: profileResponse.id, kind: "profile", versions: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadDocument("profile", "your-handle", async () => "token", () => current)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a structured API detail message", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail: { message: "Canonical validation failed." } }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(apiRequest("/v1/profiles")).rejects.toThrow("Canonical validation failed.");
  });
});

describe("agent API keys", () => {
  const listed = { id: "key-1", prefix: "cnd_abcd", scopes: ["documents:read"], revoked: false, created_at: "2026-08-03T00:00:00Z", last_used_at: null };

  it("creates, lists, and revokes scoped keys with Clerk authorization", async () => {
    const created = { id: "key-2", prefix: "cnd_efgh", scopes: ["documents:read"], key: "cnd_secret", created_at: "2026-08-03T00:00:00Z", recovery_required: false as const };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify([listed]), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const getToken = async () => "clerk-token";

    await expect(listApiKeys(getToken, () => true)).resolves.toEqual([listed]);
    await expect(createApiKey(["documents:read"], "api-key-test-create-0001", getToken, () => true)).resolves.toEqual(created);
    await expect(revokeApiKey("key-2", "api-key-test-revoke-0001", getToken, () => true)).resolves.toBeUndefined();
    expect(fetchMock.mock.calls.map(([url, options]) => [url, options?.method ?? "GET", new Headers(options?.headers).get("Authorization"), new Headers(options?.headers).get("Idempotency-Key")])).toEqual([
      ["/v1/api-keys", "GET", "Bearer clerk-token", null],
      ["/v1/api-keys", "POST", "Bearer clerk-token", "api-key-test-create-0001"],
      ["/v1/api-keys/key-2", "DELETE", "Bearer clerk-token", "api-key-test-revoke-0001"]
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ scopes: ["documents:read"] });
  });

  it("parses recovery metadata without fabricating or accepting a secret", async () => {
    const recovery = { id: "key-2", prefix: "cnd_efgh", scopes: ["documents:read"], created_at: "2026-08-03T00:00:00Z", recovery_required: true as const };
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(recovery), { status: 201, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } })));
    await expect(createApiKey(["documents:read"], "api-key-recovery-0001", async () => "clerk-token", () => true)).resolves.toEqual(recovery);

    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ...recovery, key: "cnd_fabricated" }), { status: 201, headers: { "Content-Type": "application/json" } })));
    await expect(createApiKey(["documents:read"], "api-key-recovery-0002", async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });
  });

  it("warns when key creation may have succeeded without returning its one-time secret", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new Error("connection reset")));
    let caught: unknown;
    try {
      await createApiKey(["documents:read"], "api-key-ambiguous-0001", async () => "clerk-token", () => true);
    } catch (error) {
      caught = error;
    }

    expect(presentApiKeyError(caught, "create")).toContain("one-time secret was not received");
    expect(presentApiKeyError(caught, "create")).toContain("revoke any unexpected prefix");
  });

  it("does not dispatch API-key creation after the signed-in subject changes during token retrieval", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    let current = true;
    await expect(createApiKey(["documents:read"], "api-key-subject-0001", async () => { current = false; return "different-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch an API-key inventory read without a current subject and nonempty Clerk bearer", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    let current = true;

    await expect(listApiKeys(async () => { current = false; return "token-for-another-subject"; }, () => current)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    for (const token of [null, "", "   "] as const) {
      await expect(listApiKeys(async () => token, () => true)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
