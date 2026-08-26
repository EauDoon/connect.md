import { ApiRequestError, apiRequest, apiRequestWithMetadata, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";
import { beginLogicalMutationAttempt } from "@/lib/logical-mutation";

export type ProfessionalPost = { id: string; authorProfileHandle: string; title: string; topics: string[]; version: 1; publishedAt: string; updatedAt: string; markdown: string; markdownUrl: string; etag: string };
export type PostPage = { posts: ProfessionalPost[]; nextCursor: string | null };
export type PublicPostSummary = { id: string; authorProfileHandle: string; title: string; topics: string[]; version: 1; publishedAt: string; updatedAt: string; htmlUrl: string; markdownUrl: string; etag: string };
export type PublicPostInventoryPage = { items: PublicPostSummary[]; nextCursor: string | null };
export type ProfileFollow = { profileHandle: string; createdAt: string };
export type FollowPage = { follows: ProfileFollow[]; nextCursor: string | null };
export type ProfilePostControlState = { following: boolean; contentBlocked: boolean };
export const POST_REPORT_REASONS = ["spam", "harassment", "misinformation", "privacy", "illegal_content", "other"] as const;
export type PostReportReason = (typeof POST_REPORT_REASONS)[number];

export function presentPostsError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "connect.md could not complete that post action. No change was assumed.";
  if (error.code === "offline") return "You are offline. Reconnect before trying again.";
  if (error.code === "unauthorized") return "This professional post action requires your signed-in human session.";
  if (error.code === "not_found") return "That post or profile is unavailable.";
  if (error.code === "server") return "connect.md is temporarily unavailable. No change was assumed.";
  return error.message;
}

export async function fetchPublicPost(id: string) {
  return parsePost(await apiRequest<unknown>(`/v1/posts/${encodeURIComponent(id)}`, { server: true }));
}

export async function listProfilePosts(handle: string, getToken?: TokenGetter, cursor: string | null = null) {
  const token = getToken ? await getToken() : null;
  return listProfilePostsWithToken(handle, token, cursor);
}

export async function listProfilePostsOnServer(handle: string, cursor: string | null = null) {
  return parsePostPage(await apiRequest<unknown>(`/v1/profiles/${encodeURIComponent(handle)}/posts?${pageParams(cursor).toString()}`, { server: true }));
}

export async function listPublicPostsOnServer(limit = 25, cursor: string | null = null) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw invalid("public post inventory limit");
  if (cursor !== null && (!cursor || cursor.length > 500)) throw invalid("public post inventory cursor");
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return parsePublicPostInventoryPage(
    await apiRequest<unknown>(`/v1/posts?${params.toString()}`, { server: true }),
    limit,
  );
}

export async function listProfilePostsForSubject(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listProfilePostsWithToken(handle, token, cursor));
}

export async function listFeed(getToken: TokenGetter, cursor: string | null = null) {
  return listFeedWithToken(await getToken(), cursor);
}

export async function listFeedForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listFeedWithToken(token, cursor));
}

export async function listFollows(getToken: TokenGetter, cursor: string | null = null) {
  return listFollowsWithToken(await getToken(), cursor);
}

export async function listFollowsForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listFollowsWithToken(token, cursor));
}

export async function getProfilePostControls(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return parseProfilePostControlState(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/profile-post-controls/${encodeURIComponent(handle)}`, { token })));
}

export function logicalIdempotencyKey(previous: { fingerprint: string; key: string } | null, subject: string, fingerprint: string) {
  const attempt = beginLogicalMutationAttempt(
    previous ? { fingerprint: previous.fingerprint, idempotencyKey: previous.key } : null,
    subject,
    fingerprint,
  );
  return { fingerprint: attempt.fingerprint, key: attempt.idempotencyKey };
}

export async function publishPost(markdown: string, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return parsePost(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>("/v1/posts", { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ markdown }) })));
}

export async function followProfile(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/follows/${encodeURIComponent(handle)}`, { method: "POST", token, headers: jsonHeaders(idempotencyKey) }));
  if (response.status !== 200) throw invalidMutationResponse("follow");
  assertReplayHeader(response.headers, "follow");
  try {
    return parseFollow(response.body, handle);
  } catch {
    throw invalidMutationResponse("follow");
  }
}

export async function unfollowProfile(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/follows/${encodeURIComponent(handle)}`, { method: "DELETE", token, headers: jsonHeaders(idempotencyKey, false) }));
  assertEmptyMutationResponse(response, "unfollow");
}

export async function blockProfileContent(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/content-blocks/${encodeURIComponent(handle)}`, { method: "POST", token, headers: jsonHeaders(idempotencyKey) }));
  assertEmptyMutationResponse(response, "content block");
}

export async function unblockProfileContent(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/content-blocks/${encodeURIComponent(handle)}`, { method: "DELETE", token, headers: jsonHeaders(idempotencyKey, false) }));
  assertEmptyMutationResponse(response, "content unblock");
}

export async function reportPost(id: string, input: { reason: PostReportReason; narrative: string }, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return parseReport(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/posts/${encodeURIComponent(id)}/report`, { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ reason_code: input.reason, narrative: input.narrative.trim() || null }) })));
}

function pageParams(cursor: string | null) { const query = new URLSearchParams({ limit: "25" }); if (cursor) query.set("cursor", cursor); return query; }
async function listProfilePostsWithToken(handle: string, token: string | null, cursor: string | null) { return parsePostPage(await apiRequest<unknown>(`/v1/profiles/${encodeURIComponent(handle)}/posts?${pageParams(cursor).toString()}`, { token })); }
async function listFeedWithToken(token: string | null, cursor: string | null) { return parsePostPage(await apiRequest<unknown>(`/v1/feed?${pageParams(cursor).toString()}`, { token })); }
async function listFollowsWithToken(token: string | null, cursor: string | null) { return parseFollowPage(await apiRequest<unknown>(`/v1/follows?${pageParams(cursor).toString()}`, { token })); }
function jsonHeaders(idempotencyKey: string, withJson = true) { return { ...(withJson ? { "Content-Type": "application/json" } : {}), "Idempotency-Key": idempotencyKey }; }
function assertReplayHeader(headers: Headers, label: string) { const replayed = headers.get("Idempotency-Replayed"); if (replayed !== null && replayed !== "true") throw invalidMutationResponse(label); }
function assertEmptyMutationResponse(response: { status: number; body: unknown; headers: Headers }, label: string) { if (response.status !== 204 || response.body !== "") throw invalidMutationResponse(label); assertReplayHeader(response.headers, label); }
function invalidMutationResponse(label: string) { return new ApiRequestError(`The API returned an invalid ${label} response.`, 502, "server"); }
function parsePostPage(value: unknown): PostPage { const raw = record(value, "post list"); const posts = Array.isArray(raw.posts) ? raw.posts.map(parsePost) : null; if (!posts) throw invalid("post list"); return { posts, nextCursor: nullableText(raw.next_cursor, "post cursor") }; }
function parsePublicPostInventoryPage(value: unknown, requestedLimit: number): PublicPostInventoryPage {
  const raw = record(value, "public post inventory");
  exactKeys(raw, ["items", "next_cursor"], "public post inventory");
  if (!Array.isArray(raw.items) || raw.items.length > requestedLimit) throw invalid("public post inventory");
  const items = raw.items.map(parsePublicPostSummary);
  const nextCursor = raw.next_cursor === null ? null : requiredBoundedText(raw.next_cursor, "public post inventory cursor", 500);
  return { items, nextCursor };
}
function parseFollowPage(value: unknown): FollowPage { const raw = record(value, "follow list"); const follows = Array.isArray(raw.follows) ? raw.follows.map((item) => parseFollow(item)) : null; if (!follows) throw invalid("follow list"); return { follows, nextCursor: nullableText(raw.next_cursor, "follow cursor") }; }
function parsePost(value: unknown): ProfessionalPost { const raw = record(value, "post"); if (!Array.isArray(raw.topics) || raw.topics.some((topic) => typeof topic !== "string")) throw invalid("post topics"); const id = required(raw.id, "post id"); const markdownUrl = required(raw.markdown_url, "post Markdown URL"); if (markdownUrl !== `/v1/posts/${encodeURIComponent(id)}.md`) throw invalid("post Markdown URL"); return { id, authorProfileHandle: required(raw.author_profile_handle, "post author profile handle"), title: required(raw.title, "post title"), topics: raw.topics as string[], version: oneOf(raw.version, [1], "post version"), publishedAt: required(raw.published_at, "post publication time"), updatedAt: required(raw.updated_at, "post update time"), markdown: required(raw.markdown, "post Markdown"), markdownUrl, etag: required(raw.etag, "post ETag") }; }
function parsePublicPostSummary(value: unknown): PublicPostSummary {
  const raw = record(value, "public post inventory item");
  exactKeys(raw, ["id", "author_profile_handle", "title", "topics", "version", "published_at", "updated_at", "html_url", "markdown_url", "etag"], "public post inventory item");
  const id = requiredBoundedText(raw.id, "public post id", 128);
  const authorProfileHandle = requiredBoundedText(raw.author_profile_handle, "public post author profile handle", 100);
  if (!/^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/u.test(authorProfileHandle)) throw invalid("public post author profile handle");
  const title = requiredBoundedText(raw.title, "public post title", 160);
  if (/[\r\n]/u.test(title)) throw invalid("public post title");
  if (!Array.isArray(raw.topics) || raw.topics.length < 1 || raw.topics.length > 10 || raw.topics.some((topic) => typeof topic !== "string" || !/^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/u.test(topic))) throw invalid("public post topics");
  const htmlUrl = required(raw.html_url, "public post HTML URL");
  const markdownUrl = required(raw.markdown_url, "public post Markdown URL");
  if (htmlUrl !== `/posts/${encodeURIComponent(id)}` || markdownUrl !== `/v1/posts/${encodeURIComponent(id)}.md`) throw invalid("public post canonical URLs");
  return {
    id,
    authorProfileHandle,
    title,
    topics: raw.topics as string[],
    version: oneOf(raw.version, [1], "public post version"),
    publishedAt: requiredTimestamp(raw.published_at, "public post publication time"),
    updatedAt: requiredTimestamp(raw.updated_at, "public post update time"),
    htmlUrl,
    markdownUrl,
    etag: requiredBoundedText(raw.etag, "public post ETag", 256),
  };
}
function parseFollow(value: unknown, expectedProfileHandle?: string): ProfileFollow {
  const raw = record(value, "follow");
  const keys = Object.keys(raw);
  if (keys.length !== 2 || !keys.includes("profile_handle") || !keys.includes("created_at")) throw invalid("follow");
  const profileHandle = required(raw.profile_handle, "follow profile handle");
  if (expectedProfileHandle !== undefined && profileHandle !== expectedProfileHandle) throw invalid("follow profile handle");
  return { profileHandle, createdAt: requiredTimestamp(raw.created_at, "follow creation time") };
}
function parseProfilePostControlState(value: unknown): ProfilePostControlState { const raw = record(value, "profile post controls"); if (typeof raw.following !== "boolean" || typeof raw.content_blocked !== "boolean") throw invalid("profile post controls"); return { following: raw.following, contentBlocked: raw.content_blocked }; }
function parseReport(value: unknown) { const raw = record(value, "post report"); return { id: required(raw.id, "post report id"), postId: required(raw.post_id, "post report post id"), reason: required(raw.reason_code, "post report reason"), createdAt: required(raw.created_at, "post report creation time") }; }
function record(value: unknown, label: string): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid(label); return value as Record<string, unknown>; }
function required(value: unknown, label: string) { if (typeof value !== "string" || !value) throw invalid(label); return value; }
function requiredBoundedText(value: unknown, label: string, maxLength: number) { const result = required(value, label); if (result.length > maxLength || /[\r\n]/u.test(result)) throw invalid(label); return result; }
function requiredTimestamp(value: unknown, label: string) {
  const result = required(value, label);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/u.test(result) || Number.isNaN(Date.parse(result))) throw invalid(label);
  return result;
}
function nullableText(value: unknown, label: string) { if (value === null || value === undefined) return null; return required(value, label); }
function oneOf<T extends string | number>(value: unknown, values: readonly T[], label: string): T { if (!values.includes(value as T)) throw invalid(label); return value as T; }
function exactKeys(value: Record<string, unknown>, expected: readonly string[], label: string) { const actual = Object.keys(value).sort(); const allowed = [...expected].sort(); if (actual.length !== allowed.length || actual.some((key, index) => key !== allowed[index])) throw invalid(label); }
function invalid(label: string) { return new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); }
