"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

type Mode = "register" | "login";

const inputClass =
  "min-h-12 w-full rounded-xl border border-white/10 bg-white/[.04] px-4 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid";
const submitClass =
  "inline-flex min-h-12 items-center rounded-full bg-acid px-6 text-sm font-bold text-ink transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";

export function AccountAuthPanel() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("register");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const response = await fetch(`/api/network/v1/accounts/${mode}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          email: String(form.get("email") ?? ""),
          password: String(form.get("password") ?? ""),
          ...(mode === "register" ? { handle: String(form.get("handle") ?? "") } : {}),
        }),
      });
      const body = (await response.json().catch(() => null)) as { ok?: boolean; message?: string } | null;
      if (response.ok && body?.ok === true) {
        router.refresh();
        router.push("/network");
        return;
      }
      setError(body?.message ?? "That did not work. Please try again.");
    } catch {
      setError("The network is unreachable right now. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="max-w-xl rounded-3xl border border-white/10 bg-white/[.03] p-8" data-testid="account-auth-panel">
      <div role="tablist" aria-label="Sign in or create an account" className="mb-6 flex gap-2">
        {(["register", "login"] as const).map((candidate) => (
          <button
            key={candidate}
            type="button"
            role="tab"
            aria-selected={mode === candidate}
            onClick={() => { setMode(candidate); setError(null); }}
            className={
              "min-h-11 rounded-full px-4 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid " +
              (mode === candidate ? "bg-white/10 text-white" : "text-mist hover:bg-white/[.06] hover:text-white")
            }
          >
            {candidate === "register" ? "Create account" : "Sign in"}
          </button>
        ))}
      </div>
      <form onSubmit={submit} noValidate={false}>
        {mode === "register" ? (
          <div className="mb-4">
            <label htmlFor="account-handle" className="mb-1 block text-sm font-medium text-white">
              Handle
            </label>
            <input
              id="account-handle"
              name="handle"
              type="text"
              autoComplete="username"
              required
              minLength={3}
              maxLength={30}
              pattern="[a-zA-Z0-9][a-zA-Z0-9-]{1,28}[a-zA-Z0-9]"
              placeholder="ada-lovelace"
              className={inputClass}
            />
            <p className="mt-1 text-xs text-mist">Lowercase letters, digits, hyphens. This is your public address once you publish.</p>
          </div>
        ) : null}
        <div className="mb-4">
          <label htmlFor="account-email" className="mb-1 block text-sm font-medium text-white">
            Email
          </label>
          <input id="account-email" name="email" type="email" autoComplete="email" required className={inputClass} />
        </div>
        <div className="mb-6">
          <label htmlFor="account-password" className="mb-1 block text-sm font-medium text-white">
            Password
          </label>
          <input
            id="account-password"
            name="password"
            type="password"
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            required
            minLength={10}
            maxLength={200}
            className={inputClass}
          />
          {mode === "register" ? (
            <p className="mt-1 text-xs text-mist">At least 10 characters. Stored only as a salted scrypt hash.</p>
          ) : null}
        </div>
        <div aria-live="polite">
          {error !== null ? (
            <p role="alert" className="mb-4 rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200" data-testid="account-error">
              {error}
            </p>
          ) : null}
        </div>
        <button type="submit" disabled={pending} className={submitClass} data-testid="account-submit">
          {pending ? "Working…" : mode === "register" ? "Create account" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export function AccountSignOut() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    if (pending) return;
    setPending(true);
    try {
      await fetch("/api/network/v1/accounts/logout", { method: "POST" });
      router.refresh();
      router.push("/");
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="mt-6 border-t border-white/10 pt-6"
      onSubmit={(event) => { event.preventDefault(); void signOut(); }}
    >
      <button type="submit" disabled={pending} className="text-sm font-semibold text-mist underline-offset-4 hover:text-white hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid" data-testid="account-signout">
        {pending ? "Signing out…" : "Sign out"}
      </button>
    </form>
  );
}
