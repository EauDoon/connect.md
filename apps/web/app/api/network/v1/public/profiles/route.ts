import { listPublishedProfiles } from "@/lib/network/profiles";
import { jsonResponse, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const prefix = new URL(request.url).searchParams.get("prefix");
    const profiles = await listPublishedProfiles(database(), {
      prefix: prefix === null || prefix.trim() === "" ? undefined : prefix.trim(),
    });
    return jsonResponse(
      { ok: true, profiles: profiles.map((profile) => ({ handle: profile.handle, publishedAt: profile.publishedAt })) },
      200,
      { "x-connectmd-network": "v1" },
    );
  });
}
