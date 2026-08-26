"use client";

import { LoaderCircle, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiRequestError, type TokenGetter } from "@/lib/api";
import { getOrganizationVerificationStatus, presentRecruitmentError, type Organization, type OrganizationVerificationOwnerStatus } from "@/lib/recruitment-api";

type LoadState = "loading" | "loaded" | "error";
export const OWNER_VERIFICATION_STATUS_DENIED_MESSAGE = "Verification status is unavailable for this signed-in organization owner.";

export function OrganizationVerificationStatusCard({ organization, subject, getToken, isSubjectCurrent, refreshRevision }: { organization: Organization; subject: string; getToken: TokenGetter; isSubjectCurrent: (requestSubject: string) => boolean; refreshRevision: number }) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [status, setStatus] = useState<OrganizationVerificationOwnerStatus | null>(null);
  const [error, setError] = useState("");
  const mountedRef = useRef(false);
  const requestRef = useRef(0);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; requestRef.current += 1; }; }, []);
  const stillCurrent = useCallback((requestId: number) => organizationVerificationRequestIsCurrent(requestId, requestRef.current, mountedRef.current, isSubjectCurrent(subject)), [isSubjectCurrent, subject]);

  const load = useCallback(async () => {
    if (!mountedRef.current || !isSubjectCurrent(subject)) return;
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setLoadState("loading"); setError("");
    try {
      const next = await getOrganizationVerificationStatus(organization.slug, getToken, () => stillCurrent(requestId));
      if (!stillCurrent(requestId)) return;
      setStatus(next); setLoadState("loaded");
    } catch (cause) {
      if (!stillCurrent(requestId)) return;
      setStatus(null); setLoadState("error"); setError(presentOwnerVerificationStatusError(cause));
    }
  }, [getToken, isSubjectCurrent, organization.slug, stillCurrent, subject]);

  useEffect(() => { void load(); }, [load, refreshRevision]);

  return <section aria-labelledby="verification-status-title" className="rounded-[1.5rem] border border-white/10 bg-panel p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><ShieldCheck className="size-5 text-acid" aria-hidden /><h2 id="verification-status-title" className="text-xl font-semibold text-white">Recruiting-control status</h2></div><p className="mt-2 text-sm leading-6 text-mist">Owner-private service status only. A submission is not a decision, and this workspace cannot activate recruiting control or publish a job.</p></div><Button variant="secondary" disabled={loadState === "loading"} onClick={() => void load()}>{loadState === "loading" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <RefreshCw className="size-4" aria-hidden />} Refresh status</Button></div>{loadState === "loading" && <p role="status" className="mt-5 text-sm text-mist">Loading verification status…</p>}{loadState === "error" && <p role="alert" className="mt-5 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-3 text-sm leading-6 text-amber-100">{error}</p>}{loadState === "loaded" && status && <dl className="mt-5 grid gap-3 border-t border-white/10 pt-5 text-sm sm:grid-cols-2"><Fact label="Service state" value={status.state.replaceAll("_", " ")} /><Fact label="Submitted" value={formatTime(status.submittedAt)} /><Fact label="Last updated" value={formatTime(status.updatedAt)} /><Fact label="Policy version" value={status.policyVersion ?? "Not issued"} /><Fact label="Decision expiry" value={formatTime(status.expiresAt)} /></dl>}</section>;
}

export function presentOwnerVerificationStatusError(error: unknown) {
  if (error instanceof ApiRequestError && (error.code === "unauthorized" || error.code === "not_found")) return OWNER_VERIFICATION_STATUS_DENIED_MESSAGE;
  return presentRecruitmentError(error);
}

export function organizationVerificationRequestIsCurrent(requestId: number, latestRequestId: number, mounted: boolean, subjectCurrent: boolean) {
  return mounted && subjectCurrent && requestId === latestRequestId;
}

function Fact({ label, value }: { label: string; value: string }) { return <div><dt className="text-mist/70">{label}</dt><dd className="mt-1 break-words text-white">{value}</dd></div>; }
function formatTime(value: string | null) { if (!value) return "Not issued"; const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date); }
