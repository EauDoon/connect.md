import { PROFILE_RESUME_MAX_UTF8_BYTES, type DocumentKind } from "@/lib/markdown";
import { MAX_DRAFT_CHECKPOINTS, createCheckpoint, type DraftCheckpoint } from "@/lib/draft-checkpoints";

export type RecoveryDraft = { kind: DocumentKind; markdown: string };
export type RecoveryBundle = { format: "connectmd-recovery"; version: 1; draft: RecoveryDraft; checkpoints: Omit<DraftCheckpoint, "id">[] };
export const RECOVERY_MAX_BYTES = 8 * 1024 * 1024;

function recoveryDraft(value: unknown): RecoveryDraft {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid recovery document.");
  const entry = value as Record<string, unknown>;
  if ((entry.kind !== "profile" && entry.kind !== "resume") || typeof entry.markdown !== "string") throw new Error("Recovery documents must contain a profile or resume source.");
  return { kind: entry.kind, markdown: entry.markdown };
}

export function parseRecoveryBundle(source: string): RecoveryBundle {
  if (new TextEncoder().encode(source).length > RECOVERY_MAX_BYTES) throw new Error("The recovery file exceeds the 8 MiB limit.");
  let parsed: unknown;
  try { parsed = JSON.parse(source); } catch { throw new Error("Choose a valid connect.md recovery JSON file."); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Invalid recovery file.");
  const bundle = parsed as Record<string, unknown>;
  if (bundle.format !== "connectmd-recovery" || bundle.version !== 1 || !Array.isArray(bundle.checkpoints)) throw new Error("This recovery format or version is not supported.");
  if (bundle.checkpoints.length > MAX_DRAFT_CHECKPOINTS) throw new Error("A recovery bundle can contain at most five checkpoints.");
  const checkpoints = bundle.checkpoints.map((value: unknown, id: number) => {
    const draft = recoveryDraft(value);
    const label = (value as Record<string, unknown>).label;
    if (typeof label !== "string") throw new Error("A recovery checkpoint needs a name.");
    return { ...draft, label, id };
  });
  // Reuse export bounds and distinct-name checks before any state can change.
  return JSON.parse(encodeRecoveryBundle(recoveryDraft(bundle.draft), checkpoints)) as RecoveryBundle;
}

export function encodeRecoveryBundle(draft: RecoveryDraft, checkpoints: DraftCheckpoint[]): string {
  if (checkpoints.length > MAX_DRAFT_CHECKPOINTS) throw new Error("A recovery bundle can contain at most five checkpoints.");
  if (new TextEncoder().encode(draft.markdown).length > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("The draft exceeds the 128 KiB recovery limit. Copy the source before reducing its size.");
  const checked: DraftCheckpoint[] = [];
  for (const checkpoint of checkpoints) checked.push(createCheckpoint(checked, checkpoint, checkpoint.label, checkpoint.id));
  const bundle: RecoveryBundle = { format: "connectmd-recovery", version: 1, draft, checkpoints: checked.map(({ label, kind, markdown }) => ({ label, kind, markdown })) };
  const encoded = JSON.stringify(bundle, null, 2) + "\n";
  if (new TextEncoder().encode(encoded).length > RECOVERY_MAX_BYTES) throw new Error("The recovery bundle exceeds its size limit.");
  return encoded;
}
