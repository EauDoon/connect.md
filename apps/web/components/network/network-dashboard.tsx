"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  DEFAULT_GRANT_LIFETIME_DAYS,
  DEFAULT_GRANT_SCOPES,
  GRANT_LIFETIME_DAYS,
  GRANT_SCOPE_OPTIONS,
  grantDisplayState,
  grantRequestBody,
  type GrantLifetimeDays,
  type GrantScope,
} from "@/lib/network/grant-form";

type ProfileState = {
  markdown: string;
  etag: string;
  visibility: "private" | "public";
  publishedAt: string | null;
  updatedAt: string;
} | null;

const textareaClass =
  "min-h-[24rem] w-full rounded-2xl border border-white/10 bg-white/[.04] p-4 font-mono text-sm leading-6 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid";
const primaryButton =
  "inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";
const secondaryButton =
  "inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white transition hover:bg-white/[.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";

export type GrantSummary = {
  id: string;
  name: string;
  tokenPrefix: string;
  scopes: string[];
  createdAt?: string;
  revokedAt: string | null;
  expiresAt: string | null;
  lastUsedAt?: string | null;
};

const AGENT_API_REFERENCE = "https://github.com/EauDoon/connect.md/blob/main/docs/network-agent-api.md";

/** The owner's grants with their state. Only active grants can be revoked; expired ones already deny access. */
export function GrantList({ grants, busy, onRevoke, now = new Date() }: { grants: GrantSummary[]; busy: boolean; onRevoke: (id: string) => void; now?: Date }) {
  return (
    <ul className="mt-4 grid gap-2" data-testid="grant-list">
      {grants.map((grant) => {
        const state = grantDisplayState(grant, now);
        return (
          <li key={grant.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/[.02] px-4 py-3" data-grant-state={state}>
            <div className="min-w-0">
              <p className="break-words text-sm font-semibold text-white">
                {grant.name} <span className={`ml-1 text-xs font-normal ${state === "active" ? "text-acid" : "text-mist"}`}>({state})</span>
              </p>
              <p className="break-words font-mono text-xs text-mist">
                {grant.tokenPrefix} · {grant.scopes.join(", ")}
                {grant.expiresAt !== null ? ` · ${state === "expired" ? "expired" : "expires"} ${grant.expiresAt.slice(0, 10)}` : " · no expiry"}
              </p>
              <p className="text-xs text-mist">
                {grant.createdAt ? `Created ${grant.createdAt.slice(0, 10)} · ` : ""}
                {grant.lastUsedAt ? `last used ${grant.lastUsedAt.slice(0, 10)}` : "never used"}
              </p>
            </div>
            {state === "active" ? (
              <button type="button" disabled={busy} className="text-sm font-semibold text-red-300 underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid" onClick={() => onRevoke(grant.id)}>
                Revoke
              </button>
            ) : null}
          </li>
        );
      })}
      {grants.length === 0 ? <li className="text-sm text-mist">No agent grants yet.</li> : null}
    </ul>
  );
}

export function NetworkDashboard({ handle }: { handle: string }) {
  const router = useRouter();
  const [profile, setProfile] = useState<ProfileState | undefined>(undefined);
  const [conflict, setConflict] = useState(false);
  const [markdown, setMarkdown] = useState<string>("");
  const [status, setStatus] = useState<{ kind: "info" | "error" | "success"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [grants, setGrants] = useState<GrantSummary[]>([]);
  const [newGrantToken, setNewGrantToken] = useState<string | null>(null);
  const [newGrantExpiresAt, setNewGrantExpiresAt] = useState<string | null>(null);
  const [newGrantName, setNewGrantName] = useState("");
  const [newGrantScopes, setNewGrantScopes] = useState<readonly GrantScope[]>(DEFAULT_GRANT_SCOPES);
  const [newGrantLifetime, setNewGrantLifetime] = useState<GrantLifetimeDays>(DEFAULT_GRANT_LIFETIME_DAYS);
  const canCreateGrant = !busy && newGrantName.trim() !== "" && newGrantScopes.length > 0;

  const loadProfile = useCallback(async () => {
    const response = await fetch("/api/network/v1/profile");
    if (!response.ok) throw new Error("Profile unavailable");
    const body = (await response.json()) as { profile: ProfileState };
    setProfile(body.profile);
    setMarkdown(body.profile?.markdown ?? "");
    setConflict(false);
  }, []);

  const loadGrants = useCallback(async () => {
    const response = await fetch("/api/network/v1/agent-grants");
    if (response.ok) {
      const body = (await response.json()) as { grants: typeof grants };
      setGrants(body.grants);
    }
  }, []);

  useEffect(() => {
    void loadProfile().catch(() => setStatus({ kind: "error", message: "Could not load your profile. The network may be unavailable." }));
    void loadGrants().catch(() => undefined);
  }, [loadGrants, loadProfile]);

  async function saveProfile(): Promise<void> {
    if (busy || profile === undefined || profile?.visibility === "public") return;
    setBusy(true);
    setStatus(null);
    try {
      const response = await fetch("/api/network/v1/profile", {
        method: "PUT",
        headers: {
          "content-type": "application/json",
          ...(profile !== null ? { "if-match": profile.etag } : {}),
        },
        body: JSON.stringify({ markdown }),
      });
      const body = (await response.json()) as { ok?: boolean; profile?: Exclude<ProfileState, null>; message?: string };
      if (response.ok && body.ok === true && body.profile !== undefined) {
        setProfile(body.profile);
        setConflict(false);
        setStatus({ kind: "success", message: "Profile saved. It is private until you publish it." });
      } else {
        setStatus({ kind: "error", message: body.message ?? "Save failed." });
        if (response.status === 412) setConflict(true);
      }
    } catch {
      setStatus({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  async function setVisibility(publish: boolean): Promise<void> {
    if (busy || !profile || (publish && (conflict || markdown !== profile.markdown))) return;
    setBusy(true);
    setStatus(null);
    try {
      const response = await fetch(`/api/network/v1/profile/${publish ? "publish" : "unpublish"}`, {
        method: "POST",
        headers: publish ? { "if-match": profile.etag } : {},
      });
      const body = (await response.json()) as { ok?: boolean; message?: string };
      if (response.ok && body.ok === true) {
        await loadProfile();
        router.refresh();
        setStatus({ kind: "success", message: publish ? "Profile published. It is now discoverable." : "Profile unpublished. Only you can see it again." });
      } else {
        if (response.status === 412) setConflict(true);
        setStatus({ kind: "error", message: body.message ?? "Could not change visibility." });
      }
    } catch {
      setStatus({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  async function createGrant(): Promise<void> {
    if (!canCreateGrant) return;
    setBusy(true);
    setStatus(null);
    setNewGrantToken(null);
    setNewGrantExpiresAt(null);
    try {
      const response = await fetch("/api/network/v1/agent-grants", {
        method: "POST",
        headers: { "content-type": "application/json" },
        // The expiry is computed here, so a server 400 (for example a clock
        // running ahead past the one-year bound) keeps the form for a retry.
        body: JSON.stringify(grantRequestBody(newGrantName, newGrantScopes, newGrantLifetime)),
      });
      const body = (await response.json()) as { ok?: boolean; token?: string; message?: string; grant?: { expiresAt?: string | null } };
      if (response.ok && body.ok === true) {
        setNewGrantToken(body.token!);
        setNewGrantExpiresAt(typeof body.grant?.expiresAt === "string" ? body.grant.expiresAt : null);
        setNewGrantName("");
        setNewGrantScopes(DEFAULT_GRANT_SCOPES);
        setNewGrantLifetime(DEFAULT_GRANT_LIFETIME_DAYS);
        await loadGrants();
      } else {
        setStatus({ kind: "error", message: body.message ?? "Could not create the grant." });
      }
    } catch {
      setStatus({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  async function revokeGrant(id: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    setStatus(null);
    try {
      const response = await fetch(`/api/network/v1/agent-grants/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Grant revocation failed");
      await loadGrants();
      setStatus({ kind: "success", message: "Agent grant revoked." });
    } catch {
      setStatus({ kind: "error", message: "Could not confirm revocation. The agent may still have access. Try again." });
    } finally {
      setBusy(false);
    }
  }

  const published = profile?.visibility === "public";

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <div className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-8" data-testid="profile-editor">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl font-semibold text-white">Your profile</h2>
          <span
            className="rounded-full border px-3 py-1 text-xs font-semibold"
            data-testid="profile-visibility"
            style={{
              borderColor: published ? "rgba(216,255,114,.5)" : "rgba(255,255,255,.15)",
              color: published ? "rgb(216,255,114)" : "rgb(176,182,191)",
            }}
          >
            {profile === null ? "not saved" : published ? "published" : "private"}
          </span>
        </div>
        <label htmlFor="profile-markdown" className="mb-1 block text-sm font-medium text-white">
          Profile Markdown
        </label>
        <textarea
          id="profile-markdown"
          className={textareaClass}
          value={markdown}
          disabled={busy || profile === undefined || published}
          onChange={(event) => setMarkdown(event.target.value)}
          spellCheck={false}
          placeholder={"---\nschema: connect.md/profile\nhandle: your-handle\nname: Your Name\nheadline: Your headline\n---\n\nWrite your profile here."}
        />
        <div className="mt-4 flex flex-wrap gap-3">
          <button type="button" className={primaryButton} onClick={() => void saveProfile()} disabled={busy || profile === undefined || published || conflict} data-testid="profile-save">
            {busy ? "Working…" : "Save profile"}
          </button>
          {published ? (
            <button type="button" className={secondaryButton} onClick={() => void setVisibility(false)} disabled={busy} data-testid="profile-unpublish">
              Unpublish
            </button>
          ) : (
            <button type="button" className={secondaryButton} onClick={() => void setVisibility(true)} disabled={busy || !profile || conflict || markdown !== profile.markdown} data-testid="profile-publish">
              Publish
            </button>
          )}
          {published ? (
            <a href={`/p/${handle}`} className={secondaryButton} data-testid="profile-public-link">
              View public page
            </a>
          ) : null}
        </div>
        {published ? <p className="mt-3 text-sm text-mist">Unpublish before editing. Publish again when you are ready to share the saved revision.</p> : null}
        {conflict ? (
          <div className="mt-3 text-sm text-mist">
            <p>Your edits are still in the editor. Copy them before replacing them with the saved version.</p>
            <button type="button" className={secondaryButton} disabled={busy} onClick={() => void loadProfile().catch(() => setStatus({ kind: "error", message: "Could not reload your profile." }))}>
              Discard edits and reload saved profile
            </button>
          </div>
        ) : null}
        <div aria-live="polite" className="mt-4">
          {status !== null ? (
            <p
              role="status"
              data-testid="network-status"
              className={
                "rounded-xl border px-4 py-3 text-sm " +
                (status.kind === "error"
                  ? "border-red-400/30 bg-red-400/10 text-red-200"
                  : status.kind === "success"
                    ? "border-acid/30 bg-acid/10 text-acid"
                    : "border-white/10 bg-white/[.04] text-mist")
              }
            >
              {status.message}
            </p>
          ) : null}
        </div>
      </div>

      <div className="grid gap-8">
        <div className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-8" data-testid="agent-grants">
          <h2 className="text-xl font-semibold text-white">Agent access</h2>
          <p className="mt-2 text-sm leading-6 text-mist">
            Issue scoped grants to your agents. An agent can act only within the
            scopes you tick: it can never send contact requests, decide them,
            message anyone, or issue grants. Tokens are shown once.
          </p>
          <form className="mt-4 grid gap-4" onSubmit={(event) => { event.preventDefault(); void createGrant(); }}>
            <div className="flex gap-2">
              <label htmlFor="new-grant-name" className="sr-only">Grant name</label>
              <input
                id="new-grant-name"
                value={newGrantName}
                disabled={busy}
                maxLength={64}
                onChange={(event) => setNewGrantName(event.target.value)}
                placeholder="Grant name, e.g. profile-agent"
                className="min-h-11 w-full rounded-xl border border-white/10 bg-white/[.04] px-4 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
              />
              <button type="submit" className={primaryButton} disabled={!canCreateGrant} data-testid="grant-create">
                Create
              </button>
            </div>
            <fieldset className="grid gap-2" data-testid="grant-scopes">
              <legend className="text-sm font-medium text-white">Scopes</legend>
              {GRANT_SCOPE_OPTIONS.map((option) => (
                <label key={option.scope} className="flex min-h-11 items-start gap-3 text-sm text-white">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={newGrantScopes.includes(option.scope)}
                    disabled={busy}
                    onChange={(event) => setNewGrantScopes((current) => event.target.checked ? [...current, option.scope] : current.filter((scope) => scope !== option.scope))}
                  />
                  <span>
                    {option.label} <code className="font-mono text-xs text-mist">{option.scope}</code>
                    <span className="block text-xs text-mist">{option.description}</span>
                  </span>
                </label>
              ))}
              {newGrantScopes.length === 0 ? <p className="text-xs text-amber-100" data-testid="grant-scope-required">Choose at least one scope.</p> : null}
            </fieldset>
            <label className="grid gap-1 text-sm font-medium text-white" htmlFor="new-grant-lifetime">
              Lifetime
              <select
                id="new-grant-lifetime"
                value={newGrantLifetime}
                disabled={busy}
                onChange={(event) => setNewGrantLifetime(Number(event.target.value) as GrantLifetimeDays)}
                className="min-h-11 rounded-xl border border-white/10 bg-ink px-3 text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
              >
                {GRANT_LIFETIME_DAYS.map((days) => <option key={days} value={days}>{days === 365 ? "1 year" : `${days} days`}</option>)}
              </select>
            </label>
          </form>
          {newGrantToken !== null ? (
            <div className="mt-4 rounded-xl border border-acid/40 bg-acid/10 p-4" data-testid="grant-token-shown">
              <p className="text-xs font-semibold uppercase tracking-wide text-acid">Token — shown once, copy it now</p>
              <code className="mt-2 block break-all font-mono text-sm text-white">{newGrantToken}</code>
              <p className="mt-2 text-xs text-mist" data-testid="grant-token-expiry">
                {newGrantExpiresAt !== null
                  ? `Expires ${newGrantExpiresAt.slice(0, 10)} (UTC). Revoke it here at any time before then.`
                  : "Revoke it here at any time."}
              </p>
            </div>
          ) : null}
          <GrantList grants={grants} busy={busy} onRevoke={(id) => void revokeGrant(id)} />
          <p className="mt-4 text-xs leading-5 text-mist">
            Agents call the network with the token as a bearer credential. See the{" "}
            <a href={AGENT_API_REFERENCE} className="font-semibold text-acid underline-offset-4 hover:underline" rel="noreferrer">agent API reference</a>{" "}
            for endpoints, scopes, and errors.
          </p>
        </div>

        <div className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
          <h2 className="text-xl font-semibold text-white">Shortcuts</h2>
          <div className="mt-4 grid gap-2">
            <a href="/discover" className="text-sm font-semibold text-acid underline-offset-4 hover:underline">Discover published profiles →</a>
            <a href="/inbox" className="text-sm font-semibold text-acid underline-offset-4 hover:underline">Contact requests and conversations →</a>
            <a href="/trust" className="text-sm font-semibold text-mist underline-offset-4 hover:underline">Privacy and data →</a>
          </div>
        </div>
      </div>
    </div>
  );
}
