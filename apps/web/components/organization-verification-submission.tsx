"use client";

import { FileCheck2, LoaderCircle, ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { presentRecruitmentError, submitOrganizationVerification, VERIFICATION_ARTIFACT_CONTENT_TYPES, VERIFICATION_ARTIFACT_MAX_BYTES, VERIFICATION_EVIDENCE_KINDS, type Organization, type OrganizationVerificationSubmission, type VerificationArtifactContentType, type VerificationEvidenceKind } from "@/lib/recruitment-api";
import type { TokenGetter } from "@/lib/api";

type MetadataRow = { id: string; key: string; value: string };
const blankMetadataRow = (): MetadataRow => ({ id: crypto.randomUUID(), key: "", value: "" });

export function OrganizationVerificationSubmissionForm({ organization, subject, getToken, isSubjectCurrent, onSubmitted }: { organization: Organization; subject: string; getToken: TokenGetter; isSubjectCurrent: (requestSubject: string) => boolean; onSubmitted: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const selectedFileRef = useRef<File | null>(null);
  const [evidenceKind, setEvidenceKind] = useState<VerificationEvidenceKind>("corporate_registration");
  const [metadata, setMetadata] = useState<MetadataRow[]>([blankMetadataRow()]);
  const [hasSelectedFile, setHasSelectedFile] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<OrganizationVerificationSubmission | null>(null);
  const attemptRef = useRef<LogicalMutationAttempt | null>(null);

  const clearEvidence = () => { selectedFileRef.current = null; if (fileRef.current) fileRef.current.value = ""; setHasSelectedFile(false); };
  useEffect(() => () => { clearEvidence(); }, []);
  useEffect(() => { clearEvidence(); setConfirmed(false); setNotice(null); setReceipt(null); setMetadata([blankMetadataRow()]); attemptRef.current = null; }, [organization.id, subject]);

  const onFileChange = (file: File | null) => {
    clearEvidence(); setNotice(null);
    if (!file) return;
    if (!(VERIFICATION_ARTIFACT_CONTENT_TYPES as readonly string[]).includes(file.type)) { setNotice("Choose a PDF, JPEG, PNG, or plain-text evidence file."); return; }
    if (file.size === 0 || file.size > VERIFICATION_ARTIFACT_MAX_BYTES) { setNotice("Evidence must contain at most 256 KiB."); return; }
    selectedFileRef.current = file; setHasSelectedFile(true);
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const requestSubject = subject;
    const current = () => isSubjectCurrent(requestSubject);
    const file = selectedFileRef.current;
    if (!current() || !file || !confirmed || busy) return;
    let metadataRecord: Record<string, string>;
    try { metadataRecord = metadataForSubmission(metadata); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Verification metadata is invalid."); return; }
    setBusy(true); setNotice(null); setReceipt(null);
    let submissionSucceeded = false;
    try {
      const artifactBase64 = await fileToBase64(file);
      if (!current()) return;
      attemptRef.current = beginLogicalMutationAttempt(attemptRef.current, requestSubject, { operation: "submit-organization-verification", organizationSlug: organization.slug, evidenceKind, metadata: metadataRecord, artifactContentType: file.type, artifactBase64 });
      const attempt = attemptRef.current;
      const submitted = await submitOrganizationVerification(organization.slug, { evidenceKind, metadata: metadataRecord, artifactContentType: file.type as VerificationArtifactContentType, artifactBase64 }, getToken, current, attempt.idempotencyKey);
      if (!current()) return;
      submissionSucceeded = true;
      attemptRef.current = null;
      setReceipt(submitted); setNotice("Evidence submitted for independent review. No verification decision has been made."); onSubmitted();
    } catch (error) {
      attemptRef.current = settleLogicalMutationAttempt(attemptRef.current!, error); if (current()) setNotice(attemptRef.current ? "The verification submission may have completed. Retry the unchanged submission to recover the same result. " + presentRecruitmentError(error) : presentRecruitmentError(error));
    } finally {
      if (submissionSucceeded) clearEvidence();
      if (current()) setBusy(false);
    }
  };

  return <section aria-labelledby="verification-submission-title" className="rounded-[1.5rem] border border-acid/20 bg-acid/[.06] p-5"><div className="flex gap-3"><FileCheck2 className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden /><div><h2 id="verification-submission-title" className="text-xl font-semibold text-white">Submit recruiting-control evidence</h2><p className="mt-2 text-sm leading-6 text-mist">Owner-only, human-confirmed submission for independent review. It concerns recruiting control only; it does not self-verify the organization, endorse it, or publish jobs.</p></div></div><p className="mt-4 rounded-xl border border-white/10 bg-black/15 p-3 text-sm leading-6 text-mist"><ShieldAlert className="mr-2 inline size-4 text-acid" aria-hidden />Only an active, unexpired, matching service decision permits publication. Independent reviewer access is separate and unavailable from this workspace.</p>{notice && <p role="status" className="mt-4 rounded-xl border border-white/10 bg-black/15 p-3 text-sm leading-6 text-mist">{notice}</p>}{receipt && <dl className="mt-4 grid gap-2 rounded-xl border border-white/10 bg-black/15 p-4 text-sm text-mist sm:grid-cols-2"><Receipt label="Verification ID" value={receipt.verificationId} /><Receipt label="Evidence hash" value={receipt.evidenceSha256} /><Receipt label="Artifact type" value={receipt.artifactContentType} /><Receipt label="Artifact size" value={`${receipt.artifactSizeBytes} bytes`} /><Receipt label="Submitted" value={formatTime(receipt.submittedAt)} /></dl>}<form className="mt-5 space-y-4" onSubmit={(event) => void submit(event)}><label className="block text-sm font-semibold text-white">Evidence kind<select className={selectClass} value={evidenceKind} disabled={busy} onChange={(event) => setEvidenceKind(event.target.value as VerificationEvidenceKind)}>{VERIFICATION_EVIDENCE_KINDS.map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}</select></label><fieldset><legend className="text-sm font-semibold text-white">Bounded metadata (optional)</legend><p className="mt-1 text-xs leading-5 text-mist">Up to 20 key/value pairs. Do not include more information than independent review needs.</p><div className="mt-3 space-y-2">{metadata.map((row, index) => <div key={row.id} className="grid gap-2 sm:grid-cols-[minmax(0,.65fr)_minmax(0,1fr)_auto]"><Input aria-label={`Metadata key ${index + 1}`} value={row.key} maxLength={64} disabled={busy} placeholder="Key" onChange={(event) => setMetadata((current) => current.map((item) => item.id === row.id ? { ...item, key: event.target.value } : item))} /><Input aria-label={`Metadata value ${index + 1}`} value={row.value} maxLength={500} disabled={busy} placeholder="Value" onChange={(event) => setMetadata((current) => current.map((item) => item.id === row.id ? { ...item, value: event.target.value } : item))} /><Button type="button" variant="ghost" disabled={busy || metadata.length === 1} onClick={() => setMetadata((current) => current.filter((item) => item.id !== row.id))}>Remove</Button></div>)}</div>{metadata.length < 20 && <Button type="button" variant="ghost" className="mt-2" disabled={busy} onClick={() => setMetadata((current) => [...current, blankMetadataRow()])}>Add metadata</Button>}</fieldset><label className="block text-sm font-semibold text-white">Private evidence file<input ref={fileRef} className="mt-2 block w-full text-sm text-mist file:mr-4 file:rounded-full file:border-0 file:bg-white/[.08] file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white" type="file" accept="application/pdf,image/jpeg,image/png,text/plain,.pdf,.jpg,.jpeg,.png,.txt" disabled={busy} onChange={(event) => onFileChange(event.target.files?.[0] ?? null)} /></label><p className="text-xs leading-5 text-mist">PDF, JPEG, PNG, or plain text; 256 KiB maximum. The file is converted locally only for this submission, is never previewed, logged, or placed in browser storage, and is cleared after a successful submission or when this form changes scope.</p>{hasSelectedFile && <p role="status" className="text-xs text-acid">Private evidence selected; its contents are not displayed.</p>}<label className="flex items-start gap-3 text-sm leading-6 text-mist"><input type="checkbox" className="mt-1 size-4 accent-acid" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /><span>I am the signed-in organization owner and I confirm this bounded private evidence may be submitted for independent recruiting-control review.</span></label><Button type="submit" disabled={busy || !hasSelectedFile || !confirmed}>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}Submit for independent review</Button></form></section>;
}

function metadataForSubmission(rows: MetadataRow[]) {
  const result: Record<string, string> = {};
  for (const row of rows) { const key = row.key.trim(); const value = row.value.trim(); if (!key && !value) continue; if (!key || !value) throw new Error("Each metadata row needs both a key and a value."); if (Object.hasOwn(result, key)) throw new Error("Metadata keys must be unique."); result[key] = value; }
  return result;
}

async function fileToBase64(file: File) { const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ""; for (const byte of bytes) binary += String.fromCharCode(byte); return btoa(binary); }
function Receipt({ label, value }: { label: string; value: string }) { return <div><dt className="text-xs font-semibold uppercase tracking-wide text-mist/70">{label}</dt><dd className="mt-1 break-all text-white">{value}</dd></div>; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date); }
const selectClass = "mt-1.5 min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15";
