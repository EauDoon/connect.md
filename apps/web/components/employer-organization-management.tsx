"use client";

import { Building2, ShieldAlert, UserPlus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import {
  hasActiveRecruitingControl,
  type Organization,
} from "@/lib/recruitment-api";

const selectClass =
  "mt-1.5 min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15";

type OrganizationManagementProps = {
  organization: Organization;
  busy: string | null;
  save: (input: {
    name: string;
    description: string;
    websiteUrl: string;
  }) => Promise<void>;
  invite: (
    memberProfileHandle: string,
    role: "admin" | "member",
  ) => Promise<void>;
};

export function OrganizationManagement({
  organization,
  busy,
  save,
  invite,
}: OrganizationManagementProps) {
  const [name, setName] = useState(organization.name);
  const [description, setDescription] = useState(
    organization.description ?? "",
  );
  const [websiteUrl, setWebsiteUrl] = useState(
    organization.websiteUrl ?? "",
  );
  const [member, setMember] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [acknowledged, setAcknowledged] = useState(false);
  const recruitingControlActive = hasActiveRecruitingControl(organization);

  return (
    <section className="rounded-[1.5rem] border border-white/10 bg-panel p-5">
      <div className="flex items-start gap-3">
        <Building2 className="mt-0.5 size-5 text-acid" aria-hidden />
        <div>
          <h2 className="text-xl font-semibold text-white">
            {organization.name}
          </h2>
          <p className="mt-1 text-sm text-mist">
            Private owner record ·{" "}
            {recruitingControlActive
              ? "active recruiting control"
              : "no active recruiting control"}
          </p>
        </div>
      </div>

      {!recruitingControlActive && (
        <p className="mt-5 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-3 text-sm leading-6 text-amber-100">
          <ShieldAlert className="mr-2 inline size-4" aria-hidden />
          No active recruiting-control verification was returned by the
          service. This workspace cannot publish jobs or create that state.
        </p>
      )}

      <form
        className="mt-5"
        onSubmit={(event) => {
          event.preventDefault();
          void save({ name, description, websiteUrl });
        }}
      >
        <label className="block text-sm text-white">
          Name
          <Input
            className="mt-1.5"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </label>
        <label className="mt-3 block text-sm text-white">
          Description
          <Textarea
            className="mt-1.5"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <label className="mt-3 block text-sm text-white">
          Website
          <Input
            className="mt-1.5"
            type="url"
            value={websiteUrl}
            onChange={(event) => setWebsiteUrl(event.target.value)}
          />
        </label>
        <Button className="mt-4" type="submit" disabled={busy !== null}>
          Save organization details
        </Button>
      </form>

      <form
        className="mt-7 border-t border-white/10 pt-5"
        onSubmit={(event) => {
          event.preventDefault();
          void invite(member, role);
        }}
      >
        <h3 className="inline-flex items-center gap-2 font-semibold text-white">
          <UserPlus className="size-4 text-acid" aria-hidden />
          Invite a member
        </h3>
        <p className="mt-1 text-sm leading-6 text-mist">
          Only an owner may invite. Enter the recipient&apos;s current public
          profile handle; the service keeps their internal account ID private
          and requires them to accept.
        </p>
        <label className="mt-3 block text-sm text-white">
          Recipient public profile handle
          <Input
            className="mt-1.5"
            value={member}
            onChange={(event) => setMember(event.target.value)}
            placeholder="profile-handle"
            required
          />
        </label>
        <label className="mt-3 block text-sm text-white">
          Role
          <select
            className={selectClass}
            value={role}
            onChange={(event) =>
              setRole(event.target.value as "admin" | "member")
            }
          >
            <option value="member">Member (no job management)</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label className="mt-3 flex gap-3 text-sm leading-6 text-mist">
          <input
            type="checkbox"
            className="mt-1 size-4 accent-acid"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          <span>
            I have confirmed the public profile and understand its owner must
            accept the invitation themselves.
          </span>
        </label>
        <Button
          variant="secondary"
          className="mt-4"
          type="submit"
          disabled={busy !== null || !member || !acknowledged}
        >
          Send gated invitation
        </Button>
      </form>
    </section>
  );
}
