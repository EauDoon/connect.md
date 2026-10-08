"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

export type AuthMode = "register" | "login";
type Mode = AuthMode;

const AUTH_MODES: readonly AuthMode[] = ["register", "login"];

/**
 * The browser-side mirror of the server handle rule (lib/network/identity.ts):
 * 3 to 30 lowercase letters, digits, or hyphens, starting and ending with a
 * letter or digit, with no consecutive hyphens. Browsers compile `pattern`
 * with the RegExp v flag, where an unescaped "-" in a character class is a
 * syntax error that silently disables the check, so the hyphen is escaped.
 */
export const HANDLE_INPUT_PATTERN = String.raw`(?!.*--)[a-z0-9][a-z0-9\-]{1,28}[a-z0-9]`;

/** WAI-ARIA tabs keyboard model: arrows wrap, Home and End jump to the ends. */
export function nextAuthMode(mode: AuthMode, key: string): AuthMode | null {
  const index = AUTH_MODES.indexOf(mode);
  switch (key) {
    case "ArrowRight": return AUTH_MODES[(index + 1) % AUTH_MODES.length]!;
    case "ArrowLeft": return AUTH_MODES[(index - 1 + AUTH_MODES.length) % AUTH_MODES.length]!;
    case "Home": return AUTH_MODES[0]!;
    case "End": return AUTH_MODES[AUTH_MODES.length - 1]!;
    default: return null;
  }
}

const inputClass =
  "min-h-12 w-full rounded-xl border border-white/10 bg-white/[.04] px-4 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid";
const submitClass =
  "inline-flex min-h-12 items-center rounded-full bg-acid px-6 text-sm font-bold text-ink transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";

export function AccountAuthPanel() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("register");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tabs = useRef<Partial<Record<Mode, HTMLButtonElement | null>>>({});

  function selectMode(next: Mode) {
    setMode(next);
    setError(null);
  }

  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const next = nextAuthMode(mode, event.key);
    if (next === null) return;
    event.preventDefault();
    selectMode(next);
    tabs.current[next]?.focus();
  }

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
        {AUTH_MODES.map((candidate) => (
          <button
            key={candidate}
            ref={(element) => { tabs.current[candidate] = element; }}
            id={`account-tab-${candidate}`}
            type="button"
            role="tab"
            aria-selected={mode === candidate}
            aria-controls="account-auth-form"
            tabIndex={mode === candidate ? 0 : -1}
            onClick={() => selectMode(candidate)}
            onKeyDown={onTabKeyDown}
            className={
              "min-h-11 rounded-full px-4 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid " +
              (mode === candidate ? "bg-white/10 text-white" : "text-mist hover:bg-white/[.06] hover:text-white")
            }
          >
            {candidate === "register" ? "Create account" : "Sign in"}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="account-auth-form" aria-labelledby={`account-tab-${mode}`}>
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
                autoCapitalize="none"
                spellCheck={false}
                required
                minLength={3}
                maxLength={30}
                pattern={HANDLE_INPUT_PATTERN}
                placeholder="ada-lovelace"
                aria-describedby="account-handle-help"
                className={inputClass}
              />
              <p id="account-handle-help" className="mt-1 text-xs text-mist">
                3 to 30 lowercase letters, digits, or single hyphens, starting and ending with a letter or digit. This is your public address once you publish.
              </p>
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
              aria-describedby={mode === "register" ? "account-password-help" : undefined}
              className={inputClass}
            />
            {mode === "register" ? (
              <p id="account-password-help" className="mt-1 text-xs text-mist">At least 10 characters. Stored only as a salted scrypt hash.</p>
            ) : null}
          </div>
          {error !== null ? (
            <p role="alert" className="mb-4 rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200" data-testid="account-error">
              {error}
            </p>
          ) : null}
          <button type="submit" disabled={pending} className={submitClass} data-testid="account-submit">
            {pending ? "Working…" : mode === "register" ? "Create account" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

export function AccountSignOut() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signOut() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/network/v1/accounts/logout", { method: "POST" });
      if (!response.ok) throw new Error("Sign out failed");
      router.refresh();
      router.push("/");
    } catch {
      setError("Could not confirm sign out. Your session may still be active. Try again.");
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
      {error !== null ? <p role="alert" className="mt-3 text-sm text-red-200">{error}</p> : null}
    </form>
  );
}
