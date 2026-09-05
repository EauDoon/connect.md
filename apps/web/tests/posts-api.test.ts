import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createPostMarkdown, POST_MAX_CLIENT_MARKDOWN_BYTES, utf8ByteLength, validatePostDraft } from "../lib/post-markdown";
import { publicApiMarkdownUrl } from "../lib/api";
import { mergePostsById } from "../components/profile-post-archive";
import { blockProfileContent, followProfile, getProfilePostControls, listFeedForSubject, listFollowsForSubject, listProfilePostsForSubject, listProfilePostsOnServer, listPublicPostsOnServer, logicalIdempotencyKey, publishPost, reportPost, unblockProfileContent, unfollowProfile } from "../lib/posts-api";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

const post = { id: "post-1", author_profile_handle: "ari-chen", title: "Professional note", topics: ["payments"], version: 1, published_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", markdown: "---\nschema: connect.md/post\nschema_version: 1\nid: post-1\nauthor_profile_handle: ari-chen\nversion: 1\npublished_at: 2026-08-03T00:00:00Z\nupdated_at: 2026-08-03T00:00:00Z\ntitle: Professional note\ntopics:\n  - payments\nvisibility: public\n---\n# Professional note\n\nBody.\n", markdown_url: "/v1/posts/post-1.md", etag: "\"sha256:post\"" };
const publicPost = { id: "post-1", author_profile_handle: "ari-chen", title: "Professional note", topics: ["payments"], version: 1, published_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", html_url: "/posts/post-1", markdown_url: "/v1/posts/post-1.md", etag: "\"sha256:post\"" };

function configure(response: unknown, status = 200) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("professional post Markdown and API contracts", () => {
  it("constructs client-write canonical Markdown without server-owned fields", () => {
    const input = { title: "Professional note", topicsText: "payments, product-strategy", body: "A concise Markdown body." };
    const markdown = createPostMarkdown(input);
    expect(markdown).toContain("schema: connect.md/post");
    expect(markdown).toContain("# Professional note");
    expect(markdown).not.toMatch(/\n(?:id|author_profile_handle|version|published_at|updated_at):/u);
    expect(validatePostDraft(input)).toMatchObject({ topics: ["payments", "product-strategy"], issues: [] });
  });

  it("requires a valid topic and guards canonical byte budget before publication", () => {
    expect(validatePostDraft({ title: "Bad\nTitle", topicsText: "Payments, payments", body: "![remote](https://example.test/image.png)" }).issues).toEqual(expect.arrayContaining([
      expect.stringContaining("Title"), expect.stringContaining("Topics must not repeat")
    ]));
    expect(validatePostDraft({ title: "Post", topicsText: "", body: "Image syntax remains Markdown." }).issues).toEqual(expect.arrayContaining(["Add at least one topic."]));
    const oversized = validatePostDraft({ title: "Post", topicsText: "", body: "x".repeat(POST_MAX_CLIENT_MARKDOWN_BYTES) });
    expect(utf8ByteLength(oversized.markdown)).toBeGreaterThan(POST_MAX_CLIENT_MARKDOWN_BYTES);
    expect(oversized.issues.join(" ")).toContain("10 KiB");
  });

  it("uses the exact human post and report contracts with idempotency", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(post), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "report-1", post_id: "post-1", reason_code: "spam", created_at: "2026-08-03T01:00:00Z" }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";
    const markdown = createPostMarkdown({ title: "Professional note", topicsText: "payments", body: "Body." });

    await expect(publishPost(markdown, "request-1", token, () => true)).resolves.toMatchObject({ id: "post-1", authorProfileHandle: "ari-chen", markdownUrl: "/v1/posts/post-1.md" });
    await expect(reportPost("post-1", { reason: "spam", narrative: "Private context" }, "request-1", token, () => true)).resolves.toMatchObject({ id: "report-1", postId: "post-1", reason: "spam" });
    const [publishUrl, publishInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(publishUrl).toBe("https://api.connect.test/v1/posts");
    expect(new Headers(publishInit.headers).get("Idempotency-Key")).toBe("request-1");
    expect(JSON.parse(String(publishInit.body))).toEqual({ markdown });
    const [reportUrl, reportInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(reportUrl).toBe("https://api.connect.test/v1/posts/post-1/report");
    expect(new Headers(reportInit.headers).get("Idempotency-Key")).toBe("request-1");
    expect(JSON.parse(String(reportInit.body))).toEqual({ reason_code: "spam", narrative: "Private context" });
  });

  it("uses caller-owned keys and exact follow/content-control response contracts", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ profile_handle: "ari-chen", created_at: "2026-08-03T01:00:00Z" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204, headers: { "Idempotency-Replayed": "true" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204, headers: { "Idempotency-Replayed": "true" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";

    await expect(followProfile("ari-chen", token, () => true, "follow-key")).resolves.toEqual({ profileHandle: "ari-chen", createdAt: "2026-08-03T01:00:00Z" });
    await expect(unfollowProfile("ari-chen", token, () => true, "unfollow-key")).resolves.toBeUndefined();
    await expect(blockProfileContent("ari-chen", token, () => true, "block-key")).resolves.toBeUndefined();
    await expect(unblockProfileContent("ari-chen", token, () => true, "unblock-key")).resolves.toBeUndefined();

    const [followUrl, followInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(followUrl).toBe("https://api.connect.test/v1/follows/ari-chen");
    expect(followInit.method).toBe("POST");
    expect(new Headers(followInit.headers).get("Authorization")).toBe("Bearer clerk-token");
    expect(new Headers(followInit.headers).get("Idempotency-Key")).toBe("follow-key");
    expect(followInit.body).toBeUndefined();

    const [unfollowUrl, unfollowInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(unfollowUrl).toBe("https://api.connect.test/v1/follows/ari-chen");
    expect(unfollowInit.method).toBe("DELETE");
    expect(new Headers(unfollowInit.headers).get("Idempotency-Key")).toBe("unfollow-key");
    expect(new Headers(unfollowInit.headers).get("Content-Type")).toBeNull();
    expect(unfollowInit.body).toBeUndefined();

    const [blockUrl, blockInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(blockUrl).toBe("https://api.connect.test/v1/content-blocks/ari-chen");
    expect(blockInit.method).toBe("POST");
    expect(new Headers(blockInit.headers).get("Idempotency-Key")).toBe("block-key");

    const [unblockUrl, unblockInit] = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(unblockUrl).toBe("https://api.connect.test/v1/content-blocks/ari-chen");
    expect(unblockInit.method).toBe("DELETE");
    expect(new Headers(unblockInit.headers).get("Idempotency-Key")).toBe("unblock-key");
    expect(new Headers(unblockInit.headers).get("Content-Type")).toBeNull();
    expect(unblockInit.body).toBeUndefined();
  });

  it("reuses an explicit key after ambiguous or malformed successful follow responses", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const parserFailureBody = {};
    Object.defineProperties(parserFailureBody, {
      profile_handle: { enumerable: true, get: () => { throw new Error("private parser bytes"); } },
      created_at: { enumerable: true, value: "2026-08-03T01:00:00Z" },
    });
    const parserFailureResponse = {
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: async () => parserFailureBody,
    } as unknown as Response;
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("connection dropped after commit"))
      .mockResolvedValueOnce(parserFailureResponse)
      .mockResolvedValueOnce(new Response(JSON.stringify({ profile_handle: "ari-chen", created_at: "2026-08-03T01:00:00Z" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";

    await expect(followProfile("ari-chen", token, () => true, "follow-retry-key")).rejects.toMatchObject({ code: "request" });
    await expect(followProfile("ari-chen", token, () => true, "follow-retry-key")).rejects.toMatchObject({ status: 502, code: "server", message: expect.not.stringContaining("private parser bytes") });
    await expect(followProfile("ari-chen", token, () => true, "follow-retry-key")).resolves.toEqual({ profileHandle: "ari-chen", createdAt: "2026-08-03T01:00:00Z" });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("follow-retry-key");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("follow-retry-key");
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("Idempotency-Key")).toBe("follow-retry-key");
  });

  it("rejects wrong successful status, body, and replay marker without response diagnostics", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ profile_handle: "ari-chen", created_at: "2026-08-03T01:00:00Z" }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204, headers: { "Idempotency-Replayed": "false" } }))
      .mockResolvedValueOnce(new Response("unexpected", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";

    await expect(followProfile("ari-chen", token, () => true, "wrong-status")).rejects.toMatchObject({ status: 502, code: "server" });
    await expect(unfollowProfile("ari-chen", token, () => true, "wrong-marker")).rejects.toMatchObject({ status: 502, code: "server" });
    await expect(blockProfileContent("ari-chen", token, () => true, "wrong-empty-status")).rejects.toMatchObject({ status: 502, code: "server", message: expect.not.stringContaining("unexpected") });
  });

  it("rejects a mismatched or credential-shaped follow response", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ profile_handle: "other-profile", created_at: "2026-08-03T01:00:00Z" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ profile_handle: "ari-chen", created_at: "2026-08-03T01:00:00Z", key: "private-key", token: "private-token", secret: "private-secret", recovery_required: true }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ profile_handle: "ari-chen", created_at: "not-a-timestamp" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";

    await expect(followProfile("ari-chen", token, () => true, "mismatched-handle")).rejects.toMatchObject({ status: 502, code: "server" });
    await expect(followProfile("ari-chen", token, () => true, "credential-shaped")).rejects.toMatchObject({ status: 502, code: "server", message: expect.not.stringContaining("private-secret") });
    await expect(followProfile("ari-chen", token, () => true, "invalid-time")).rejects.toMatchObject({ status: 502, code: "server" });
  });

  it("reuses one logical publication key after a lost acknowledgement and rotates it only when bytes change", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValueOnce("publish-1").mockReturnValueOnce("publish-2") });
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("connection dropped after commit"))
      .mockResolvedValueOnce(new Response(JSON.stringify(post), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";
    const markdown = createPostMarkdown({ title: "Professional note", topicsText: "payments", body: "Body." });
    let attempt = logicalIdempotencyKey(null, "subject-1", markdown);
    await expect(publishPost(markdown, attempt.key, token, () => true)).rejects.toMatchObject({ code: "request" });
    attempt = logicalIdempotencyKey(attempt, "subject-1", markdown);
    await expect(publishPost(markdown, attempt.key, token, () => true)).resolves.toMatchObject({ id: "post-1" });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("publish-1");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("publish-1");
    expect(logicalIdempotencyKey(attempt, "subject-1", `${markdown}\nEdited`).key).toBe("publish-2");
  });

  it("reads pre-existing caller-owned profile post controls without inferring their state", async () => {
    const fetchMock = configure({ following: true, content_blocked: true });
    await expect(getProfilePostControls("ari-chen", async () => "clerk-token", () => true)).resolves.toEqual({ following: true, contentBlocked: true });
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.connect.test/v1/profile-post-controls/ari-chen");
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer clerk-token");
  });

  it("keeps every post/follow/block/report mutation subject-bound before dispatch", async () => {
    const fetchMock = configure({});
    const transition = async <T>(operation: (token: () => Promise<string>, guard: () => boolean) => Promise<T>) => {
      let current = true;
      await expect(operation(async () => { current = false; return "different-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    };
    await transition((token, guard) => publishPost("# post", "request-1", token, guard));
    await transition((token, guard) => followProfile("ari-chen", token, guard, "follow-subject-key"));
    await transition((token, guard) => unfollowProfile("ari-chen", token, guard, "unfollow-subject-key"));
    await transition((token, guard) => blockProfileContent("ari-chen", token, guard, "block-subject-key"));
    await transition((token, guard) => unblockProfileContent("ari-chen", token, guard, "unblock-subject-key"));
    await transition((token, guard) => reportPost("post-1", { reason: "spam", narrative: "" }, "request-1", token, guard));
    await transition((token, guard) => getProfilePostControls("ari-chen", token, guard));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch private reads with a token resolved after an account transition", async () => {
    const fetchMock = configure({ posts: [], next_cursor: null });
    const transition = async <T>(operation: (token: () => Promise<string>, guard: () => boolean) => Promise<T>) => {
      let current = true;
      await expect(operation(async () => { current = false; return "different-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    };
    await transition((token, guard) => listProfilePostsForSubject("ari-chen", token, guard));
    await transition((token, guard) => listFeedForSubject(token, guard));
    await transition((token, guard) => listFollowsForSubject(token, guard));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the private server API origin for the crawlable profile-post archive", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "http://api:8000");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ posts: [post], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(listProfilePostsOnServer("ari-chen")).resolves.toMatchObject({ posts: [{ id: "post-1" }] });
    expect(fetchMock.mock.calls[0][0]).toBe("http://api:8000/v1/profiles/ari-chen/posts?limit=25");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("parses the public chronological inventory through exact canonical URL and privacy allowlists", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "http://api:8000");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ items: [publicPost], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPublicPostsOnServer(4)).resolves.toEqual({
      items: [{ id: "post-1", authorProfileHandle: "ari-chen", title: "Professional note", topics: ["payments"], version: 1, publishedAt: "2026-08-03T00:00:00Z", updatedAt: "2026-08-03T00:00:00Z", htmlUrl: "/posts/post-1", markdownUrl: "/v1/posts/post-1.md", etag: "\"sha256:post\"" }],
      nextCursor: null,
    });
    expect(fetchMock.mock.calls[0][0]).toBe("http://api:8000/v1/posts?limit=4");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("rejects body/private fields and noncanonical public-inventory URLs", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "http://api:8000");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...publicPost, markdown: "private duplicate body", owner_id: "owner-secret" }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...publicPost, html_url: "https://untrusted.example/posts/post-1" }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPublicPostsOnServer(4)).rejects.toMatchObject({ code: "server", message: expect.not.stringContaining("owner-secret") });
    await expect(listPublicPostsOnServer(4)).rejects.toMatchObject({ code: "server", message: expect.not.stringContaining("untrusted.example") });
  });

  it("accepts short and empty pages with a progressing raw-candidate cursor", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "http://api:8000");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [publicPost], next_cursor: "filtered-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: "filtered-again" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPublicPostsOnServer(4)).resolves.toMatchObject({ items: [{ id: "post-1" }], nextCursor: "filtered-next" });
    await expect(listPublicPostsOnServer(4, "filtered-next")).resolves.toEqual({ items: [], nextCursor: "filtered-again" });
  });

  it("scopes component state by account, auth mode, and post before it can paint or dispatch", () => {
    const controls = readFileSync(new URL("../components/profile-post-controls.tsx", import.meta.url), "utf8");
    const archive = readFileSync(new URL("../components/profile-post-archive.tsx", import.meta.url), "utf8");
    const reportControl = readFileSync(new URL("../components/post-report-control.tsx", import.meta.url), "utf8");
    expect(controls).toContain('key={`${subject}:${handle}`}');
    expect(archive).toContain('key={`authenticated:${subject}:${handle}`}');
    expect(archive).toContain('key={`anonymous:${handle}`}');
    expect(archive).toContain("listProfilePostsForSubject(handle, getToken, isSubjectCurrent, cursor)");
    expect(reportControl).toContain('key={`${subject}:${postId}`}');
    expect(reportControl).not.toContain("useEffect");
  });

  it("requires the synchronous owner and captured subject guard on every private post-control completion", () => {
    const controls = readFileSync(new URL("../components/profile-post-controls.tsx", import.meta.url), "utf8");
    const feed = readFileSync(new URL("../components/professional-feed.tsx", import.meta.url), "utf8");
    for (const source of [controls, feed]) {
      expect(source).toContain("claimLogicalMutation(mutationClaimSlotRef.current)");
      expect(source).toContain("const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent()");
      expect(source).toContain("if (!requestIsCurrent()) return;");
      expect(source).toContain("claim.release(); setBusy(null)");
    }
    expect(controls.indexOf("claimLogicalMutation(mutationClaimSlotRef.current")).toBeLessThan(controls.indexOf('setBusy("follow")'));
    expect(controls).toContain("setState(requestBlocked ? { following: false, contentBlocked: false } : { following: false, contentBlocked: true })");
    expect(feed).toContain("unfollowAttemptsRef.current");
  });

  it("keeps the private feed's moderation action at the 44px minimum", () => {
    const feed = readFileSync(new URL("../components/professional-feed.tsx", import.meta.url), "utf8");
    const moderationLink = feed.match(/<Link href="\/moderation" className="([^"]+)">Review post case status<\/Link>/u);

    expect(moderationLink?.[1]?.split(/\s+/u)).toEqual(expect.arrayContaining([
      "inline-flex",
      "min-h-11",
      "items-center",
    ]));
  });

  it("de-duplicates archive page IDs and blocks non-progressing cursors before another request", () => {
    const first = { id: "one", authorProfileHandle: "ari-chen", title: "One", topics: ["payments"], version: 1 as const, publishedAt: "2026-08-03T00:00:00Z", updatedAt: "2026-08-03T00:00:00Z", markdown: "# One", markdownUrl: "/v1/posts/one.md", etag: "one" };
    const duplicate = { ...first };
    const second = { ...first, id: "two", title: "Two", markdownUrl: "/v1/posts/two.md" };
    expect(mergePostsById([first], [duplicate, second, second])).toEqual([first, second]);
    const archive = readFileSync(new URL("../components/profile-post-archive.tsx", import.meta.url), "utf8");
    const feed = readFileSync(new URL("../components/professional-feed.tsx", import.meta.url), "utf8");
    expect(archive).toContain("inFlightRef.current !== null");
    expect(archive).toContain("deliveredCursorsRef.current.has(requestKey)");
    expect(feed).toContain("moreInFlightRef.current.has(kind)");
    expect(feed).toContain("initialLoadInFlightRef.current");
  });

  it("resolves canonical Markdown against only a safe public API origin", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");
    expect(publicApiMarkdownUrl("/v1/posts/post-1.md")).toBe("https://api.connect.test/v1/posts/post-1.md");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://user:pass@api.connect.test/");
    expect(publicApiMarkdownUrl("/v1/posts/post-1.md")).toBeNull();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/api");
    expect(publicApiMarkdownUrl("/v1/posts/post-1.md")).toBeNull();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/?debug=1");
    expect(publicApiMarkdownUrl("/v1/posts/post-1.md")).toBeNull();
    vi.unstubAllEnvs();
    expect(publicApiMarkdownUrl("/v1/posts/post-1.md")).toBe("/v1/posts/post-1.md");
    expect(publicApiMarkdownUrl("javascript:alert(1)")).toBeNull();
    expect(publicApiMarkdownUrl("//api.connect.test/v1/posts/post-1.md")).toBeNull();
    expect(publicApiMarkdownUrl("/posts/post-1.md")).toBeNull();
    expect(publicApiMarkdownUrl("https://outside.test/v1/posts/post-1.md")).toBeNull();
  });

  it("clears private report input and avoids persistence, credentials, and agent controls", () => {
    const reportControl = readFileSync(new URL("../components/post-report-control.tsx", import.meta.url), "utf8");
    const composer = readFileSync(new URL("../components/post-composer.tsx", import.meta.url), "utf8");
    expect(reportControl).toContain('setReason(""); setNarrative(""); setOpen(false)');
    expect(reportControl).not.toMatch(/localStorage|sessionStorage|URLSearchParams|console\./u);
    expect(composer).toContain("Agents and API keys cannot publish posts");
    expect(composer).toContain("publicationRef.current = beginLogicalMutationAttempt");
    expect(composer).not.toMatch(/localStorage|sessionStorage|console\./u);
    const controls = readFileSync(new URL("../components/profile-post-controls.tsx", import.meta.url), "utf8");
    expect(controls).toContain("useState<ProfilePostControlState | null>(null)");
    expect(controls).toContain("getProfilePostControls(handle, getToken, isSubjectCurrent)");
  });
});
