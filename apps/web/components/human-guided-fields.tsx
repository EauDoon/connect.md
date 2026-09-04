"use client";

import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import type { GuidedReferenceChoices } from "@/components/draft-provider";
import {
  BufferedInput,
  BufferedTextarea,
  FieldLabel,
} from "@/components/human-buffered-fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import {
  appendGuidedEntry,
  parseGuidedSection,
  removeGuidedEntry,
  replaceGuidedEntry,
  swapGuidedEntries,
  type GuidedEntryBlock,
  type GuidedEntryKind,
  type GuidedEntryValue,
} from "@/lib/guided-sections";
import type { HumanFields } from "@/lib/markdown";

const guidedSelectClass =
  "w-full rounded-xl border border-white/12 bg-black/25 px-3.5 py-3 text-sm text-white outline-none transition focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:cursor-not-allowed disabled:opacity-60";
const commaValues = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

function GuidedV2Section({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={id}
      className="rounded-xl border border-white/10 bg-black/[.12] p-4"
    >
      <h3 id={id} className="text-sm font-semibold text-white">
        {title}
      </h3>
      <p className="mt-1 text-xs leading-5 text-mist/80">{description}</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">{children}</div>
    </section>
  );
}

export function GuidedEntriesEditor({ kind, value, disabled, onChange }: { kind: GuidedEntryKind; value: string; disabled: boolean; onChange: (value: string) => void }) {
  const blocks = useMemo(() => parseGuidedSection(value, kind), [kind, value]);
  const entries = blocks.filter((block): block is GuidedEntryBlock => block.type === "guided");
  const hasAdvancedSource = blocks.some((block) => block.type === "raw" && block.source.trim().length > 0);
  const [announcement, setAnnouncement] = useState("");
  const labels = kind === "experience"
    ? { title: "Experience", primary: "Role", secondary: "Organization", context: "Dates, location, or context", highlights: "Outcomes and evidence" }
    : { title: "Education", primary: "Institution", secondary: "Credential or field", context: "Dates, location, or context", highlights: "Details and evidence" };

  const update = (block: GuidedEntryBlock, patch: Partial<GuidedEntryValue>) => onChange(replaceGuidedEntry(value, block, { ...block.value, ...patch }));
  const add = () => onChange(appendGuidedEntry(value, { kind, primary: kind === "experience" ? "New role" : "New institution", secondary: "", context: "", highlights: [] }));
  const focusAfterChange = (id: string, message: string) => {
    setAnnouncement(message);
    window.requestAnimationFrame(() => document.getElementById(id)?.focus());
  };
  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    onChange(swapGuidedEntries(value, entries[index], entries[target]));
    focusAfterChange(`${kind}-card-${target}-title`, `${labels.title} entry moved to position ${target + 1}.`);
  };
  const remove = (block: GuidedEntryBlock, index: number) => {
    onChange(removeGuidedEntry(value, block));
    const focusId = entries.length === 1 ? `${kind}-add-entry` : `${kind}-card-${Math.min(index, entries.length - 2)}-title`;
    focusAfterChange(focusId, `${labels.title} entry ${index + 1} removed.`);
  };

  return <fieldset className="sm:col-span-2 rounded-2xl border border-white/10 bg-black/[.12] p-4">
    <legend className="px-1 text-sm font-semibold text-white">{labels.title} {kind === "education" ? <span className="font-normal text-mist">(optional for profiles)</span> : null}</legend>
    <p className="text-xs leading-5 text-mist">Add and edit plain-language cards. Each change updates the canonical Markdown section; no separate career database is created.</p>
    <div className="mt-4 space-y-3">
      {entries.map((block, index) => <section key={`${kind}-${index}`} aria-labelledby={`${kind}-card-${index}-title`} className="rounded-xl border border-white/10 bg-black/20 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 id={`${kind}-card-${index}-title`} tabIndex={-1} className="text-sm font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-acid/60">{labels.title} entry {index + 1}</h4>
          <div className="flex flex-wrap gap-1">
            <Button type="button" variant="ghost" className="min-h-11 px-2" disabled={disabled || index === 0} aria-label={`Move ${labels.title.toLowerCase()} entry ${index + 1} up`} onClick={() => move(index, -1)}><ChevronUp className="size-4" aria-hidden /></Button>
            <Button type="button" variant="ghost" className="min-h-11 px-2" disabled={disabled || index === entries.length - 1} aria-label={`Move ${labels.title.toLowerCase()} entry ${index + 1} down`} onClick={() => move(index, 1)}><ChevronDown className="size-4" aria-hidden /></Button>
            <Button type="button" variant="ghost" className="min-h-11 px-2 text-red-200 hover:text-red-100" disabled={disabled} aria-label={`Remove ${labels.title.toLowerCase()} entry ${index + 1}`} onClick={() => remove(block, index)}><Trash2 className="size-4" aria-hidden /></Button>
          </div>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div><FieldLabel htmlFor={`${kind}-${index}-primary`}>{labels.primary}</FieldLabel><BufferedInput id={`${kind}-${index}-primary`} disabled={disabled} maxLength={160} value={block.value.primary} onCommit={(primary) => update(block, { primary })} placeholder={kind === "experience" ? "Role" : "Institution"} /></div>
          <div><FieldLabel htmlFor={`${kind}-${index}-secondary`}>{labels.secondary}</FieldLabel><BufferedInput id={`${kind}-${index}-secondary`} disabled={disabled} maxLength={160} value={block.value.secondary} onCommit={(secondary) => update(block, { secondary })} placeholder={kind === "experience" ? "Organization" : "Credential or field"} /></div>
          <div className="sm:col-span-2"><FieldLabel htmlFor={`${kind}-${index}-context`}>{labels.context}</FieldLabel><BufferedInput id={`${kind}-${index}-context`} disabled={disabled} maxLength={240} value={block.value.context} onCommit={(context) => update(block, { context })} placeholder="2022–present · Singapore" /></div>
          <div className="sm:col-span-2"><FieldLabel htmlFor={`${kind}-${index}-highlights`}>{labels.highlights}</FieldLabel><BufferedTextarea id={`${kind}-${index}-highlights`} disabled={disabled} value={block.value.highlights.join("\n")} onCommit={(nextValue) => update(block, { highlights: nextValue.split(/\r?\n/u) })} placeholder="One outcome per line—no Markdown needed" describedBy={`${kind}-${index}-highlights-help`} /><p id={`${kind}-${index}-highlights-help`} className="mt-1 text-xs text-mist/75">One outcome per line. Bullets are generated in Markdown automatically.</p></div>
        </div>
      </section>)}
      {entries.length === 0 && <p className="rounded-xl border border-dashed border-white/15 p-4 text-sm leading-6 text-mist">No simple {labels.title.toLowerCase()} cards yet. Add one below; any complex source remains untouched.</p>}
      <Button id={`${kind}-add-entry`} type="button" variant="secondary" disabled={disabled} onClick={() => { add(); setAnnouncement(`${labels.title} entry added.`); }}><Plus className="size-4" aria-hidden /> Add {kind === "experience" ? "role" : "education"}</Button>
      <p className="sr-only" aria-live="polite">{announcement}</p>
    </div>
    {hasAdvancedSource && <details className="mt-4 rounded-xl border border-amber-200/15 bg-amber-200/[.04] p-3">
      <summary className="flex min-h-11 cursor-pointer items-center text-sm font-semibold text-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Advanced Markdown preserved</summary>
      <p id={`${kind}-advanced-help`} className="mt-2 text-xs leading-5 text-mist">Complex paragraphs, comments, or nested Markdown are not guessed into form fields. Edit the exact section below or switch to MD Mode. Changes commit when focus leaves the field.</p>
      <FieldLabel htmlFor={`${kind}-advanced-source`}>Exact {labels.title} section source</FieldLabel>
      <BufferedTextarea id={`${kind}-advanced-source`} className="mt-3 min-h-40 font-mono text-xs" describedBy={`${kind}-advanced-help`} placeholder={`Advanced ${labels.title} Markdown`} disabled={disabled} value={value} onCommit={onChange} />
    </details>}
  </fieldset>;
}

export function StructuredV2Fields({ fields, guidedReferenceChoices, setGuidedReferenceChoices, patch, disabled }: { fields: HumanFields; guidedReferenceChoices: GuidedReferenceChoices; setGuidedReferenceChoices: (choices: Partial<GuidedReferenceChoices>) => void; patch: (patchFields: Partial<HumanFields>) => void; disabled: boolean }) {
  const workModes: Array<{ value: HumanFields["workModes"][number]; label: string }> = [
    { value: "on_site", label: "On-site" },
    { value: "hybrid", label: "Hybrid" },
    { value: "remote", label: "Remote" }
  ];
  const toggleWorkMode = (mode: HumanFields["workModes"][number]) => {
    patch({ workModes: fields.workModes.includes(mode) ? fields.workModes.filter((item) => item !== mode) : [...fields.workModes, mode] });
  };
  const hasOptionalSignals = fields.languages.length > 0 || fields.organizations.length > 0 || fields.workModes.length > 0 || fields.availabilityStatus !== "not_disclosed" || fields.representationStatus !== "not_disclosed" || fields.contactDisclosure !== "none";
  const [optionalSignalsOpen, setOptionalSignalsOpen] = useState(hasOptionalSignals);
  const { languageProficiency, organizationRelationship } = guidedReferenceChoices;
  useEffect(() => { if (hasOptionalSignals) setOptionalSignalsOpen(true); }, [hasOptionalSignals]);

  return <fieldset className="sm:col-span-2 space-y-4 rounded-2xl border border-acid/20 bg-acid/[.04] p-4">
    <legend className="px-1 text-sm font-semibold text-white">Structured professional signals <span className="font-normal text-mist">(v2)</span></legend>
    <p className="text-xs leading-5 text-mist">These are your own labels, stored as deterministic <code>connectmd-user-*</code> references—not an official taxonomy. Existing references remain unchanged while their labels remain unchanged. Work modes are optional: leave them blank when you do not want to make a public work-mode declaration.</p>

    <GuidedV2Section id="v2-discovery" title="Discovery" description="Describe the core of your work with your own labels. Location and skills remain in the essential-shape fields above.">
      <div>
        <FieldLabel htmlFor="occupations">Occupations</FieldLabel>
        <BufferedInput id="occupations" value={fields.occupations.join(", ")} onCommit={(nextValue) => patch({ occupations: commaValues(nextValue) })} placeholder="Product manager, Researcher" maxLength={3_200} disabled={disabled} describedBy="occupations-help" />
        <p id="occupations-help" className="mt-1 text-xs text-mist/75">Required. Separate labels with commas.</p>
      </div>
      <div>
        <FieldLabel htmlFor="industries">Industries</FieldLabel>
        <BufferedInput id="industries" value={fields.industries.join(", ")} onCommit={(nextValue) => patch({ industries: commaValues(nextValue) })} placeholder="Financial services, Software" maxLength={3_200} disabled={disabled} />
      </div>
      <div>
        <FieldLabel htmlFor="seniority">Seniority</FieldLabel>
        <BufferedInput id="seniority" value={fields.seniority} onCommit={(nextValue) => patch({ seniority: nextValue })} placeholder="Senior individual contributor" maxLength={160} disabled={disabled} />
      </div>
      <div>
        <FieldLabel htmlFor="open-to">Open to</FieldLabel>
        <BufferedInput id="open-to" value={fields.openTo.join(", ")} onCommit={(nextValue) => patch({ openTo: commaValues(nextValue) })} placeholder="Advisory, Full-time roles" maxLength={3_200} disabled={disabled} />
      </div>
    </GuidedV2Section>

    <details open={optionalSignalsOpen} onToggle={(event) => setOptionalSignalsOpen(event.currentTarget.open)} className="group rounded-xl border border-white/10 bg-black/[.12] p-4">
      <summary className="min-h-11 cursor-pointer list-none rounded-lg text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid [&::-webkit-details-marker]:hidden">
        <span className="block text-sm font-semibold text-white">More professional signals <span className="font-normal text-mist">(optional)</span></span>
        <span className="mt-1 block text-xs leading-5 text-mist/80">Languages, organizations, availability, representation, and contact stay out of the first pass.</span>
      </summary>
      <div className="mt-4 space-y-4">
        <GuidedV2Section id="v2-context" title="Languages and organizations" description="Choose a proficiency before adding a new language. Existing references remain unchanged until you edit their labels.">
          <div>
            <FieldLabel htmlFor="languages">Languages</FieldLabel>
            <BufferedInput id="languages" value={fields.languages.join(", ")} onCommit={(nextValue) => patch({ languages: commaValues(nextValue), languageProficiency })} placeholder="English, Mandarin" maxLength={4_800} disabled={disabled} describedBy="languages-help" />
            <p id="languages-help" className="mt-1 text-xs text-mist/75">Existing entries retain their IDs and proficiency. Choose a proficiency before adding new labels.</p>
          </div>
          <div>
            <FieldLabel htmlFor="language-proficiency">Proficiency for new languages</FieldLabel>
            <select id="language-proficiency" disabled={disabled} value={languageProficiency} onChange={(event) => setGuidedReferenceChoices({ languageProficiency: event.target.value as HumanFields["languageProficiency"] })} className={guidedSelectClass}>
              <option value="">Choose before adding a language</option>
              <option value="basic">Basic</option>
              <option value="conversational">Conversational</option>
              <option value="professional">Professional</option>
              <option value="native_or_bilingual">Native or bilingual</option>
            </select>
          </div>
          <div>
            <FieldLabel htmlFor="organizations">Organizations</FieldLabel>
            <BufferedInput id="organizations" value={fields.organizations.join(", ")} onCommit={(nextValue) => patch({ organizations: commaValues(nextValue), organizationRelationship })} placeholder="Example Company" maxLength={8_000} disabled={disabled} />
          </div>
          <div>
            <FieldLabel htmlFor="organization-relationship">New organization relationship</FieldLabel>
            <select id="organization-relationship" disabled={disabled} value={organizationRelationship} onChange={(event) => setGuidedReferenceChoices({ organizationRelationship: event.target.value as HumanFields["organizationRelationship"] })} className={guidedSelectClass}>
              <option value="current_employer">Current employer</option>
              <option value="past_employer">Past employer</option>
              <option value="founder">Founder</option>
              <option value="member">Member</option>
              <option value="education">Education</option>
              <option value="client">Client</option>
              <option value="other">Other</option>
            </select>
          </div>
        </GuidedV2Section>

        <GuidedV2Section id="v2-availability" title="Availability" description="Choose only work modes and timing you want to declare. Leave a field blank when it is not disclosed.">
          <fieldset className="sm:col-span-2">
            <legend className="mb-1.5 block text-sm font-medium text-white">Work modes</legend>
            <div className="flex flex-wrap gap-3">
              {workModes.map((mode) => <label key={mode.value} className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-white/12 bg-black/15 px-3 text-sm text-mist">
                <input type="checkbox" checked={fields.workModes.includes(mode.value)} disabled={disabled} onChange={() => toggleWorkMode(mode.value)} className="size-4 accent-acid" />
                {mode.label}
              </label>)}
            </div>
          </fieldset>
          <div>
            <FieldLabel htmlFor="availability-status">Availability</FieldLabel>
            <select id="availability-status" disabled={disabled} value={fields.availabilityStatus} onChange={(event) => patch({ availabilityStatus: event.target.value as HumanFields["availabilityStatus"] })} className={guidedSelectClass}>
              <option value="not_disclosed">Not disclosed</option>
              <option value="available_now">Available now</option>
              <option value="available_from">Available from a date</option>
              <option value="not_available">Not available</option>
            </select>
          </div>
          {fields.availabilityStatus === "available_from" && <div>
            <FieldLabel htmlFor="available-from">Available from</FieldLabel>
            <Input id="available-from" type="date" disabled={disabled} value={fields.availableFrom} onChange={(event) => patch({ availableFrom: event.target.value })} />
          </div>}
        </GuidedV2Section>

        <GuidedV2Section id="v2-representation-contact" title="Representation and contact" description="Keep representation and contact private unless you explicitly disclose them.">
          <div>
            <FieldLabel htmlFor="representation-status">Public representation</FieldLabel>
            <select id="representation-status" disabled={disabled} value={fields.representationStatus} onChange={(event) => patch({ representationStatus: event.target.value as HumanFields["representationStatus"] })} className={guidedSelectClass}>
              <option value="not_disclosed">Not disclosed</option>
              <option value="self">Self-managed</option>
              <option value="authorized_representative">Authorized representative</option>
              <option value="organization">Organization-managed</option>
            </select>
          </div>
          {(fields.representationStatus === "authorized_representative" || fields.representationStatus === "organization") && <div>
            <FieldLabel htmlFor="representative">Representative label</FieldLabel>
            <BufferedInput id="representative" value={fields.representative} onCommit={(nextValue) => patch({ representative: nextValue })} placeholder="Representative or organization" maxLength={160} disabled={disabled} />
          </div>}
          <div>
            <FieldLabel htmlFor="contact-disclosure">Contact disclosure</FieldLabel>
            <select id="contact-disclosure" disabled={disabled} value={fields.contactDisclosure} onChange={(event) => patch({ contactDisclosure: event.target.value as HumanFields["contactDisclosure"] })} className={guidedSelectClass}>
              <option value="none">None</option>
              <option value="platform_only">Platform only</option>
              <option value="public">Public contact channel</option>
            </select>
          </div>
          {fields.contactDisclosure === "public" && <>
            <div>
              <FieldLabel htmlFor="contact-type">Public contact type</FieldLabel>
              <select id="contact-type" disabled={disabled} value={fields.contactType} onChange={(event) => patch({ contactType: event.target.value as HumanFields["contactType"] })} className={guidedSelectClass}>
                <option value="email">Email</option>
                <option value="phone">Phone</option>
                <option value="url">URL</option>
                <option value="platform">Platform</option>
              </select>
            </div>
            <div>
              <FieldLabel htmlFor="contact-value">Public contact value</FieldLabel>
              <BufferedInput id="contact-value" value={fields.contactValue} onCommit={(nextValue) => patch({ contactValue: nextValue })} placeholder="name@example.com" maxLength={320} disabled={disabled} />
            </div>
            <div>
              <FieldLabel htmlFor="contact-label">Public contact label <span className="font-normal text-mist">(optional)</span></FieldLabel>
              <BufferedInput id="contact-label" value={fields.contactLabel} onCommit={(nextValue) => patch({ contactLabel: nextValue })} placeholder="Work email" maxLength={160} disabled={disabled} />
            </div>
          </>}
        </GuidedV2Section>
      </div>
    </details>
  </fieldset>;
}
