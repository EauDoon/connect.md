"use client";

import { type ReactNode } from "react";

import { ConnectmdAuthProvider } from "@/components/auth-provider";
import { DraftProvider } from "@/components/draft-provider";

export function Providers({ children }: { children: ReactNode }) {
  return <ConnectmdAuthProvider><DraftProvider>{children}</DraftProvider></ConnectmdAuthProvider>;
}
