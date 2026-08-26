export const SIGNED_OUT_DRAFT_SUBJECT = "signed-out";

export function resolvedDraftSubject(configured: boolean, isLoaded: boolean, subject: string | null) {
  if (!configured || !isLoaded) return null;
  return subject ? `user:${subject}` : SIGNED_OUT_DRAFT_SUBJECT;
}

export function requiresDraftReset(owner: string | null, resolvedSubject: string | null) {
  return owner !== null && resolvedSubject !== null && owner !== resolvedSubject;
}

export function shouldMaskOwnedDraft(owner: string | null, configured: boolean, isLoaded: boolean, resolvedSubject: string | null) {
  return (owner !== null && configured && !isLoaded) || requiresDraftReset(owner, resolvedSubject);
}

export function maskOwnedDraftSnapshot<T>(masked: boolean, snapshot: T) {
  return masked ? null : snapshot;
}
