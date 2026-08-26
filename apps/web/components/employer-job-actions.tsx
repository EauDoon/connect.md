"use client";

import { LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import {
  hasActiveRecruitingControl,
  type Job,
  type JobInput,
  type Organization,
} from "@/lib/recruitment-api";

const blankJob: JobInput = {
  slug: "",
  title: "",
  description: "",
  location: "",
  workMode: null,
  employmentType: null,
};

const selectClass =
  "mt-1.5 min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15";

type JobActionsProps = {
  organization: Organization;
  job: Job | null;
  busy: string | null;
  inspect: (slug: string) => Promise<void>;
  create: (input: JobInput) => Promise<void>;
  save: (input: JobInput) => Promise<void>;
  lifecycle: (action: "publish" | "close") => Promise<void>;
};

export function JobActions({
  organization,
  job,
  busy,
  inspect,
  create,
  save,
  lifecycle,
}: JobActionsProps) {
  const [slug, setSlug] = useState("");
  const [draft, setDraft] = useState<JobInput>(job ? jobInput(job) : blankJob);
  const recruitingControlActive = hasActiveRecruitingControl(organization);
  const update = <K extends keyof JobInput>(key: K, value: JobInput[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  return (
    <section>
      <h3 className="font-semibold text-white">Job draft</h3>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void inspect(slug);
        }}
      >
        <Input
          value={slug}
          onChange={(event) => setSlug(event.target.value)}
          placeholder="existing-job-slug"
          required
        />
        <Button variant="secondary" type="submit" disabled={busy !== null}>
          Open
        </Button>
      </form>

      <form
        className="mt-5"
        onSubmit={(event) => {
          event.preventDefault();
          void (job ? save(draft) : create(draft));
        }}
      >
        <label className="block text-sm text-white">
          Slug
          <Input
            className="mt-1.5"
            value={draft.slug}
            disabled={Boolean(job)}
            onChange={(event) =>
              update(
                "slug",
                event.target.value.toLowerCase().replace(/\s+/gu, "-"),
              )
            }
            required
          />
        </label>
        <label className="mt-3 block text-sm text-white">
          Title
          <Input
            className="mt-1.5"
            value={draft.title}
            onChange={(event) => update("title", event.target.value)}
            required
          />
        </label>
        <label className="mt-3 block text-sm text-white">
          Description
          <Textarea
            className="mt-1.5"
            value={draft.description}
            onChange={(event) => update("description", event.target.value)}
            required
          />
        </label>
        <label className="mt-3 block text-sm text-white">
          Location
          <Input
            className="mt-1.5"
            value={draft.location}
            onChange={(event) => update("location", event.target.value)}
          />
        </label>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm text-white">
            Work mode
            <select
              className={selectClass}
              value={draft.workMode ?? ""}
              onChange={(event) =>
                update(
                  "workMode",
                  (event.target.value || null) as Job["workMode"],
                )
              }
            >
              <option value="">Unspecified</option>
              <option value="remote">Remote</option>
              <option value="hybrid">Hybrid</option>
              <option value="onsite">Onsite</option>
            </select>
          </label>
          <label className="text-sm text-white">
            Employment
            <select
              className={selectClass}
              value={draft.employmentType ?? ""}
              onChange={(event) =>
                update(
                  "employmentType",
                  (event.target.value || null) as Job["employmentType"],
                )
              }
            >
              <option value="">Unspecified</option>
              <option value="full_time">Full time</option>
              <option value="part_time">Part time</option>
              <option value="contract">Contract</option>
              <option value="internship">Internship</option>
              <option value="temporary">Temporary</option>
            </select>
          </label>
        </div>
        <Button className="mt-4" type="submit" disabled={busy !== null}>
          {job ? "Save job" : "Create private draft"}
        </Button>
      </form>

      {job && (
        <div className="mt-5 rounded-xl border border-white/10 bg-black/15 p-4">
          <p className="text-sm text-mist">
            Current state: <strong className="text-white">{job.status}</strong>
          </p>
          {job.status === "draft" && (
            <Button
              className="mt-3"
              disabled={busy !== null || !recruitingControlActive}
              title={
                !recruitingControlActive
                  ? "An active recruiting-control verification from the service is required before publishing."
                  : undefined
              }
              onClick={() => void lifecycle("publish")}
            >
              {busy === "publish" && (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              )}
              Publish as signed-in human
            </Button>
          )}
          {job.status === "draft" && !recruitingControlActive && (
            <p className="mt-2 text-xs leading-5 text-amber-100">
              Publication is disabled until the service returns active
              recruiting-control verification. This interface cannot create
              it.
            </p>
          )}
          {job.status === "published" && (
            <Button
              variant="danger"
              className="mt-3"
              disabled={busy !== null}
              onClick={() => void lifecycle("close")}
            >
              Close job
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

function jobInput(job: Job): JobInput {
  return {
    slug: job.slug,
    title: job.title,
    description: job.description,
    location: job.location ?? "",
    workMode: job.workMode,
    employmentType: job.employmentType,
  };
}
