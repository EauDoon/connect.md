import { notFound } from "next/navigation";
import { cache } from "react";

import { getPublishedProfile, ProfileError } from "@/lib/network/profiles";
import { database, networkDatabaseConfigured } from "@/lib/network/db";
import { normalizeHandleLookup } from "@/lib/network/identity";

/**
 * Decode a route param that Next may or may not have decoded already. The
 * handle alphabet has no "%", so a second decode of a valid handle is a no-op
 * and anything undecodable is simply not a handle.
 */
function decodeHandleParam(raw: string): string | null {
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

/**
 * The one published-profile lookup for a /p/{handle} request, shared by the
 * segment layout, generateMetadata, and the page (React cache keeps it to one
 * query). Anything that is not an explicitly published profile is a real
 * not-found: an impossible handle, an unconfigured network, and a private,
 * unpublished, or missing profile all look the same from outside.
 */
export const loadPublishedProfile = cache(async (rawHandle: string) => {
  const decoded = decodeHandleParam(rawHandle);
  const handle = decoded === null ? null : normalizeHandleLookup(decoded);
  if (handle === null || !networkDatabaseConfigured()) notFound();
  try {
    return await getPublishedProfile(database(), handle);
  } catch (error) {
    if (error instanceof ProfileError && error.code === "not-found") notFound();
    throw error;
  }
});
