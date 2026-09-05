"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

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

export function NetworkDashboard({ handle }: { handle: string }) {
  const router = useRouter();
  const [profile, setProfile] = useState<ProfileState>(undefined as unknown as ProfileState);
  const [markdown, setMarkdown] = useState<string>("");
  const [status, setStatus] = useState<{ kind: "info" | "error" | "success"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [grants, setGrants] = useState<Array<{ id: string; name: string; tokenPrefix: string; scopes: string[]; revokedAt: string | null; expiresAt: string | null }>>([]);
  const [newGrantToken, setNewGrantToken] = useState<string | null>(null);
  const [newGrantName, setNewGrantName] = useState("");

  const loadProfile = useCallback(async () => {
    const response = await fetch("/api/network/v1/profile");
    const body = (await response.json()) as { profile: ProfileState };
    setProfile(body.profile);
    setMarkdown(body.profile?.markdown ?? "");
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
    if (busy) return;
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
      const body = (await response.json()) as { ok?: boolean; etag?: string; message?: string };
      if (response.ok && body.ok === true && profile !== null) {
        setProfile({ ...profile, markdown, etag: body.etag! });
        setStatus({ kind: "success", message: "Profile saved. It is private until you publish it." });
      } else {
        setStatus({ kind: "error", message: body.message ?? "Save failed." });
        if (response.status === 412) await loadProfile();
      }
    } catch {
      setStatus({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  async function setVisibility(publish: boolean): Promise<void> {
    if (busy) return;
    setBusy(true);
    setStatus(null);
    try {
      const response = await fetch(`/api/network/v1/profile/${publish ? "publish" : "unpublish"}`, { method: "POST" });
      const body = (await response.json()) as { ok?: boolean; message?: string };
      if (response.ok && body.ok === true) {
        await loadProfile();
        router.refresh();
        setStatus({ kind: "success", message: publish ? "Profile published. It is now discoverable." : "Profile unpublished. Only you can see it again." });
      } else {
        setStatus({ kind: "error", message: body.message ?? "Could not change visibility." });
      }
    } catch {
      setStatus({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  async function createGrant(): Promise<void> {
    if (busy || newGrantName.trim() === "") return;
    setBusy(true);
    setStatus(null);
    setNewGrantToken(null);
    try {
      const response = await fetch("/api/network/v1/agent-grants", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: newGrantName.trim(), scopes: ["profile:read", "profile:write"] }),
      });
      const body = (await response.json()) as { ok?: boolean; token?: string; message?: string };
      if (response.ok && body.ok === true) {
        setNewGrantToken(body.token!);
        setNewGrantName("");
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
    try {
      await fetch(`/api/network/v1/agent-grants/${id}`, { method: "DELETE" });
      await loadGrants();
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
          onChange={(event) => setMarkdown(event.target.value)}
          spellCheck={false}
          placeholder={"---\nschema: connect.md/profile\nhandle: your-handle\nname: Your Name\nheadline: Your headline\n---\n\nWrite your profile here."}
        />
        <div className="mt-4 flex flex-wrap gap-3">
          <button type="button" className={primaryButton} onClick={() => void saveProfile()} disabled={busy} data-testid="profile-save">
            {busy ? "Working…" : "Save profile"}
          </button>
          {published ? (
            <button type="button" className={secondaryButton} onClick={() => void setVisibility(false)} disabled={busy} data-testid="profile-unpublish">
              Unpublish
            </button>
          ) : (
            <button type="button" className={secondaryButton} onClick={() => void setVisibility(true)} disabled={busy || profile === null} data-testid="profile-publish">
              Publish
            </button>
          )}
          {published ? (
            <a href={`/p/${handle}`} className={secondaryButton} data-testid="profile-public-link">
              View public page
            </a>
          ) : null}
        </div>
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
            Issue scoped grants to your agents. Agents can read or write this
            profile only within their scope, never send contact requests, and
            never message anyone. Tokens are shown once.
          </p>
          <div className="mt-4 flex gap-2">
            <label htmlFor="new-grant-name" className="sr-only">Grant name</label>
            <input
              id="new-grant-name"
              value={newGrantName}
              onChange={(event) => setNewGrantName(event.target.value)}
              placeholder="Grant name, e.g. profile-agent"
              className="min-h-11 w-full rounded-xl border border-white/10 bg-white/[.04] px-4 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
            />
            <button type="button" className={primaryButton} onClick={() => void createGrant()} disabled={busy || newGrantName.trim() === ""} data-testid="grant-create">
              Create
            </button>
          </div>
          {newGrantToken !== null ? (
            <div className="mt-4 rounded-xl border border-acid/40 bg-acid/10 p-4" data-testid="grant-token-shown">
              <p className="text-xs font-semibold uppercase tracking-wide text-acid">Token — shown once, copy it now</p>
              <code className="mt-2 block break-all font-mono text-sm text-white">{newGrantToken}</code>
            </div>
          ) : null}
          <ul className="mt-4 grid gap-2" data-testid="grant-list">
            {grants.map((grant) => (
              <li key={grant.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/[.02] px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-white">
                    {grant.name} {grant.revokedAt !== null ? <span className="ml-1 text-xs font-normal text-mist">(revoked)</span> : null}
                  </p>
                  <p className="font-mono text-xs text-mist">
                    {grant.tokenPrefix} · {grant.scopes.join(", ")}
                    {grant.expiresAt !== null ? ` · expires ${grant.expiresAt.slice(0, 10)}` : ""}
                  </p>
                </div>
                {grant.revokedAt === null ? (
                  <button type="button" className="text-sm font-semibold text-red-300 underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid" onClick={() => void revokeGrant(grant.id)}>
                    Revoke
                  </button>
                ) : null}
              </li>
            ))}
            {grants.length === 0 ? <li className="text-sm text-mist">No agent grants yet.</li> : null}
          </ul>
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
