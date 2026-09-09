import { PROFILE_RESUME_MAX_UTF8_BYTES, type DocumentKind } from "@/lib/markdown";
import { MAX_DRAFT_CHECKPOINTS, createCheckpoint, type DraftCheckpoint } from "@/lib/draft-checkpoints";

export type RecoveryDraft = { kind: DocumentKind; markdown: string };
export type RecoveryBundle = { format: "connectmd-recovery"; version: 1; draft: RecoveryDraft; checkpoints: Omit<DraftCheckpoint, "id">[] };
export const RECOVERY_MAX_BYTES = 8 * 1024 * 1024;

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
