import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

type Fixture = {
  profileMarkdown: string;
  resumeMarkdown: string;
  postMarkdown: string;
  protocolManifest: {
    version: number;
    base_url: string;
    environment: string;
    recruiting_enabled: boolean;
    account_lifecycle_enabled: boolean;
    evidence_boundary: string;
    responses: Record<
      string,
      {
        status: number;
        headers: Record<string, string>;
        sha256: string;
        body_base64: string;
      }
    >;
  };
  profileDocument: {
    owner_id: string;
    etag: string;
    markdown_url: string;
  };
  resumeDocument: {
    owner_id: string;
    etag: string;
    identifier: string;
    markdown_url: string;
  };
  post: {
    id: string;
    author_profile_handle: string;
    version: number;
    published_at: string;
    updated_at: string;
    markdown: string;
    markdown_url: string;
    etag: string;
  };
};

type TextProbe = {
  status: number;
  contentType: string;
  sha256: string;
  byteLength: number;
  body: string;
  headers: Record<string, string>;
};

const BROWSER_CREDENTIAL_HEADER_NAMES = [
  "authorization",
  "cookie",
  "proxy-authorization",
] as const;
type BrowserCredentialHeaderKind = (typeof BROWSER_CREDENTIAL_HEADER_NAMES)[number];

type TrafficAudit = {
  externalOrigins: Set<string>;
  mutatingMethods: string[];
  credentialHeaderViolations: Array<{ kind: BrowserCredentialHeaderKind; origin: string }>;
  markdownAccepts: string[];
  webSocketOrigins: string[];
};

type AxeViolation = {
  id: string;
  impact: string | null;
  nodes: number;
};

type AxeWindow = Window & {
  axe?: {
    run: (
      context: Document,
      options: { runOnly: { type: "tag"; values: string[] } },
    ) => Promise<{
      violations: Array<{
        id: string;
        impact: string | null;
        nodes: unknown[];
      }>;
    }>;
  };
};

const fixture = JSON.parse(
  readFileSync(resolve(process.cwd(), "e2e", "public-fixtures.json"), "utf8"),
) as Fixture;
const require = createRequire(resolve(process.cwd(), "package.json"));
const axeScriptPath = require.resolve("axe-core/axe.min.js");
const e2eBaseUrl = process.env.E2E_BASE_URL;
if (!e2eBaseUrl) throw new Error("E2E_BASE_URL is required");
const parsedBaseUrl = new URL(e2eBaseUrl);
if (
  !["http:", "https:"].includes(parsedBaseUrl.protocol) ||
  parsedBaseUrl.username ||
  parsedBaseUrl.password ||
  parsedBaseUrl.pathname !== "/" ||
  parsedBaseUrl.search ||
  parsedBaseUrl.hash
) {
  throw new Error("E2E_BASE_URL must be an absolute HTTP(S) origin");
}
const expectedOrigin = parsedBaseUrl.origin;
const fixtureEvidenceBoundary = fixture.protocolManifest.evidence_boundary;
const publicFixtureOwnerId = "00000000-0000-4000-8000-000000000001";
const requiredOpenApiOperations: Record<string, readonly string[]> = {
  "/v1/profiles/{handle}.md": ["get"],
  "/v1/resumes/{slug}.md": ["get"],
  "/v1/search": ["get"],
  "/v1/search/query": ["post"],
  "/v1/taxonomies": ["get"],
  "/v1/taxonomies/{taxonomy}": ["get"],
};
const mobilePublicRoutes = [
  "/",
  "/trust",
  "/agent-directory",
  "/representatives",
  "/r/ada-lovelace-resume",
  "/posts/fixture-post-field-notes",
  "/search",
] as const;
const narrowReflowViewport = { width: 320, height: 800 } as const;
const taxonomyAliasA = `tx1_${"a".repeat(64)}`;
const taxonomyAliasB = `tx1_${"b".repeat(64)}`;

function exactE2eUrl(resourcePath: string): string {
  const url = new URL(resourcePath, expectedOrigin);
  if (url.origin !== expectedOrigin) {
    throw new Error("browser release gate attempted a non-E2E origin");
  }
  return url.href;
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function contentDigestForSha256(value: string): string {
  return `sha-256=:${Buffer.from(value, "hex").toString("base64")}:`;
}

function browserCredentialHeaderKind(name: string): BrowserCredentialHeaderKind | null {
  const normalized = name.toLowerCase();
  return BROWSER_CREDENTIAL_HEADER_NAMES.find((candidate) => candidate === normalized) ?? null;
}

function webSocketAuditOrigin(rawUrl: string): string {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "ws:" || url.protocol === "wss:" ? url.origin : "invalid";
  } catch {
    return "invalid";
  }
}

async function auditPage(page: Page): Promise<TrafficAudit> {
  const audit: TrafficAudit = {
    externalOrigins: new Set<string>(),
    mutatingMethods: [],
    credentialHeaderViolations: [],
    markdownAccepts: [],
    webSocketOrigins: [],
  };
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== expectedOrigin) {
      audit.externalOrigins.add(url.origin);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  await page.routeWebSocket("**/*", async (socket) => {
    audit.webSocketOrigins.push(webSocketAuditOrigin(socket.url()));
    await socket.close({ code: 1008, reason: "browser-release-websocket-blocked" });
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== expectedOrigin) {
      audit.externalOrigins.add(url.origin);
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
      audit.mutatingMethods.push(request.method());
    }
    const headers = request.headers();
    for (const headerName of Object.keys(headers)) {
      const kind = browserCredentialHeaderKind(headerName);
      if (kind) audit.credentialHeaderViolations.push({ kind, origin: url.origin });
    }
    if (url.pathname.endsWith(".md")) {
      audit.markdownAccepts.push(headers.accept ?? "");
    }
  });
  return audit;
}

async function probeText(
  page: Page,
  resourcePath: string,
  accept: string,
): Promise<TextProbe> {
  return page.evaluate(async ({ resourcePath: path, accept: requestedAccept, expectedOrigin: origin }) => {
    const target = new URL(path, origin);
    if (target.origin !== origin) {
      throw new Error("browser release gate attempted a non-E2E origin");
    }
    const response = await fetch(target.href, { headers: { Accept: requestedAccept } });
    const bytes = await response.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hash = Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    const body = new TextDecoder().decode(bytes);
    const headers: Record<string, string> = {};
    for (const [name, value] of response.headers.entries()) {
      if (!["content-length", "connection", "date", "keep-alive", "transfer-encoding"].includes(name)) {
        headers[name] = value;
      }
    }
    return {
      status: response.status,
      contentType: response.headers.get("content-type") ?? "",
      sha256: hash,
      byteLength: bytes.byteLength,
      body,
      headers,
    };
  }, { resourcePath: exactE2eUrl(resourcePath), accept, expectedOrigin });
}

function expectedProtocolResponse(resourcePath: string) {
  const entry = fixture.protocolManifest.responses[resourcePath];
  if (!entry) throw new Error(`missing protocol fixture route: ${resourcePath}`);
  return entry;
}

function base64ByteLength(value: string): number {
  return Buffer.from(value, "base64").byteLength;
}

async function assertProtocolResponse(
  page: Page,
  resourcePath: string,
  accept: string,
): Promise<TextProbe> {
  const probe = await probeText(page, resourcePath, accept);
  const expected = expectedProtocolResponse(resourcePath);
  expect(probe.status, resourcePath).toBe(expected.status);
  expect(probe.sha256, resourcePath).toBe(expected.sha256);
  expect(probe.byteLength, resourcePath).toBe(base64ByteLength(expected.body_base64));
  expect(probe.headers, resourcePath).toEqual(expected.headers);
  expect(probe.contentType, resourcePath).toBe(expected.headers["content-type"]);
  return probe;
}

async function assertNoExternalWritesOrCredentials(audit: TrafficAudit): Promise<void> {
  expect([...audit.externalOrigins]).toEqual([]);
  expect(audit.mutatingMethods).toEqual([]);
  expect(audit.credentialHeaderViolations).toEqual([]);
  expect(audit.webSocketOrigins).toEqual([]);
}

async function assertNarrowFirstUseLayout(page: Page, path: string, requiredSelectors: readonly string[], viewportWidth: number): Promise<void> {
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle");
  const layout = await page.evaluate((selectors) => {
    const viewportWidth = document.documentElement.clientWidth;
    const elements = Array.from(document.querySelectorAll<HTMLElement>("a,button,input,select,textarea,[tabindex]:not([tabindex='-1'])"));
    const offenders = elements
      .filter((element) => {
        const style = getComputedStyle(element);
        const zIndex = Number.parseInt(style.zIndex, 10);
        const isTransparentNegativeLayer = zIndex < 0 && style.color === "rgba(0, 0, 0, 0)" && style.backgroundColor === "rgba(0, 0, 0, 0)";
        if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || Number.parseFloat(style.opacity) === 0 || isTransparentNegativeLayer) return false;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        return rect.width > 0 && (rect.left < -1 || rect.right > viewportWidth + 1);
      })
      .map((element) => {
        const descriptor = element.id
          ? `#${element.id}`
          : element.getAttribute("aria-label")
            ? `[aria-label=\"${element.getAttribute("aria-label")}\"]`
            : element.getAttribute("href")
              ? `[href=\"${element.getAttribute("href")}\"]`
              : "";
        return `${element.tagName.toLowerCase()}${descriptor}`;
      });
    const required = selectors.map((selector) => {
      const element = document.querySelector<HTMLElement>(selector);
      if (!element) return { selector, present: false, visible: false, inBounds: false };
      const rect = element.getBoundingClientRect();
      return {
        selector,
        present: true,
        visible: rect.width > 0 && rect.height > 0,
        inBounds: rect.left >= -1 && rect.right <= viewportWidth + 1,
      };
    });
    return {
      viewportWidth,
      scrollWidth: document.documentElement.scrollWidth,
      offenders,
      required,
    };
  }, requiredSelectors);
  expect(layout.viewportWidth, `${path} viewport`).toBe(viewportWidth);
  expect(layout.scrollWidth, `${path} document overflow`).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.offenders, `${path} interactive overflow`).toEqual([]);
  for (const required of layout.required) {
    expect(required.present, `${path} missing ${required.selector}`).toBe(true);
    expect(required.visible, `${path} hidden ${required.selector}`).toBe(true);
    expect(required.inBounds, `${path} clipped ${required.selector}`).toBe(true);
  }
}

function assertBoundedOpenApiFixture(openapi: TextProbe): void {
  const parsed = JSON.parse(openapi.body) as {
    paths?: Record<string, Record<string, unknown>>;
  };
  const paths = parsed.paths ?? {};
  expect(parsed.paths).toBeDefined();
  expect(Object.keys(paths)).not.toHaveLength(0);
  for (const [path, methods] of Object.entries(requiredOpenApiOperations)) {
    expect(Object.prototype.hasOwnProperty.call(paths, path), path).toBe(true);
    for (const method of methods) {
      expect(Object.prototype.hasOwnProperty.call(paths[path] ?? {}, method), `${path} ${method}`).toBe(true);
    }
  }
  expect(Object.prototype.hasOwnProperty.call(paths, "/mcp")).toBe(false);
  expect(Object.prototype.hasOwnProperty.call(paths, "/a2a/message:send")).toBe(false);
}

async function assertA11y(page: Page): Promise<void> {
  await page.addScriptTag({ path: axeScriptPath });
  const violations = await page.evaluate(async () => {
    const axeWindow = window as AxeWindow;
    if (!axeWindow.axe) throw new Error("axe not loaded");
    const result = await axeWindow.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    });
    return result.violations.map((violation): AxeViolation => ({
        id: violation.id,
        impact: violation.impact,
        nodes: violation.nodes.length,
      }));
  });
  expect(violations).toEqual([]);
}

async function assertSequentialHeadingLevels(page: Page, path: string): Promise<void> {
  const headings = await page.locator("h1,h2,h3,h4,h5,h6").evaluateAll((elements) => elements.map((element) => ({
    level: Number(element.tagName.slice(1)),
    text: element.textContent?.trim().replace(/\s+/gu, " ") ?? "",
  })));
  expect(headings.length, `${path} heading count`).toBeGreaterThan(0);
  expect(headings[0]?.level, `${path} first heading`).toBe(1);
  expect(headings.slice(1).filter((heading) => heading.level === 1), `${path} additional h1 headings`).toEqual([]);
  const jumps = headings.slice(1).flatMap((heading, index) => heading.level > headings[index].level + 1
    ? [`h${headings[index].level} ${headings[index].text} -> h${heading.level} ${heading.text}`]
    : []);
  expect(jumps, `${path} skipped heading levels`).toEqual([]);
}

async function assertExplicitMinimumTouchTargets(page: Page): Promise<void> {
  const targets = page.locator("a.min-h-11, button.min-h-11, input.min-h-11, select.min-h-11, textarea.min-h-11, summary");
  for (let index = 0; index < await targets.count(); index += 1) {
    const target = targets.nth(index);
    if (!(await target.isVisible())) continue;
    const box = await target.boundingBox();
    expect(box, `touch target ${index} must have a rendered box`).not.toBeNull();
    expect(box!.width, `touch target ${index} width`).toBeGreaterThanOrEqual(44);
    expect(box!.height, `touch target ${index} height`).toBeGreaterThanOrEqual(44);
  }
}

async function assertSearchFacetTouchTargets(page: Page): Promise<void> {
  const targets = page.locator('[data-touch-target="search-facet"]');
  const count = await targets.count();
  expect(count, "search fixture must render at least one standalone facet target").toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const target = targets.nth(index);
    await expect(target, `search facet ${index} should be visible`).toBeVisible();
    const box = await target.boundingBox();
    expect(box, `search facet ${index} must have a rendered box`).not.toBeNull();
    expect(box!.width, `search facet ${index} width`).toBeGreaterThanOrEqual(44);
    expect(box!.height, `search facet ${index} height`).toBeGreaterThanOrEqual(44);
  }
}

async function assertSearchResultPrimaryTouchTargets(page: Page): Promise<void> {
  const targets = page.locator('[data-touch-target="search-result-primary"]');
  const count = await targets.count();
  expect(count, "search fixture must render at least one primary result target").toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const target = targets.nth(index);
    await expect(target, `search result primary ${index} should be visible`).toBeVisible();
    const box = await target.boundingBox();
    expect(box, `search result primary ${index} must have a rendered box`).not.toBeNull();
    expect(box!.width, `search result primary ${index} width`).toBeGreaterThanOrEqual(44);
    expect(box!.height, `search result primary ${index} height`).toBeGreaterThanOrEqual(44);
  }
}

async function minimumTouchTargetBox(target: Locator, label: string) {
  await expect(target, `${label} should be visible`).toBeVisible();
  const box = await target.boundingBox();
  expect(box, `${label} should have a rendered box`).not.toBeNull();
  expect(box!.width, `${label} width`).toBeGreaterThanOrEqual(44);
  expect(box!.height, `${label} height`).toBeGreaterThanOrEqual(44);
  return box!;
}

function boxesOverlap(first: { x: number; y: number; width: number; height: number }, second: { x: number; y: number; width: number; height: number }): boolean {
  return first.x < second.x + second.width && first.x + first.width > second.x && first.y < second.y + second.height && first.y + first.height > second.y;
}

async function assertVisibleKeyboardFocus(target: Locator): Promise<void> {
  await target.focus();
  await expect(target).toBeFocused();
  const outline = await target.evaluate((element) => {
    const style = getComputedStyle(element);
    return { color: style.outlineColor, style: style.outlineStyle, width: Number.parseFloat(style.outlineWidth) };
  });
  expect(outline.style).not.toBe("none");
  expect(outline.color).not.toBe("rgba(0, 0, 0, 0)");
  expect(outline.width).toBeGreaterThanOrEqual(2);
}

async function assertFocusedHeadingClearsStickyHeader(page: Page, target: Locator): Promise<void> {
  const geometry = await target.evaluate((element) => {
    const header = document.querySelector("header");
    const headingBox = element.getBoundingClientRect();
    const headerBox = header?.getBoundingClientRect();
    return {
      headerBottom: headerBox?.bottom ?? 0,
      headingBottom: headingBox.bottom,
      headingTop: headingBox.top,
      viewportHeight: window.innerHeight,
    };
  });
  expect(geometry.headingTop).toBeGreaterThanOrEqual(geometry.headerBottom + 8);
  expect(geometry.headingBottom).toBeLessThanOrEqual(geometry.viewportHeight);
  await expect(target).toBeFocused();
}

test("anonymous landing and discovery expose safe current paths", async ({ page }) => {
  const audit = await auditPage(page);
  expect(fixtureEvidenceBoundary).toContain("hermetic current-source fixture parity");
  expect(fixtureEvidenceBoundary).toContain("not live");

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Choose what you want done." })).toBeVisible();
  await expect(page.locator('[aria-labelledby="agent-handoff-instruction-label"]')).toContainText(
    "/agent-readme.md",
  );
  await expect(page.locator('[aria-labelledby="agent-handoff-instruction-label"]')).toContainText(
    "Do not publish",
  );
  await expect(page.locator('[aria-labelledby="agent-handoff-instruction-label"]')).toContainText(
    "contact anyone",
  );
  await expect(
    page.getByRole("link", { name: "Agent README", exact: true }),
  ).toBeVisible();
  await assertExplicitMinimumTouchTargets(page);

  await assertProtocolResponse(page, "/agent-readme.md", "text/markdown");

  await page.goto("/discover", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "One public network, projected for browsers and agents." }),
  ).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Unavailable now" })).toHaveCount(0);
  for (const path of ["/llms.txt", "/llms-full.txt", "/openapi.json", "/.well-known/agent-card.json"]) {
    await expect(page.locator(`a[href="${path}"]`)).toBeVisible();
  }
  await assertExplicitMinimumTouchTargets(page);

  await assertProtocolResponse(page, "/llms.txt", "text/plain");
  await assertProtocolResponse(page, "/llms-full.txt", "text/plain");
  const openapi = await assertProtocolResponse(page, "/openapi.json", "application/json");
  assertBoundedOpenApiFixture(openapi);
  const agentCard = await assertProtocolResponse(
    page,
    "/.well-known/agent-card.json",
    "application/json",
  );
  const card = JSON.parse(agentCard.body) as {
    skills?: Array<{ id?: unknown }>;
  };
  expect(card.skills).toHaveLength(7);
  expect(new Set(card.skills?.map((skill) => skill.id))).toEqual(
    new Set([
      "search-public-documents",
      "discover-public-taxonomies",
      "discover-public-agents",
      "list-profile-agents",
      "request-mediated-contact",
      "send-mandate-bound-agent-outreach",
      "get-mandate-bound-agent-outreach-status",
    ]),
  );
  expect(JSON.stringify(card).toLowerCase()).not.toContain("tools/list");
  expect(JSON.stringify(card).toLowerCase()).not.toContain('"mcp"');

  await assertNoExternalWritesOrCredentials(audit);
});

test("public crawler contracts expose only bounded canonical sitemap URLs", async ({ page }) => {
  const audit = await auditPage(page);
  const sitemapPaths = ["/sitemap/0.xml", "/sitemap/1.xml", "/sitemap/2.xml", "/sitemap/3.xml"] as const;
  const canonicalSiteOrigin = "https://connect.md";
  const expectedCategoryPaths: Record<(typeof sitemapPaths)[number], string[]> = {
    "/sitemap/0.xml": [
      "/",
      "/search",
      "/discover",
      "/trust",
      "/representatives",
      "/agent-directory",
      "/p/ada-lovelace",
      "/r/ada-lovelace-resume",
    ],
    "/sitemap/1.xml": [],
    "/sitemap/2.xml": [],
    "/sitemap/3.xml": ["/posts/fixture-post-field-notes"],
  };
  const privatePrefixes = [
    "/account",
    "/human",
    "/md",
    "/feed",
    "/moderation",
    "/moderation-review",
    "/appeal-review",
    "/inbox",
    "/applications",
    "/employer",
    "/verification-review",
    "/network",
    "/messages/",
    "/organizations",
    "/jobs",
  ];

  await page.goto("/", { waitUntil: "domcontentloaded" });
  const robots = await probeText(page, "/robots.txt", "text/plain");
  expect(robots.status).toBe(200);
  expect(robots.contentType).toContain("text/plain");
  expect(robots.body).toContain("User-Agent: *");
  for (const sitemapPath of sitemapPaths) {
    expect(robots.body).toContain(`Sitemap: ${canonicalSiteOrigin}${sitemapPath}`);
  }
  for (const privatePrefix of privatePrefixes) {
    expect(robots.body).toContain(`Disallow: ${privatePrefix}`);
  }

  for (const sitemapPath of sitemapPaths) {
    const sitemap = await probeText(page, sitemapPath, "application/xml");
    expect(sitemap.status).toBe(200);
    expect(sitemap.contentType).toContain("application/xml");
    expect(sitemap.body).toContain("<urlset");
    const urls = Array.from(sitemap.body.matchAll(/<loc>([^<]+)<\/loc>/gu), (match) => match[1]);
    expect(urls).toHaveLength(new Set(urls).size);
    expect(urls.length).toBeLessThanOrEqual(50_000);
    const paths = urls.map((value) => {
      const url = new URL(value);
      expect(url.origin).toBe(canonicalSiteOrigin);
      return url.pathname;
    });
    expect(paths).toEqual(expectedCategoryPaths[sitemapPath]);
    for (const pathname of paths) {
      expect(privatePrefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))).toBe(false);
    }
  }

  const allSitemapPaths = Object.values(expectedCategoryPaths).flat();
  expect(allSitemapPaths).not.toContain("/organizations/private-lab");
  expect(allSitemapPaths).not.toContain("/jobs/lumen-labs/unpublished-role");
  await assertNoExternalWritesOrCredentials(audit);
});

test("default-off public recruiting routes are hidden before release enablement", async ({ page }) => {
  const audit = await auditPage(page);
  const gatedPaths = [
    "/organizations",
    "/organizations/lumen-labs",
    "/jobs",
    "/jobs/lumen-labs/systems-researcher",
  ] as const;

  for (const path of gatedPaths) {
    const response = await page.goto(path, { waitUntil: "domcontentloaded" });
    expect(response).not.toBeNull();
    expect(response!.status(), path).toBe(404);
    expect(response!.headers()["x-robots-tag"], path).toContain("noindex");
    expect(response!.headers()["content-type"], path).toContain("text/html");
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, nofollow");
  }

  await page.goto("/discover", { waitUntil: "domcontentloaded" });
  await expect(page.locator('a[href^="/organizations"]')).toHaveCount(0);
  await expect(page.locator('a[href^="/jobs"]')).toHaveCount(0);
  await expect(page.getByText("Public organization records", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Opportunities from the service gate", { exact: true })).toHaveCount(0);

  const disabledRecruiting = await page.evaluate(async () => {
    const [organizations, jobs] = await Promise.all([
      fetch("/v1/organizations").then(async (response) => [response.status, await response.json()] as const),
      fetch("/v1/jobs").then(async (response) => [response.status, await response.json()] as const),
    ]);
    return { organizations, jobs };
  });
  expect(disabledRecruiting).toEqual({
    organizations: [200, { organizations: [], next_cursor: null }],
    jobs: [200, { jobs: [], next_cursor: null }],
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  const primaryNavigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
  await expect(primaryNavigation.getByRole("link", { name: "Agent directory", exact: true })).toHaveAttribute("href", "/agent-directory");
  await expect(primaryNavigation.locator('a[href="/network"]')).toHaveCount(0);
  await expect(primaryNavigation.locator('a[href="/agents"]')).toHaveCount(0);
  await expect(page.locator('a[href="/employer"]')).toHaveCount(0);
  await expect(page.getByText("Recruiting is not available in this release", { exact: true })).toBeVisible();
  await expect(page.getByText(/Private employer preparation appears only in deployments with authenticated workspaces/u)).toBeVisible();
  await page.getByRole("link", { name: /Explore published agents/u }).click();
  await expect(page).toHaveURL(/\/agent-directory$/u);
  await expect(page.getByRole("heading", { name: "Find a published agent identity, not a claim of authority.", exact: true })).toBeVisible();

  await page.goto("/trust", { waitUntil: "domcontentloaded" });
  await expect(page.locator('a[href^="/organizations"]')).toHaveCount(0);
  await expect(page.locator('a[href^="/jobs"]')).toHaveCount(0);
  await expect(page.locator('a[href="/employer"]')).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Recruiting is not available in this release", exact: true })).toBeVisible();
  await assertNoExternalWritesOrCredentials(audit);
});

test("profile HTML and canonical Markdown remain byte-parity linked", async ({ page }) => {
  const audit = await auditPage(page);

  expect(fixture.profileDocument.owner_id).toBe(publicFixtureOwnerId);
  expect(fixture.resumeDocument.owner_id).toBe(publicFixtureOwnerId);
  expect(fixture.profileDocument.etag).toBe(`"sha256-${sha256(fixture.profileMarkdown)}"`);
  expect(fixture.resumeDocument.etag).toBe(`"sha256-${sha256(fixture.resumeMarkdown)}"`);
  expect(fixture.post.markdown).toBe(fixture.postMarkdown);
  expect(fixture.post.etag).toBe(`"sha256-${sha256(fixture.postMarkdown)}"`);

  await page.goto("/p/ada-lovelace", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Ada Lovelace", level: 1 })).toBeVisible();
  await expect(page.getByText("Backend engineer", { exact: true })).toBeVisible();

  const markdownLink = page.locator('a[type="text/markdown"]');
  await expect(markdownLink).toHaveCount(1);
  const href = await markdownLink.getAttribute("href");
  expect(href).not.toBeNull();
  const markdownUrl = new URL(href!, page.url());
  expect(markdownUrl.origin).toBe(new URL(page.url()).origin);
  const markdownPath = markdownUrl.pathname;
  expect(markdownPath).toBe(fixture.profileDocument.markdown_url);

  const markdown = await probeText(page, markdownPath, "text/markdown");
  expect(markdown.status).toBe(200);
  expect(markdown.contentType).toContain("text/markdown");
  expect(markdown.sha256).toBe(sha256(fixture.profileMarkdown));
  expect(markdown.byteLength).toBe(Buffer.byteLength(fixture.profileMarkdown, "utf8"));
  expect(markdown.headers.etag).toBe(`"sha256-${markdown.sha256}"`);
  expect(markdown.headers["content-digest"]).toBe(contentDigestForSha256(markdown.sha256));
  expect(audit.markdownAccepts.some((value) => value.includes("text/markdown"))).toBe(true);

  await page.goto("/r/ada-lovelace-resume", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Ada Lovelace Resume", level: 1 })).toBeVisible();
  const resumeMarkdownLink = page.locator('a[type="text/markdown"]');
  await expect(resumeMarkdownLink).toHaveCount(1);
  const resumeHref = await resumeMarkdownLink.getAttribute("href");
  expect(resumeHref).not.toBeNull();
  const resumeMarkdownUrl = new URL(resumeHref!, page.url());
  expect(resumeMarkdownUrl.origin).toBe(new URL(page.url()).origin);
  const resumeMarkdownPath = resumeMarkdownUrl.pathname;
  expect(resumeMarkdownPath).toBe(fixture.resumeDocument.markdown_url);
  const resumeMarkdown = await probeText(page, resumeMarkdownPath, "text/markdown");
  expect(resumeMarkdown.status).toBe(200);
  expect(resumeMarkdown.contentType).toContain("text/markdown");
  expect(resumeMarkdown.sha256).toBe(sha256(fixture.resumeMarkdown));
  expect(resumeMarkdown.byteLength).toBe(Buffer.byteLength(fixture.resumeMarkdown, "utf8"));
  expect(resumeMarkdown.headers.etag).toBe(`"sha256-${resumeMarkdown.sha256}"`);
  expect(resumeMarkdown.headers["content-digest"]).toBe(contentDigestForSha256(resumeMarkdown.sha256));

  await page.goto(`/posts/${fixture.post.id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Field notes on canonical Markdown", level: 1 })).toBeVisible();
  const archiveLink = page.getByRole("link", { name: `@${fixture.post.author_profile_handle}'s archive`, exact: true });
  await expect(archiveLink).toHaveAttribute("href", `/p/${fixture.post.author_profile_handle}/posts`);
  const authorLink = page.getByRole("link", { name: `@${fixture.post.author_profile_handle}`, exact: true });
  await expect(authorLink).toHaveAttribute("href", `/p/${fixture.post.author_profile_handle}`);
  await expect(page.locator('a[href^="/v1/"]:not([type="text/markdown"])')).toHaveCount(0);
  const postMarkdownLink = page.locator('a[type="text/markdown"]');
  await expect(postMarkdownLink).toHaveCount(1);
  await expect(postMarkdownLink).toHaveAttribute("type", "text/markdown");
  for (const [label, link] of [["archive", archiveLink], ["author", authorLink], ["canonical Markdown", postMarkdownLink]] as const) {
    const box = await link.boundingBox();
    expect(box, `${label} link should have a measurable box`).not.toBeNull();
    expect(box!.height, `${label} link should be at least 44px high`).toBeGreaterThanOrEqual(44);
  }
  const postHref = await postMarkdownLink.getAttribute("href");
  expect(postHref).not.toBeNull();
  const postMarkdownUrl = new URL(postHref!, page.url());
  expect(postMarkdownUrl.origin).toBe(new URL(page.url()).origin);
  const postMarkdownPath = postMarkdownUrl.pathname;
  expect(postMarkdownPath).toBe(fixture.post.markdown_url);
  const postMarkdown = await probeText(page, postMarkdownPath, "text/markdown");
  expect(postMarkdown.status).toBe(200);
  expect(postMarkdown.contentType).toContain("text/markdown");
  expect(postMarkdown.sha256).toBe(sha256(fixture.postMarkdown));
  expect(postMarkdown.byteLength).toBe(Buffer.byteLength(fixture.postMarkdown, "utf8"));
  expect(postMarkdown.headers.etag).toBe(`"sha256-${postMarkdown.sha256}"`);
  expect(postMarkdown.headers["content-digest"]).toBe(contentDigestForSha256(postMarkdown.sha256));

  await page.goto("/search", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "1 indexed result", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ada Lovelace", exact: true })).toBeVisible();
  const viewProfile = page.getByRole("link", { name: "View profile", exact: true });
  await expect(viewProfile).toBeVisible();
  await assertVisibleKeyboardFocus(viewProfile);
  await assertSearchFacetTouchTargets(page);
  await assertSearchResultPrimaryTouchTargets(page);
  await assertExplicitMinimumTouchTargets(page);

  await page.setViewportSize(narrowReflowViewport);
  await page.emulateMedia({ reducedMotion: "reduce" });

  await assertNarrowFirstUseLayout(page, "/trust", ['nav[aria-label="Public platform contracts"]'], narrowReflowViewport.width);
  const contractNavigation = page.getByRole("navigation", { name: "Public platform contracts", exact: true });
  for (const label of ["Agent onboarding README", "llms.txt", "Complete agent guide", "OpenAPI"] as const) {
    await minimumTouchTargetBox(contractNavigation.getByRole("link", { name: label, exact: true }), `Trust contract ${label}`);
  }

  await assertNarrowFirstUseLayout(page, "/p/ada-lovelace", ['a[href="/search"]', 'a[type="text/markdown"]'], narrowReflowViewport.width);
  await minimumTouchTargetBox(page.getByRole("link", { name: "Search people", exact: true }), "Profile Search people link");
  await minimumTouchTargetBox(page.getByRole("link", { name: "View canonical Markdown", exact: true }), "Profile canonical Markdown link");

  await assertNarrowFirstUseLayout(page, "/representatives", ['h3 a[href="/p/ada-lovelace"]'], narrowReflowViewport.width);
  await expect(page.getByRole("heading", { name: "1 profile", exact: true })).toBeVisible();
  await expect(page.locator('a[href="/agents"]')).toHaveCount(0);
  await expect(page.getByText(/Private agent permissions and reviewable changes appear only in deployments with authenticated workspaces/u)).toBeVisible();
  await minimumTouchTargetBox(page.getByRole("link", { name: "Ada Lovelace", exact: true }), "Representative profile-name link");

  const typedSearch = new URLSearchParams([
    ["occupation_ids", taxonomyAliasA],
    ["occupation_ids", taxonomyAliasB],
  ]);
  const typedSearchPath = `/search?${typedSearch.toString()}`;
  await assertNarrowFirstUseLayout(
    page,
    typedSearchPath,
    [`button[aria-label="Remove ${taxonomyAliasA}"]`, `button[aria-label="Remove ${taxonomyAliasB}"]`],
    narrowReflowViewport.width,
  );
  const firstRemoveBox = await minimumTouchTargetBox(page.getByRole("button", { name: `Remove ${taxonomyAliasA}`, exact: true }), "First taxonomy removal");
  const secondRemoveBox = await minimumTouchTargetBox(page.getByRole("button", { name: `Remove ${taxonomyAliasB}`, exact: true }), "Second taxonomy removal");
  expect(boxesOverlap(firstRemoveBox, secondRemoveBox), "taxonomy removal targets must not overlap").toBe(false);

  await page.goto("/search?q=fixture-empty", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "No matching public documents", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "1 indexed result", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Ada Lovelace", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "View profile", exact: true })).toHaveCount(0);

  await page.goto("/search?q=fixture-unavailable", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Directory temporarily unavailable", exact: true })).toBeVisible();
  await expect(page.getByText("Public records are temporarily unavailable. Try again shortly.", { exact: true })).toBeVisible();
  await expect(page.getByText("fixture search unavailable", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/API origin/iu)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "1 indexed result", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No matching public documents", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Ada Lovelace", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "View profile", exact: true })).toHaveCount(0);

  await page.goto("/agents/fixture-unavailable", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "This view is temporarily unavailable.", exact: true })).toBeVisible();
  await expect(page.getByText("Try again shortly. If the problem continues, return home and reopen the page.", { exact: true })).toBeVisible();
  await expect(page.getByText("fixture private agent service unavailable", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/\bAPI\b|server-side configuration|No document was changed/iu)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Try again", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Home", exact: true })).toHaveAttribute("href", "/");

  const missingPageResponse = await page.goto("/definitely-not-a-connectmd-route", { waitUntil: "domcontentloaded" });
  expect(missingPageResponse?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "This page is not available.", exact: true })).toBeVisible();
  await expect(page.getByText("Private and unpublished records are never exposed through this page.", { exact: true })).toBeVisible();
  await minimumTouchTargetBox(page.getByRole("link", { name: "Explore public records", exact: true }), "Not-found Discover link");
  await minimumTouchTargetBox(page.getByRole("link", { name: "Home", exact: true }), "Not-found Home link");
  await assertNoExternalWritesOrCredentials(audit);
});

test("self-hosted Markdown Mode loads and shares the Guided draft", async ({ page }) => {
  const audit = await auditPage(page);
  const editedName = "Ada Browser Continuity";

  await page.goto("/human", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Make your work read like a signal." })).toBeVisible();
  await page.getByRole("button", { name: "Next: shape" }).click();
  await page.getByLabel("Name", { exact: true }).fill(editedName);
  await expect(page.getByRole("link", { name: "Markdown" })).toBeVisible();
  await page.getByRole("link", { name: "Markdown" }).click();
  await expect(page).toHaveURL(/\/md$/u);
  await expect(page.getByRole("heading", { name: "Edit the source. Keep the same document." })).toBeVisible();
  await expect(page.locator(".monaco-editor")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading Markdown editor…", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: editedName })).toBeVisible();

  await page.getByRole("link", { name: "Guided", exact: true }).click();
  await expect(page).toHaveURL(/\/human$/u);
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(editedName);

  await assertNoExternalWritesOrCredentials(audit);
});

test("Human Mode preserves the canonical stage journey and signed-out release boundary", async ({ page }) => {
  const audit = await auditPage(page);
  const protectedRequests: string[] = [];
  const editedNarrative = "Browser progression keeps this canonical narrative.";
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/v1/")) protectedRequests.push(`${request.method()} ${pathname}`);
  });

  await page.goto("/human", { waitUntil: "domcontentloaded" });
  const chapterNav = page.locator('nav[aria-label="Human Mode chapter navigation"]').first();
  const activeChapter = chapterNav.locator('button[aria-current="step"]');
  await expect(activeChapter).toHaveCount(1);
  await expect(activeChapter.getByText("Foundation", { exact: true })).toBeVisible();
  const markdownModeLabel = page
    .getByRole("navigation", {
      name: "Editing mode. Switching views keeps the current canonical draft.",
      exact: true,
    })
    .getByText("Markdown", { exact: true });
  await expect(markdownModeLabel).toBeVisible();
  expect(
    await markdownModeLabel.evaluate((element) => element.scrollWidth <= element.clientWidth),
  ).toBe(true);

  await page
    .getByRole("navigation", { name: "Foundation chapter navigation", exact: true })
    .getByRole("button", { name: "Next: shape", exact: true })
    .click();
  await expect(page.locator("#human-stage-shape-title")).toBeFocused();
  await expect(activeChapter).toHaveCount(1);
  await expect(activeChapter.getByText("Shape", { exact: true })).toBeVisible();

  const narrative = page.getByLabel("About", { exact: true });
  await narrative.fill(editedNarrative);
  await expect(narrative).toBeFocused();
  const reviewButton = page
    .getByRole("navigation", { name: "Shape chapter navigation", exact: true })
    .getByRole("button", { name: "Review document", exact: true });
  // Keep the buffered field focused so this exercises activateStage's explicit flush.
  await reviewButton.dispatchEvent("click");

  await expect(page.locator("#human-stage-review-title")).toBeFocused();
  await expect(activeChapter).toHaveCount(1);
  await expect(activeChapter.getByText("Review", { exact: true })).toBeVisible();
  await expect(page.locator(".light-preview").getByText(editedNarrative, { exact: true })).toBeVisible();

  await page
    .getByRole("navigation", { name: "Review chapter navigation", exact: true })
    .getByRole("button", { name: "Release document", exact: true })
    .click();
  await expect(page.locator("#human-stage-release-title")).toBeFocused();
  await expect(activeChapter).toHaveCount(1);
  await expect(activeChapter.getByText("Release", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Save gate", exact: true })).toBeVisible();
  await expect(
    page.getByText("Clerk configuration is required before this deployment can publish.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Save profile", exact: true })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "Agent API keys", exact: true })).toHaveCount(0);

  await page
    .getByRole("navigation", { name: "Release chapter navigation", exact: true })
    .getByRole("button", { name: "Back to review", exact: true })
    .click();
  await expect(page.locator("#human-stage-review-title")).toBeFocused();
  await expect(activeChapter).toHaveCount(1);
  await expect(activeChapter.getByText("Review", { exact: true })).toBeVisible();
  await expect(page.locator(".light-preview").getByText(editedNarrative, { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/human", { waitUntil: "domcontentloaded" });
  const compactControls = page.locator('[aria-label="Compact stage controls"]');
  const compactNext = compactControls.getByRole("button");
  const mobileChapterNav = page.locator('nav[aria-label="Human Mode chapter navigation"]').first();
  const mobileChapterButtons = mobileChapterNav.getByRole("button");
  const mobileActiveChapter = mobileChapterNav.locator('button[aria-current="step"]');
  await expect(compactControls).toBeVisible();
  await expect(compactControls).toContainText("Step 1 of 4");
  await expect(mobileChapterButtons).toHaveCount(4);
  const foundationBounds = await mobileChapterButtons.nth(0).boundingBox();
  const shapeBounds = await mobileChapterButtons.nth(1).boundingBox();
  const reviewBounds = await mobileChapterButtons.nth(2).boundingBox();
  const releaseBounds = await mobileChapterButtons.nth(3).boundingBox();
  expect(foundationBounds).not.toBeNull();
  expect(shapeBounds).not.toBeNull();
  expect(reviewBounds).not.toBeNull();
  expect(releaseBounds).not.toBeNull();
  expect(Math.abs((foundationBounds?.y ?? 0) - (shapeBounds?.y ?? 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((reviewBounds?.y ?? 0) - (releaseBounds?.y ?? 0))).toBeLessThanOrEqual(1);
  expect((reviewBounds?.y ?? 0) - (foundationBounds?.y ?? 0)).toBeGreaterThanOrEqual(44);
  await expect(mobileActiveChapter).toHaveCount(1);
  await expect(mobileActiveChapter.getByText("Foundation", { exact: true })).toBeVisible();
  await compactNext.click();
  await assertFocusedHeadingClearsStickyHeader(page, page.locator("#human-stage-shape-title"));
  await expect(mobileActiveChapter).toHaveCount(1);
  await expect(mobileActiveChapter.getByText("Shape", { exact: true })).toBeVisible();
  await expect(compactControls).toContainText("Step 2 of 4");
  await compactNext.click();
  await assertFocusedHeadingClearsStickyHeader(page, page.locator("#human-stage-review-title"));
  await expect(mobileActiveChapter).toHaveCount(1);
  await expect(mobileActiveChapter.getByText("Review", { exact: true })).toBeVisible();
  await expect(compactControls).toContainText("Step 3 of 4");
  await compactNext.click();
  await assertFocusedHeadingClearsStickyHeader(page, page.locator("#human-stage-release-title"));
  await expect(mobileActiveChapter).toHaveCount(1);
  await expect(mobileActiveChapter.getByText("Release", { exact: true })).toBeVisible();
  await expect(compactControls).toContainText("Step 4 of 4");
  await compactNext.click();
  await assertFocusedHeadingClearsStickyHeader(page, page.locator("#human-stage-review-title"));
  await expect(mobileActiveChapter).toHaveCount(1);
  await expect(mobileActiveChapter.getByText("Review", { exact: true })).toBeVisible();
  await expect(compactControls).toContainText("Step 3 of 4");

  await page.setViewportSize(narrowReflowViewport);
  await page.goto("/human", { waitUntil: "domcontentloaded" });
  const narrowModeSwitch = page.getByRole("navigation", {
    name: "Editing mode. Switching views keeps the current canonical draft.",
    exact: true,
  });
  const narrowModeSwitchBounds = await narrowModeSwitch.boundingBox();
  expect(narrowModeSwitchBounds).not.toBeNull();
  expect(narrowModeSwitchBounds?.y ?? Number.POSITIVE_INFINITY).toBeLessThan(
    narrowReflowViewport.height,
  );
  expect(
    (narrowModeSwitchBounds?.y ?? Number.POSITIVE_INFINITY)
      + (narrowModeSwitchBounds?.height ?? Number.POSITIVE_INFINITY),
  ).toBeLessThanOrEqual(narrowReflowViewport.height + 1);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
  ).toBe(true);

  expect(protectedRequests).toEqual([]);
  await assertNoExternalWritesOrCredentials(audit);
});

test("anonymous mobile navigation and auth boundaries remain keyboard-safe", async ({ page }) => {
  const audit = await auditPage(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle");

  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await expect(page.locator(":focus-visible")).toHaveCount(1);
  const skipLinkBounds = await skipLink.boundingBox();
  expect(skipLinkBounds?.width ?? 0).toBeGreaterThanOrEqual(44);
  expect(skipLinkBounds?.height ?? 0).toBeGreaterThanOrEqual(44);
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  const navigationToggle = page.getByRole("button", { name: "Open navigation" });
  await expect(navigationToggle).toHaveCSS("width", "44px");
  await expect(navigationToggle).toHaveCSS("height", "44px");
  const primaryNavigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
  const homeLink = primaryNavigation.getByRole("link", { name: "connect.md", exact: true });
  const [primaryNavigationBounds, homeLinkBounds, navigationToggleBounds] = await Promise.all([
    primaryNavigation.boundingBox(),
    homeLink.boundingBox(),
    navigationToggle.boundingBox(),
  ]);
  expect(primaryNavigationBounds).not.toBeNull();
  expect(homeLinkBounds).not.toBeNull();
  expect(navigationToggleBounds).not.toBeNull();
  expect(homeLinkBounds!.x - primaryNavigationBounds!.x).toBeGreaterThanOrEqual(12);
  expect(
    primaryNavigationBounds!.x + primaryNavigationBounds!.width
      - navigationToggleBounds!.x - navigationToggleBounds!.width,
  ).toBeGreaterThanOrEqual(12);
  await navigationToggle.focus();
  await page.keyboard.press("Enter");
  const mobileNavigation = page.getByRole("navigation", { name: "Mobile primary navigation", exact: true });
  await expect(mobileNavigation).toBeVisible();
  await expect(mobileNavigation.getByRole("link", { name: "Agent directory", exact: true })).toHaveAttribute("href", "/agent-directory");
  await expect(mobileNavigation.locator('a[href="/network"]')).toHaveCount(0);
  await expect(mobileNavigation.locator('a[href="/agents"]')).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();

  const noHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  );
  expect(noHorizontalOverflow).toBe(true);
  const motionSafe = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("*"))
      .flatMap((element) => {
        const style = getComputedStyle(element);
        return [style.animationDuration, style.transitionDuration];
      })
      .every((value) => value.split(",").every((duration) => Number.parseFloat(duration) <= 0.05)),
  );
  expect(motionSafe).toBe(true);

  await page.goto("/p/ada-lovelace", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByText("Private human connection controls are unavailable in this deployment.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText("Sign in to continue", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Follow profile" })).toHaveCount(0);

  await page.setViewportSize(narrowReflowViewport);
  for (const path of mobilePublicRoutes) {
    await page.goto(path, { waitUntil: "domcontentloaded" });
    const pageFitsMobileViewport = await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    );
    expect(pageFitsMobileViewport, path).toBe(true);
    await assertSequentialHeadingLevels(page, path);
  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  const firstUseLayout = [
    {
      path: "/",
      selectors: ['header a[href="/"]', '[aria-label="Open navigation"]', "#agent-handoff-instruction", 'a[href="/agent-readme.md"]'],
    },
    {
      path: "/human",
      selectors: ['header a[href="/"]', '[aria-label="Open navigation"]', "#human-stage-foundation", "#load-existing-title", "#ingest-title", 'a[href="/md"]'],
    },
    {
      path: "/md",
      selectors: ['header a[href="/"]', '[aria-label="Open navigation"]', "#editor-title", "#validation-title", "#preview-title"],
    },
  ] as const;
  // 160px is an effective CSS-layout proxy for 320px physical pixels at 200% zoom, not literal browser zoom.
  for (const viewportWidth of [320, 160]) {
    await page.setViewportSize({ width: viewportWidth, height: 900 });
    for (const route of firstUseLayout) {
      await assertNarrowFirstUseLayout(page, route.path, route.selectors, viewportWidth);
      await assertSequentialHeadingLevels(page, route.path);
    }
  }

  await assertNoExternalWritesOrCredentials(audit);
});

test("anonymous private routes fail closed without protected API reads", async ({ page }) => {
  const audit = await auditPage(page);
  const protectedRequests: string[] = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/v1/")) protectedRequests.push(`${request.method()} ${pathname}`);
  });

  const privatePaths = [
    "/account",
    "/agents",
    "/appeal-review",
    "/applications",
    "/employer",
    "/feed",
    "/inbox?profile=ada-lovelace",
    "/messages/conversation-1",
    "/moderation",
    "/moderation-review",
    "/network",
    "/verification-review",
    "/workspace",
  ] as const;

  for (const path of privatePaths) {
    const response = await page.goto(path, { waitUntil: "domcontentloaded" });
    expect(response).not.toBeNull();
    expect(response!.status(), path).toBe(404);
    expect(response!.headers()["cache-control"], path).toContain("no-store");
    expect(response!.headers()["x-robots-tag"], path).toContain("noindex");
    expect((await response!.body()).toString(), path).toContain('<meta name="robots" content="noindex, nofollow">');
  }

  await page.goto("/trust", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Know what is public before you publish.", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Private and permission-gated", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What you control", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Read the agent runbook", exact: true })).toBeVisible();

  expect(protectedRequests).toEqual([]);
  await assertNoExternalWritesOrCredentials(audit);
});

test("public release pages have no WCAG A or AA accessibility violations", async ({ page }) => {
  const audit = await auditPage(page);
  for (const path of [
    "/",
    "/trust",
    "/discover",
    "/agent-directory",
    "/representatives",
    "/p/ada-lovelace",
    "/r/ada-lovelace-resume",
    "/posts/fixture-post-field-notes",
    "/search",
  ]) {
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await assertA11y(page);
  }
  await page.setViewportSize(narrowReflowViewport);
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const path of mobilePublicRoutes) {
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await assertA11y(page);
  }
  await assertNoExternalWritesOrCredentials(audit);
});
