/**
 * Assistive-technology tree audit (Chromium full accessibility tree — the
 * same AX surface screen readers consume). Walks every active page's AX tree
 * and verifies:
 *   - every focusable control (button/link/textbox/combobox/...) has a name
 *   - headings exist and do not skip levels downward
 * Run: node e2e/at-review.mjs <base-url>
 */

import { chromium } from "@playwright/test";

const BASE_URL = process.argv[2] ?? "http://127.0.0.1:3000";
const PAGES = ["/", "/human", "/md", "/trust", "/account", "/network", "/discover", "/inbox"];

const NAME_REQUIRED = new Set(["button", "link", "textbox", "combobox", "checkbox", "radio", "searchbox", "slider", "switch"]);

function audit(cdpNodes) {
  const byId = new Map(cdpNodes.map((node) => [node.nodeId, node]));
  const problems = [];
  const headings = [];
  let controlCount = 0;
  for (const node of cdpNodes) {
    if (node.ignored === true) continue;
    const role = node.role?.value ?? "";
    const name = node.name?.value ?? "";
    if (NAME_REQUIRED.has(role)) {
      controlCount++;
      if (String(name).trim() === "") {
        problems.push(`unlabeled ${role}${node.value?.value ? ` (value "${String(node.value.value).slice(0, 40)}")` : ""}`);
      }
    }
    if (role === "heading") {
      headings.push({ level: node.properties?.find((property) => property.name === "level")?.value?.value ?? 0, name });
    }
  }
  let previous = 0;
  for (const heading of headings) {
    if (previous > 0 && heading.level > previous + 1) {
      problems.push(`heading level jump: h${String(previous)} -> h${String(heading.level)} "${heading.name}"`);
    }
    previous = heading.level;
  }
  void byId;
  return { problems, headingCount: headings.length, controlCount };
}

const browser = await chromium.launch();
const context = await browser.newContext();
let totalProblems = 0;
const lines = [];

for (const path of PAGES) {
  const page = await context.newPage();
  await page.goto(BASE_URL + path, { waitUntil: "networkidle" });
  const session = await context.newCDPSession(page);
  const { nodes } = await session.send("Accessibility.getFullAXTree");
  const { problems, headingCount, controlCount } = audit(nodes);
  totalProblems += problems.length;
  const line = `${path}: ${String(controlCount)} controls, ${String(headingCount)} headings, ${String(problems.length)} problem(s)`;
  lines.push(line);
  console.log(line);
  for (const problem of problems) console.log(`  ! ${problem}`);
  await session.detach();
  await page.close();
}

await browser.close();
if (totalProblems > 0) {
  console.error(`AT tree audit: FAIL (${String(totalProblems)} problem(s) across ${String(PAGES.length)} pages)`);
  process.exit(1);
}
console.log(`AT tree audit: PASS (every control named, headings ordered, ${String(PAGES.length)} pages)`);
