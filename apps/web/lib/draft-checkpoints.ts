import { PROFILE_RESUME_MAX_UTF8_BYTES, type DocumentKind } from "@/lib/markdown";

export type DraftCheckpoint = { id: number; label: string; kind: DocumentKind; markdown: string };
export const MAX_DRAFT_CHECKPOINTS = 5;

export function renameCheckpoint(existing: DraftCheckpoint[], id: number, label: string): DraftCheckpoint[] {
  const current = existing.find((entry) => entry.id === id);
  if (!current) throw new Error("This checkpoint is no longer available.");
  const renamed = createCheckpoint(existing.filter((entry) => entry.id !== id), current, label, id);
  return existing.map((entry) => entry.id === id ? renamed : entry);
}

export function createCheckpoint(existing: DraftCheckpoint[], draft: Pick<DraftCheckpoint, "kind" | "markdown">, label: string, id: number): DraftCheckpoint {
  if (existing.length >= MAX_DRAFT_CHECKPOINTS) throw new Error("Five checkpoints are already held in this tab. Remove one before adding another.");
  if (new TextEncoder().encode(draft.markdown).length > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("This draft exceeds the 128 KiB checkpoint limit. Copy the source from the plain-text editor before reducing its size.");
  const trimmed = label.trim();
  if (!trimmed || trimmed.length > 60) throw new Error("Name the checkpoint using 1 to 60 characters.");
  if (existing.some((entry) => entry.label.toLocaleLowerCase("en-US") === trimmed.toLocaleLowerCase("en-US"))) throw new Error("A checkpoint already has this name. Choose a distinct name so you can restore the intended version.");
  return { id, label: trimmed, kind: draft.kind, markdown: draft.markdown };
}
