"use client";

import { Download, FileText, LoaderCircle, ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { MarkdownPreview } from "@/components/markdown-preview";
import { Button } from "@/components/ui/button";
import {
  assertApplicationSnapshotMatchesApplication,
  getEmployerApplicationSnapshot,
  getEmployerApplicationSnapshotMarkdown,
  presentApplicationSnapshotError,
  verifyApplicationSnapshotMarkdown,
  type Application,
  type ApplicationSnapshot,
  type Job,
} from "@/lib/recruitment-api";
import type { TokenGetter } from "@/lib/api";

type SnapshotState = "idle" | "loading" | "loaded" | "error";

export function ApplicationSnapshotControl({
  job,
  application,
  subject,
  getToken,
  isSubjectCurrent,
  disabled = false,
}: {
  job: Job;
  application: Application;
  subject: string;
  getToken: TokenGetter;
  isSubjectCurrent: (subject: string) => boolean;
  disabled?: boolean;
}) {
  const [state, setState] = useState<SnapshotState>("idle");
  const [snapshot, setSnapshot] = useState<ApplicationSnapshot | null>(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const requestRef = useRef(0);

  useEffect(() => {
    requestRef.current += 1;
    setState("idle");
    setSnapshot(null);
    setError("");
    setDownloading(false);
    return () => {
      requestRef.current += 1;
    };
  }, [application.id, job.id, subject]);

  const isCurrentRequest = (requestId: number, requestSubject: string) =>
    requestRef.current === requestId && isSubjectCurrent(requestSubject);

  const review = async () => {
    if (
      disabled ||
      state === "loading" ||
      !window.confirm(
        "Open this immutable applicant-selected Markdown snapshot solely for this job review?",
      )
    )
      return;

    const requestSubject = subject;
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setState("loading");
    setSnapshot(null);
    setError("");
    try {
      const loaded = await getEmployerApplicationSnapshot(
        job,
        application.id,
        getToken,
        () => isCurrentRequest(requestId, requestSubject),
      );
      if (!isCurrentRequest(requestId, requestSubject)) return;
      setSnapshot(assertApplicationSnapshotMatchesApplication(loaded, application));
      setState("loaded");
    } catch (caught) {
      if (!isCurrentRequest(requestId, requestSubject)) return;
      setError(presentApplicationSnapshotError(caught));
      setState("error");
    }
  };

  const download = async () => {
    if (!snapshot || downloading || disabled) return;
    const requestSubject = subject;
    const requestId = requestRef.current;
    setDownloading(true);
    setError("");
    try {
      const markdown = await getEmployerApplicationSnapshotMarkdown(
        job,
        application.id,
        getToken,
        () => isCurrentRequest(requestId, requestSubject),
      );
      await verifyApplicationSnapshotMarkdown(markdown, snapshot.snapshotSha256);
      if (!isCurrentRequest(requestId, requestSubject)) return;
      const objectUrl = URL.createObjectURL(
        new Blob([markdown], { type: "text/markdown;charset=utf-8" }),
      );
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `connectmd-${job.slug}-${snapshot.snapshotKind}-snapshot.md`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    } catch (caught) {
      if (!isCurrentRequest(requestId, requestSubject)) return;
      setError(presentApplicationSnapshotError(caught));
      setState("error");
    } finally {
      if (isCurrentRequest(requestId, requestSubject)) setDownloading(false);
    }
  };

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="inline-flex items-center gap-2 text-sm font-semibold text-white">
            <FileText className="size-4 text-acid" aria-hidden />
            Immutable Markdown snapshot
          </h4>
          <p className="mt-1 text-xs leading-5 text-mist">
            Employer-only, purpose-limited review. The browser verifies the
            stored SHA-256 before rendering or downloading it.
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          disabled={disabled || state === "loading" || downloading}
          onClick={() => void review()}
        >
          {state === "loading" && (
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
          )}
          Review immutable snapshot
        </Button>
      </div>

      {state === "loading" && (
        <p role="status" className="mt-4 text-sm text-mist">
          Loading and verifying the immutable Markdown snapshot…
        </p>
      )}
      {state === "error" && error && (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-amber-300/25 bg-amber-300/[.08] p-3 text-sm leading-6 text-amber-100"
        >
          <ShieldAlert className="mr-2 inline size-4" aria-hidden />
          {error}
        </p>
      )}
      {state === "loaded" && snapshot && (
        <div className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
            <p className="text-xs text-mist">
              {snapshot.snapshotKind} · {snapshot.snapshotIdentifier} · v
              {snapshot.snapshotVersion} · SHA-256 {snapshot.snapshotSha256}
            </p>
            <Button
              type="button"
              variant="secondary"
              disabled={disabled || downloading}
              onClick={() => void download()}
            >
              {downloading ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              ) : (
                <Download className="size-4" aria-hidden />
              )}
              Download canonical .md
            </Button>
          </div>
          <MarkdownPreview
            markdown={snapshot.markdown}
            className="mt-5 max-h-[34rem] overflow-y-auto pr-2"
            headingOffset={4}
          />
        </div>
      )}
    </div>
  );
}
