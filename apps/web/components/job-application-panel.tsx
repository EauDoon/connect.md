"use client";

import { LoaderCircle, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { ApiRequestError } from "@/lib/api";
import {
  loadApplicationDocumentInventory,
  type ApplicationDocumentInventoryResult,
} from "@/lib/application-document-inventory";
import {
  APPLICATION_MESSAGE_MAX_LENGTH,
  authSubjectIsCurrent,
  listApplicationDocuments,
  presentRecruitmentError,
  submitApplication,
  type ApplicationDocument,
  type Job,
} from "@/lib/recruitment-api";

export type ApplicationSubmissionIntent = {
  organizationSlug: string;
  jobSlug: string;
  message: string;
  snapshotKind: "profile" | "resume";
  snapshotIdentifier: string;
};

export type ApplicationSubmissionAttempt = {
  fingerprint: string;
  idempotencyKey: string;
};

export function nextApplicationSubmissionAttempt(
  previous: ApplicationSubmissionAttempt | null,
  intent: ApplicationSubmissionIntent,
  createKey: () => string = () => crypto.randomUUID(),
) {
  const fingerprint = JSON.stringify(intent);
  return previous?.fingerprint === fingerprint
    ? previous
    : { fingerprint, idempotencyKey: createKey() } satisfies ApplicationSubmissionAttempt;
}

export function shouldRetainApplicationSubmissionAttempt(error: unknown) {
  return error instanceof ApiRequestError && (error.code === "server" || (error.code === "request" && error.status === undefined));
}

type DocumentInventoryStatus =
  | "loading"
  | ApplicationDocumentInventoryResult["status"];

export function JobApplicationPanel({ job }: { job: Job }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } =
    useConnectmdAuth();

  if (!configured)
    return (
      <Gate
        title="Sign-in is not configured"
        body="This deployment cannot submit applications until signed-in human authentication is configured."
      />
    );
  if (!isLoaded)
    return (
      <Gate
        title="Checking your session"
        body="Application controls will appear after your signed-in session is available."
        loading
      />
    );
  if (!isSignedIn || !subject)
    return (
      <Gate
        title="A signed-in human must apply"
        body="Agents cannot submit or withdraw applications. Sign in, then choose one public profile or resume snapshot."
      />
    );

  return (
    <AuthenticatedJobApplicationPanel
      key={`${subject}:${job.id}`}
      job={job}
      subject={subject}
      getToken={getToken}
    />
  );
}

function AuthenticatedJobApplicationPanel({
  job,
  subject,
  getToken,
}: {
  job: Job;
  subject: string;
  getToken: ReturnType<typeof useConnectmdAuth>["getToken"];
}) {
  const [documents, setDocuments] = useState<ApplicationDocument[]>([]);
  const [documentInventoryStatus, setDocumentInventoryStatus] =
    useState<DocumentInventoryStatus>("loading");
  const [documentInventoryError, setDocumentInventoryError] = useState("");
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const subjectRef = useRef<string | null>(subject);
  const inventoryRequestRef = useRef(0);
  const submissionAttemptRef = useRef<ApplicationSubmissionAttempt | null>(null);
  subjectRef.current = subject;

  const loadDocuments = useCallback(async () => {
    const requestSubject = subject;
    const requestId = inventoryRequestRef.current + 1;
    inventoryRequestRef.current = requestId;
    const isCurrent = () =>
      inventoryRequestRef.current === requestId &&
      authSubjectIsCurrent(subjectRef.current, requestSubject);

    setDocumentInventoryStatus("loading");
    setDocumentInventoryError("");
    setDocuments([]);
    setSelected("");
    submissionAttemptRef.current = null;

    const result = await loadApplicationDocumentInventory(
      () => listApplicationDocuments(getToken, isCurrent),
      presentRecruitmentError,
    );
    if (!isCurrent()) return;

    setDocuments(result.documents);
    setSelected(result.selected);
    if (result.status === "error") {
      setDocumentInventoryError(result.error);
      setDocumentInventoryStatus("error");
    } else {
      setDocumentInventoryStatus("ready");
    }
  }, [getToken, subject]);

  useEffect(
    () => () => {
      subjectRef.current = null;
      inventoryRequestRef.current += 1;
    },
    [],
  );

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  useEffect(() => {
    submissionAttemptRef.current = null;
  }, [job.id]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const [kind, identifier] = selected.split(":", 2);
    if (
      documentInventoryStatus !== "ready" ||
      (kind !== "profile" && kind !== "resume") ||
      !identifier ||
      !confirmed
    )
      return;
    if (
      !window.confirm(
        "Submit this human-confirmed application with the selected public document snapshot?",
      )
    )
      return;
    const requestSubject = subject;
    const intent = {
      organizationSlug: job.organizationSlug,
      jobSlug: job.slug,
      message: message.trim(),
      snapshotKind: kind,
      snapshotIdentifier: identifier,
    } satisfies ApplicationSubmissionIntent;
    const attempt = nextApplicationSubmissionAttempt(
      submissionAttemptRef.current,
      intent,
    );
    submissionAttemptRef.current = attempt;
    setBusy(true);
    setNotice(null);
    try {
      const application = await submitApplication(
        job,
        {
          message: message.trim(),
          snapshotKind: kind,
          snapshotIdentifier: identifier,
        },
        getToken,
        () => authSubjectIsCurrent(subjectRef.current, requestSubject),
        attempt.idempotencyKey,
      );
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setNotice(
        `Application submitted. Its ${application.snapshotKind} snapshot is fixed at version ${application.snapshotVersion}.`,
      );
      setMessage("");
      setConfirmed(false);
      submissionAttemptRef.current = null;
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) {
        if (shouldRetainApplicationSubmissionAttempt(error)) {
          setNotice(
            "The application may have been submitted, but its confirmation was not received. Retry this unchanged application to recover the original result; changing its message or snapshot starts a new submission.",
          );
        } else {
          submissionAttemptRef.current = null;
          setNotice(presentRecruitmentError(error));
        }
      }
    } finally {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject))
        setBusy(false);
    }
  };

  const inventoryLoading = documentInventoryStatus === "loading";

  return (
    <section
      aria-labelledby="application-title"
      className="rounded-[1.4rem] border border-white/10 bg-panel p-5 sm:p-6"
    >
      <div className="flex gap-3">
        <ShieldCheck
          className="mt-0.5 size-5 shrink-0 text-acid"
          aria-hidden
        />
        <div>
          <h2
            id="application-title"
            className="text-xl font-semibold text-white"
          >
            Apply as a human
          </h2>
          <p className="mt-1 text-sm leading-6 text-mist">
            Select one public canonical document. The employer receives an
            immutable versioned snapshot, not your whole account.
          </p>
        </div>
      </div>
      {notice && (
        <p
          role="status"
          className="mt-5 rounded-xl border border-white/10 bg-black/20 p-3 text-sm leading-6 text-mist"
        >
          {notice}
        </p>
      )}
      <form className="mt-5" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm font-semibold text-white">
          Public profile or resume
          <select
            value={selected}
            onChange={(event) => {
              submissionAttemptRef.current = null;
              setSelected(event.target.value);
            }}
            className={fieldClass}
            disabled={
              busy ||
              documentInventoryStatus !== "ready" ||
              documents.length === 0
            }
          >
            <option value="">
              {inventoryLoading
                ? "Loading public documents…"
                : documentInventoryStatus === "error"
                  ? "Public documents unavailable"
                  : "Choose a public document"}
            </option>
            {documents.map((document) => (
              <option
                key={document.id}
                value={`${document.kind}:${document.identifier}`}
              >
                {document.kind} · {document.identifier} · v{document.version}
              </option>
            ))}
          </select>
        </label>
        {inventoryLoading && (
          <p role="status" className="mt-2 text-sm text-mist">
            <LoaderCircle
              className="mr-2 inline size-4 animate-spin text-acid"
              aria-hidden
            />
            Loading your public documents…
          </p>
        )}
        {documentInventoryStatus === "error" && (
          <div
            role="alert"
            className="mt-3 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-3"
          >
            <p className="text-sm leading-6 text-amber-100/85">
              {documentInventoryError}
            </p>
            <Button
              type="button"
              variant="secondary"
              className="mt-3"
              onClick={() => void loadDocuments()}
            >
              <RefreshCw className="size-4" aria-hidden />
              Retry public documents
            </Button>
          </div>
        )}
        {documentInventoryStatus === "ready" && documents.length === 0 && (
          <p
            role="status"
            aria-live="polite"
            className="mt-2 text-sm leading-6 text-amber-100/85"
          >
            No public profile or resume is available. Publish one first;
            private documents cannot be attached to an application.
          </p>
        )}
        <label className="mt-4 block text-sm font-semibold text-white">
          Message
          <Textarea
            className="mt-1.5"
            value={message}
            onChange={(event) => {
              submissionAttemptRef.current = null;
              setMessage(event.target.value);
            }}
            minLength={1}
            maxLength={APPLICATION_MESSAGE_MAX_LENGTH}
            required
            disabled={busy}
            placeholder="Why this role is relevant to you."
          />
        </label>
        <label className="mt-4 flex items-start gap-3 text-sm leading-6 text-mist">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            disabled={busy}
            className="mt-1 size-4 accent-acid"
          />
          <span>
            I am the signed-in human submitting this application and I confirm
            this public document snapshot may be shared for this job review.
          </span>
        </label>
        <Button
          className="mt-5 w-full"
          type="submit"
          disabled={
            busy ||
            documentInventoryStatus !== "ready" ||
            !selected ||
            !message.trim() ||
            !confirmed
          }
        >
          {busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
          Submit human-confirmed application
        </Button>
      </form>
    </section>
  );
}

function Gate({
  title,
  body,
  loading = false,
}: {
  title: string;
  body: string;
  loading?: boolean;
}) {
  return (
    <section className="rounded-[1.4rem] border border-white/10 bg-panel p-5">
      <h2 className="text-lg font-semibold text-white">
        {loading && (
          <LoaderCircle
            className="mr-2 inline size-4 animate-spin text-acid"
            aria-hidden
          />
        )}
        {title}
      </h2>
      <AsyncBoundaryMessage className="mt-2 text-sm leading-6 text-mist" loading={loading}>{body}</AsyncBoundaryMessage>
    </section>
  );
}

const fieldClass =
  "mt-1.5 min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:opacity-50";
