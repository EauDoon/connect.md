"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

const inputClass =
  "min-h-12 w-full rounded-xl border border-white/10 bg-white/[.04] px-4 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid";
const primaryButton =
  "inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";
const secondaryButton =
  "inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white transition hover:bg-white/[.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";

type Notice = { kind: "success" | "error"; message: string } | null;

function sessions(count: number) {
  return `${count} other session${count === 1 ? "" : "s"}`;
}

/** True once a confirmation has been typed that differs from the new password. */
export function confirmMismatch(next: string, confirm: string) {
  return confirm !== "" && next !== confirm;
}

export function otherSessionsLine(count: number) {
  return count === 0 ? "No other browser is signed in to this account." : `Signed in on ${sessions(count)}.`;
}

/**
 * Signed-in account security: change the password (which signs out every other
 * browser) and sign out other sessions. Agent grants are separate credentials
 * managed under Agent access on /network.
 */
export function AccountSecurityPanel({ otherSessions }: { otherSessions: number }) {
  const router = useRouter();
  const [pending, setPending] = useState<"password" | "sessions" | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const mismatch = confirmMismatch(next, confirm);

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending !== null) return;
    if (next !== confirm) {
      setNotice({ kind: "error", message: "The new password and its confirmation do not match." });
      return;
    }
    setPending("password");
    setNotice(null);
    try {
      const response = await fetch("/api/network/v1/accounts/password", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ currentPassword: current, newPassword: next }),
      });
      const body = (await response.json().catch(() => null)) as { ok?: boolean; message?: string; revokedSessions?: number } | null;
      if (response.ok && body?.ok === true) {
        setCurrent(""); setNext(""); setConfirm("");
        const revoked = typeof body.revokedSessions === "number" ? body.revokedSessions : 0;
        setNotice({ kind: "success", message: `Password changed. ${revoked === 0 ? "No other session was signed in." : `Signed out ${sessions(revoked)}.`} This browser stays signed in.` });
        router.refresh();
        return;
      }
      setNotice({ kind: "error", message: body?.message ?? "The password was not changed. Try again." });
    } catch {
      setNotice({ kind: "error", message: "The network is unreachable right now. The password was not changed." });
    } finally {
      setPending(null);
    }
  }

  async function signOutOthers() {
    if (pending !== null) return;
    setPending("sessions");
    setNotice(null);
    try {
      const response = await fetch("/api/network/v1/sessions/revoke-others", { method: "POST" });
      const body = (await response.json().catch(() => null)) as { ok?: boolean; revoked?: number } | null;
      if (!response.ok || body?.ok !== true) throw new Error("Revocation failed");
      const revoked = typeof body.revoked === "number" ? body.revoked : 0;
      setNotice({ kind: "success", message: revoked === 0 ? "No other session was signed in." : `Signed out ${sessions(revoked)}.` });
      router.refresh();
    } catch {
      setNotice({ kind: "error", message: "Could not confirm that other sessions were signed out. They may still be active. Try again." });
    } finally {
      setPending(null);
    }
  }

  return (
    <section aria-labelledby="account-security-title" className="mt-8 border-t border-white/10 pt-6" data-testid="account-security">
      <h3 id="account-security-title" className="text-xl font-semibold text-white">Security</h3>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-mist">
        Changing your password signs out every other browser. Agent grants are separate credentials: review or revoke them under Agent access on your network page.
      </p>
      <form className="mt-5 grid max-w-xl gap-4" onSubmit={(event) => void changePassword(event)}>
        <div>
          <label htmlFor="security-current-password" className="mb-1 block text-sm font-medium text-white">Current password</label>
          <input id="security-current-password" type="password" autoComplete="current-password" required maxLength={200} value={current} disabled={pending !== null} onChange={(event) => setCurrent(event.target.value)} className={inputClass} />
        </div>
        <div>
          <label htmlFor="security-new-password" className="mb-1 block text-sm font-medium text-white">New password</label>
          <input id="security-new-password" type="password" autoComplete="new-password" required minLength={10} maxLength={200} aria-describedby="security-new-password-help" value={next} disabled={pending !== null} onChange={(event) => setNext(event.target.value)} className={inputClass} />
          <p id="security-new-password-help" className="mt-1 text-xs text-mist">At least 10 characters, mixing two of lowercase, uppercase, digits, and symbols.</p>
        </div>
        <div>
          <label htmlFor="security-confirm-password" className="mb-1 block text-sm font-medium text-white">Confirm new password</label>
          <input id="security-confirm-password" type="password" autoComplete="new-password" required maxLength={200} aria-invalid={mismatch} aria-describedby={mismatch ? "security-confirm-mismatch" : undefined} value={confirm} disabled={pending !== null} onChange={(event) => setConfirm(event.target.value)} className={inputClass} />
          {mismatch ? <p id="security-confirm-mismatch" className="mt-1 text-xs text-red-200">Does not match the new password.</p> : null}
        </div>
        <div>
          <button type="submit" disabled={pending !== null} className={primaryButton} data-testid="security-change-password">
            {pending === "password" ? "Changing…" : "Change password"}
          </button>
        </div>
      </form>
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <p className="text-sm text-mist" data-testid="security-other-sessions">{otherSessionsLine(otherSessions)}</p>
        <button type="button" disabled={pending !== null} onClick={() => void signOutOthers()} className={secondaryButton} data-testid="security-revoke-others">
          {pending === "sessions" ? "Signing out…" : "Sign out other sessions"}
        </button>
      </div>
      <div aria-live="polite" className="mt-4">
        {notice !== null ? (
          <p role={notice.kind === "error" ? "alert" : "status"} data-testid="security-notice" className={`rounded-xl border px-4 py-3 text-sm ${notice.kind === "error" ? "border-red-400/30 bg-red-400/10 text-red-200" : "border-acid/30 bg-acid/10 text-acid"}`}>
            {notice.message}
          </p>
        ) : null}
      </div>
    </section>
  );
}
