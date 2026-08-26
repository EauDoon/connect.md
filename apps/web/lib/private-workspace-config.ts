import "server-only";

import { privateRouteAuthConfigured } from "@/lib/private-route-auth";

export function privateWorkspaceConfiguredFromEnvironment(): boolean {
  return privateRouteAuthConfigured(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
    process.env.CLERK_SECRET_KEY,
  );
}
