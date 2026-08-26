import { type DocumentResponse } from "@/lib/api";
import { rebaseServerOwnedFields, type DocumentKind } from "@/lib/markdown";

export type SaveSnapshot = {
  subject: string;
  kind: DocumentKind;
  revision: number;
  lineage: number;
  identifier: string;
  markdown: string;
  existingIdentity: string | null;
};

export type CurrentSaveState = {
  subject: string | null;
  kind: DocumentKind;
  revision: number;
  lineage: number;
  identifier: string;
  markdown: string;
  existing: DocumentResponse | null;
};

export function savedDocumentIdentity(document: DocumentResponse | null) {
  return document ? `${document.kind}:${document.id}:${document.identifier}` : null;
}

export function reconcileSaveResponse(snapshot: SaveSnapshot, current: CurrentSaveState, response: DocumentResponse) {
  const changedDocument = current.subject !== snapshot.subject
    || current.kind !== snapshot.kind
    || response.kind !== snapshot.kind
    || current.lineage !== snapshot.lineage
    || current.identifier !== snapshot.identifier
    || response.identifier !== snapshot.identifier
    || savedDocumentIdentity(current.existing) !== snapshot.existingIdentity;
  if (changedDocument) return { disposition: "discard" as const, markdown: current.markdown, savedDocument: current.existing };

  const editedDuringSave = current.revision !== snapshot.revision || current.markdown !== snapshot.markdown;
  if (editedDuringSave) {
    try {
      return { disposition: "preserve" as const, markdown: rebaseServerOwnedFields(current.markdown, response.markdown), savedDocument: response };
    } catch {
      return { disposition: "discard" as const, markdown: current.markdown, savedDocument: current.existing };
    }
  }
  return { disposition: "hydrate" as const, markdown: response.markdown, savedDocument: response };
}

export function uncertainSaveOutcomeMessage() {
  return "Stopped waiting; the save may have completed—verify the original document before retrying.";
}

export function discardedSuccessfulSaveMessage(response: DocumentResponse) {
  return `The original ${response.kind} ${response.identifier} was saved as version ${response.version}; the active draft was not replaced.`;
}

export function priorAccountSuccessfulSaveMessage() {
  return "A save for the prior account completed; the active draft was not replaced.";
}
