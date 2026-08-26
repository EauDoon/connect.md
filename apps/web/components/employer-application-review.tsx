"use client";

import { CheckCircle2, Eye, LoaderCircle } from "lucide-react";
import { useEffect, useRef } from "react";

import { ApplicationSnapshotControl } from "@/components/application-snapshot";
import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import {
  authSubjectIsCurrent,
  type Application,
  type Job,
} from "@/lib/recruitment-api";

type ApplicationReviewProps = {
  job: Job | null;
  applications: Application[];
  applicationCursor: string | null;
  messages: Record<string, string>;
  busy: string | null;
  load: () => Promise<void>;
  loadOlder: () => Promise<void>;
  view: (application: Application) => Promise<void>;
  decide: (
    application: Application,
    action: "review" | "accept" | "reject",
  ) => Promise<void>;
};

export function ApplicationReview({
  job,
  applications,
  applicationCursor,
  messages,
  busy,
  load,
  loadOlder,
  view,
  decide,
}: ApplicationReviewProps) {
  const { subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;

  useEffect(
    () => () => {
      subjectRef.current = null;
    },
    [],
  );

  return (
    <section>
      <h3 className="font-semibold text-white">
        Purpose-limited application review
      </h3>
      <p className="mt-1 text-sm leading-6 text-mist">
        Loading summaries, opening a note, or opening an immutable Markdown
        snapshot makes the required job-review purpose request only after your
        confirmation.
      </p>
      <Button
        variant="secondary"
        className="mt-4"
        disabled={!job || busy !== null}
        onClick={() => void load()}
      >
        Load application summaries
      </Button>

      {applications.length === 0 ? (
        <p className="mt-5 text-sm text-mist">
          {job
            ? "No application summaries loaded."
            : "Open a job to review its applications."}
        </p>
      ) : (
        <ol className="mt-5 space-y-3">
          {applications.map((application) => (
            <li
              key={application.id}
              className="rounded-xl border border-white/10 bg-black/15 p-4"
            >
              <div className="flex flex-wrap justify-between gap-2">
                <span className="text-sm font-semibold text-white">
                  {application.snapshotKind} · {application.snapshotIdentifier}
                </span>
                <span className="text-xs text-mist">
                  {application.status.replaceAll("_", " ")}
                </span>
              </div>
              {messages[application.id] && (
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-mist">
                  {messages[application.id]}
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  variant="ghost"
                  disabled={busy !== null}
                  onClick={() => void view(application)}
                >
                  {busy === application.id ? (
                    <LoaderCircle
                      className="size-4 animate-spin"
                      aria-hidden
                    />
                  ) : (
                    <Eye className="size-4" aria-hidden />
                  )}
                  Review note
                </Button>
                {application.status === "submitted" && (
                  <Button
                    variant="secondary"
                    disabled={busy !== null}
                    onClick={() => void decide(application, "review")}
                  >
                    Mark review
                  </Button>
                )}
                {(application.status === "submitted" ||
                  application.status === "under_review") && (
                  <>
                    <Button
                      variant="secondary"
                      disabled={busy !== null}
                      onClick={() => void decide(application, "accept")}
                    >
                      <CheckCircle2 className="size-4" aria-hidden />
                      Accept
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy !== null}
                      onClick={() => void decide(application, "reject")}
                    >
                      Reject
                    </Button>
                  </>
                )}
              </div>
              {job && subject && (
                <ApplicationSnapshotControl
                  key={`${subject}-${job.id}-${application.id}`}
                  job={job}
                  application={application}
                  subject={subject}
                  getToken={getToken}
                  isSubjectCurrent={(requestSubject) =>
                    authSubjectIsCurrent(subjectRef.current, requestSubject)
                  }
                  disabled={busy !== null}
                />
              )}
            </li>
          ))}
        </ol>
      )}

      {applicationCursor && (
        <Button
          variant="secondary"
          className="mt-5"
          disabled={busy !== null}
          onClick={() => void loadOlder()}
        >
          {busy === "applications-more" && (
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
          )}
          Load older application summaries
        </Button>
      )}
    </section>
  );
}
