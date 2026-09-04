import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const publicCopySources = [
  "app/layout.tsx",
  "app/page.tsx",
  "app/trust/page.tsx",
  "components/agent-handoff.tsx",
  "components/human-builder.tsx",
  "components/markdown-editor.tsx",
  "components/mode-switch.tsx",
  "components/publish-panel.tsx",
  "components/site-header.tsx",
  "public/agent-readme.md",
  "public/llms.txt",
] as const;

describe("public copy style", () => {
  it("keeps typographic dashes out of the live standalone surface", () => {
    for (const relativePath of publicCopySources) {
      const source = readFileSync(resolve(process.cwd(), relativePath), "utf8");
      expect(source, relativePath).not.toMatch(/[—–]/u);
    }
  });
});
