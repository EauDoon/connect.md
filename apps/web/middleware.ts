import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

export const BOUNDED_NOT_FOUND_BODY = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"robots\" content=\"noindex, nofollow\"><title>Not found</title></head><body><p>Not found</p></body></html>";

export default function middleware(_request: NextRequest, _event: NextFetchEvent) {
  return new NextResponse(BOUNDED_NOT_FOUND_BODY, {
    status: 404,
    headers: {
      "Cache-Control": "private, no-store, max-age=0",
      "Content-Type": "text/html; charset=utf-8",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
}

export const config = {
  matcher: [
    "/account/:path*",
    "/agent-directory/:path*",
    "/agents/:path*",
    "/appeal-review/:path*",
    "/applications/:path*",
    "/discover/:path*",
    "/employer/:path*",
    "/feed/:path*",
    "/inbox/:path*",
    "/jobs/:path*",
    "/messages/:path*",
    "/moderation/:path*",
    "/moderation-review/:path*",
    "/network/:path*",
    "/organizations/:path*",
    "/p/:path*",
    "/posts/:path*",
    "/r/:path*",
    "/representatives/:path*",
    "/search/:path*",
    "/verification-review/:path*",
    "/workspace/:path*",
  ],
};
