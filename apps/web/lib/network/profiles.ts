/**
 * Profile service: one canonical, private-by-default Markdown profile per
 * account, with explicit publishing.
 *
 * Validation uses the same canonical validator the guest builder uses
 * (lib/validation validateDraft), so a profile saved to the network is the
 * same document the owner validated locally. Saving is not publishing:
 * `visibility` starts private and changes only through the explicit publish
 * and unpublish actions.
 */

import { createHash } from "node:crypto";
import postgres from "postgres";
import { hasValidationErrors, validateDraft } from "@/lib/validation";
import { EMPTY_DRAFT_ISSUE, PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";
import type { AccountRecord } from "./auth-service";

export type ProfileRecord = {
  markdown: string;
  etag: string;
  visibility: "private" | "public";
  publishedAt: string | null;
  updatedAt: string;
  createdAt: string;
};

export class ProfileError extends Error {
  readonly code: "invalid" | "not-found" | "conflict" | "precondition" | "private";

  constructor(code: ProfileError["code"], message: string) {
    super(message);
    this.code = code;
    this.name = "ProfileError";
  }
}

export function profileEtag(markdown: string): string {
  return `"${createHash("sha256").update(markdown, "utf8").digest("hex")}"`;
}

function serializeProfile(row: Record<string, unknown> | undefined): ProfileRecord {
  if (row === undefined) throw new ProfileError("not-found", "No profile saved yet.");
  return {
    markdown: row.markdown as string,
    etag: row.etag as string,
    visibility: row.visibility as "private" | "public",
    publishedAt: row.published_at === null ? null : (row.published_at as Date).toISOString(),
    updatedAt: (row.updated_at as Date).toISOString(),
    createdAt: (row.created_at as Date).toISOString(),
  };
}

export function validateProfileMarkdown(markdown: unknown): string {
  if (typeof markdown !== "string") throw new ProfileError("invalid", "Profile must be Markdown text.");
  const bytes = Buffer.byteLength(markdown, "utf8");
  if (bytes === 0) throw new ProfileError("invalid", EMPTY_DRAFT_ISSUE);
  if (bytes > PROFILE_RESUME_MAX_UTF8_BYTES) {
    throw new ProfileError("invalid", `Profile exceeds ${PROFILE_RESUME_MAX_UTF8_BYTES} UTF-8 bytes.`);
  }
  const issues = validateDraft(markdown, "profile");
  if (hasValidationErrors(issues)) {
    const first = issues.find((issue) => issue.level === "error") ?? issues[0];
    throw new ProfileError("invalid", first?.message ?? "Profile Markdown is invalid.");
  }
  return markdown;
}

export async function getProfile(sql: postgres.Sql, accountId: string): Promise<ProfileRecord | null> {
  const rows = await sql`
    SELECT markdown, etag, visibility, published_at, updated_at, created_at
    FROM network_profiles WHERE account_id = ${accountId} LIMIT 1
  `;
  if (rows.length === 0) return null;
  return serializeProfile(rows[0]);
}

export async function saveProfile(
  sql: postgres.Sql,
  account: AccountRecord,
  markdown: unknown,
  ifMatch: string | null,
): Promise<ProfileRecord> {
  const validated = validateProfileMarkdown(markdown);
  const etag = profileEtag(validated);
  const expectedEtag = ifMatch ?? (await networkProfilesCurrentEtag(sql, account.id));
  const rows = await sql`
    INSERT INTO network_profiles (account_id, markdown, etag, visibility)
    VALUES (${account.id}, ${validated}, ${etag}, 'private')
    ON CONFLICT (account_id) DO UPDATE SET
      markdown = EXCLUDED.markdown,
      etag = EXCLUDED.etag,
      updated_at = now()
    WHERE network_profiles.etag = ${expectedEtag}
    RETURNING markdown, etag, visibility, published_at, updated_at, created_at
  `;
  if (rows.length === 0) {
    throw new ProfileError("precondition", "The profile changed since you last loaded it. Reload and reapply your edit.");
  }
  return serializeProfile(rows[0]);
}

async function networkProfilesCurrentEtag(sql: postgres.Sql, accountId: string): Promise<string> {
  const rows = await sql`SELECT etag FROM network_profiles WHERE account_id = ${accountId} LIMIT 1`;
  if (rows.length === 0) {
    // First save: no conflict can occur, so the WHERE clause is irrelevant.
    // The marker only needs to be a valid, unmatchable SQL string (real
    // etags are double-quoted hex, so this can never match one).
    return "__first_save_no_conflict__";
  }
  return rows[0]!.etag as string;
}

export async function setProfileVisibility(
  sql: postgres.Sql,
  account: AccountRecord,
  visibility: "private" | "public",
): Promise<ProfileRecord> {
  const rows = await sql`
    UPDATE network_profiles SET
      visibility = ${visibility},
      published_at = CASE WHEN ${visibility} = 'public' THEN COALESCE(published_at, now()) ELSE published_at END,
      updated_at = now()
    WHERE account_id = ${account.id}
    RETURNING markdown, etag, visibility, published_at, updated_at, created_at
  `;
  if (rows.length === 0) {
    throw new ProfileError("not-found", "Save a profile before changing its visibility.");
  }
  return serializeProfile(rows[0]);
}

export type PublishedProfileSummary = {
  handle: string;
  etag: string;
  publishedAt: string;
  updatedAt: string;
};

/** Discovery: explicitly published profiles only, newest first. */
export async function listPublishedProfiles(
  sql: postgres.Sql,
  options: { prefix?: string; limit?: number } = {},
): Promise<PublishedProfileSummary[]> {
  const limit = Math.max(1, Math.min(100, options.limit ?? 50));
  const prefix = options.prefix === undefined ? null : options.prefix.toLowerCase();
  const rows = prefix === null
    ? await sql`
        SELECT a.handle, p.etag, p.published_at, p.updated_at
        FROM network_profiles p JOIN network_accounts a ON a.id = p.account_id
        WHERE p.visibility = 'public' AND a.status = 'active'
        ORDER BY p.published_at DESC LIMIT ${limit}
      `
    : await sql`
        SELECT a.handle, p.etag, p.published_at, p.updated_at
        FROM network_profiles p JOIN network_accounts a ON a.id = p.account_id
        WHERE p.visibility = 'public' AND a.status = 'active' AND a.handle LIKE ${prefix + "%"}
        ORDER BY p.published_at DESC LIMIT ${limit}
      `;
  return rows.map((row) => ({
    handle: row.handle as string,
    etag: row.etag as string,
    publishedAt: (row.published_at as Date).toISOString(),
    updatedAt: (row.updated_at as Date).toISOString(),
  }));
}

/** Public read: published profile of one handle; fails closed to not-found. */
export async function getPublishedProfile(sql: postgres.Sql, handle: string): Promise<ProfileRecord & { handle: string }> {
  const rows = await sql`
    SELECT p.markdown, p.etag, p.visibility, p.published_at, p.updated_at, p.created_at
    FROM network_profiles p JOIN network_accounts a ON a.id = p.account_id
    WHERE a.handle = ${handle.toLowerCase()} AND p.visibility = 'public' AND a.status = 'active'
    LIMIT 1
  `;
  const row = rows[0];
  if (row === undefined) throw new ProfileError("not-found", "No published profile for that handle.");
  return { handle: handle.toLowerCase(), ...serializeProfile(row) };
}
