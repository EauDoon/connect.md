import { type DocumentResponse } from "@/lib/api";
import { type DocumentKind, normaliseMarkdown, starterFor } from "@/lib/markdown";

export function shouldConfirmDraftReplacement(markdown: string, kind: DocumentKind, savedDocument: DocumentResponse | null) {
  if (savedDocument) return true;
  if (!markdown.trim()) return false;
  return normaliseMarkdown(markdown) !== normaliseMarkdown(starterFor(kind));
}

export function isImportResultCurrent(startKind: DocumentKind, startRevision: number, currentKind: DocumentKind, currentRevision: number, mounted: boolean, aborted: boolean) {
  return mounted && !aborted && startKind === currentKind && startRevision === currentRevision;
}
