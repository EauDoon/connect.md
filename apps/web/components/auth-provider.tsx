"use client";

import { ClerkProvider, useAuth } from "@clerk/nextjs";
import { createContext, type ReactNode, useContext, useMemo } from "react";

import { type TokenGetter } from "@/lib/api";
import { publicAuthConfigured } from "@/lib/public-auth-config";

type AuthState = {
  configured: boolean;
  isLoaded: boolean;
  isSignedIn: boolean;
  subject: string | null;
  getToken: TokenGetter;
};

const AuthContext = createContext<AuthState>({
  configured: false,
  isLoaded: true,
  isSignedIn: false,
  subject: null,
  getToken: async () => null
});

function ClerkBridge({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, userId, getToken } = useAuth();
  const value = useMemo<AuthState>(() => ({
    configured: true,
    isLoaded,
    isSignedIn: Boolean(isSignedIn),
    subject: userId ?? null,
    getToken: async () => getToken()
  }), [getToken, isLoaded, isSignedIn, userId]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function ConnectmdAuthProvider({ children }: { children: ReactNode }) {
  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  if (!publicAuthConfigured(publishableKey)) return <AuthContext.Provider value={{ configured: false, isLoaded: true, isSignedIn: false, subject: null, getToken: async () => null }}>{children}</AuthContext.Provider>;

  return (
    <ClerkProvider publishableKey={publishableKey}>
      <ClerkBridge>{children}</ClerkBridge>
    </ClerkProvider>
  );
}

export function useConnectmdAuth() {
  return useContext(AuthContext);
}
