import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

import { privateRouteAuthConfigured } from "@/lib/private-route-auth";

export { privateRouteAuthConfigured } from "@/lib/private-route-auth";

/**
 * This gate proves only that a current human session exists before Next.js
 * returns a private route shell. Every resource and staff-role decision stays
 * with the API; signing in never establishes moderator, reviewer, employer,
 * owner, recipient, or agent authority.
 */
const configuredPrivateRouteMiddleware = clerkMiddleware(async (auth) => {
  await auth.protect({ token: "session_token" });
});

export const BOUNDED_NOT_FOUND_BODY = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"robots\" content=\"noindex, nofollow\"><title>Not found</title></head><body><p>Not found</p></body></html>";

function unavailableResponse() {
  return new NextResponse(BOUNDED_NOT_FOUND_BODY, {
    status: 404,
    headers: {
      "Cache-Control": "private, no-store, max-age=0",
      "Content-Type": "text/html; charset=utf-8",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
}

function isRecruitingRoute(pathname: string) {
  return pathname === "/organizations"
    || pathname.startsWith("/organizations/")
    || pathname === "/jobs"
    || pathname.startsWith("/jobs/");
}

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  if (isRecruitingRoute(request.nextUrl.pathname)) {
    if (process.env.CONNECTMD_RECRUITING_ENABLED !== "true") return unavailableResponse();
    return NextResponse.next();
  }
  if (!privateRouteAuthConfigured(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
    process.env.CLERK_SECRET_KEY,
  )) {
    return unavailableResponse();
  }
  return configuredPrivateRouteMiddleware(request, event);
}

/**
 * Keep this an explicit private-route allowlist. Routes omitted here are
 * intentionally public, including anonymous local-first editing at /human and
 * /md, public discovery, and public Agent Identity pages at /agents/[handle].
 */
export const config = {
  matcher: [
    "/account/:path*",
    "/agents",
    "/appeal-review/:path*",
    "/applications/:path*",
    "/employer/:path*",
    "/feed/:path*",
    "/inbox/:path*",
    "/messages/:path*",
    "/moderation/:path*",
    "/moderation-review/:path*",
    "/network/:path*",
    "/organizations/:path*",
    "/jobs/:path*",
    "/verification-review/:path*",
    "/workspace/:path*",
  ],
};
