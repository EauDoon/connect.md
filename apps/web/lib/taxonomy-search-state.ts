import type { TaxonomyPage, TaxonomyTerm } from "@/lib/taxonomy-api";

export type TaxonomySearchStatus = "idle" | "loading" | "ready" | "empty" | "loading-more" | "unavailable" | "invalid" | "stale";
export type TaxonomySearchState = {
  taxonomy: string;
  query: string;
  terms: TaxonomyTerm[];
  nextCursor: string | null;
  revision: number | null;
  deliveredCursors: ReadonlySet<string>;
  inFlightKey: string | null;
  status: TaxonomySearchStatus;
  error: string | null;
};

export type TaxonomySearchAction =
  | { type: "reset"; query?: string }
  | { type: "start"; requestKey: string; query: string; cursor: string | null }
  | { type: "success"; requestKey: string; query: string; cursor: string | null; page: TaxonomyPage }
  | { type: "failure"; requestKey: string; query: string; cursor: string | null; status?: number; message: string }
  | { type: "retry" };

export function createTaxonomySearchState(taxonomy: string, query = ""): TaxonomySearchState {
  return { taxonomy, query, terms: [], nextCursor: null, revision: null, deliveredCursors: new Set(), inFlightKey: null, status: "idle", error: null };
}

export function canStartTaxonomyRequest(state: TaxonomySearchState, requestKey: string, cursor: string | null) {
  if (state.inFlightKey !== null) return false;
  if (cursor !== null && state.deliveredCursors.has(cursor)) return false;
  return true;
}

export function appendTaxonomyPage(existing: TaxonomyTerm[], page: TaxonomyPage, currentCursor: string | null, deliveredCursors: ReadonlySet<string>) {
  const known = new Set(existing.map((term) => `${term.taxonomy}:${term.canonicalId}`));
  const nextItems = page.terms.filter((term) => {
    const key = `${term.taxonomy}:${term.canonicalId}`;
    if (known.has(key)) return false;
    known.add(key);
    return true;
  });
  const cursorDidNotProgress = page.nextCursor !== null && (page.nextCursor === currentCursor || deliveredCursors.has(page.nextCursor));
  return { items: [...existing, ...nextItems], nextCursor: cursorDidNotProgress ? null : page.nextCursor, cursorDidNotProgress };
}

export function taxonomySearchReducer(state: TaxonomySearchState, action: TaxonomySearchAction): TaxonomySearchState {
  switch (action.type) {
    case "reset":
      return { ...createTaxonomySearchState(state.taxonomy, action.query ?? ""), status: "idle" };
    case "retry":
      return { ...state, status: "idle", error: null };
    case "start":
      if (!canStartTaxonomyRequest(state, action.requestKey, action.cursor)) return state;
      return { ...state, query: action.query, inFlightKey: action.requestKey, status: action.cursor ? "loading-more" : "loading", error: null };
    case "success": {
      if (state.inFlightKey !== action.requestKey || state.query !== action.query) return state;
      if (action.cursor !== null && state.revision !== null && state.revision !== action.page.revision) {
        return { ...state, inFlightKey: null, nextCursor: null, status: "stale", error: "This taxonomy changed while loading. Search again to refresh its terms." };
      }
      const delivered = new Set(action.cursor === null ? [] : state.deliveredCursors);
      if (action.cursor !== null) delivered.add(action.cursor);
      const merged = action.cursor === null ? appendTaxonomyPage([], action.page, null, new Set()) : appendTaxonomyPage(state.terms, action.page, action.cursor, delivered);
      const cursorDidNotProgress = action.cursor === null ? false : merged.cursorDidNotProgress;
      return { ...state, terms: merged.items, nextCursor: merged.nextCursor, revision: action.page.revision, deliveredCursors: delivered, inFlightKey: null, status: merged.items.length === 0 ? "empty" : "ready", error: cursorDidNotProgress ? "The taxonomy cursor did not advance. Loaded terms remain available." : null };
    }
    case "failure": {
      if (state.inFlightKey !== action.requestKey || state.query !== action.query) return state;
      const status: TaxonomySearchStatus = action.status === 409 ? "stale" : action.status === 422 || action.status === 400 ? "invalid" : action.status === 503 ? "unavailable" : "unavailable";
      return { ...state, inFlightKey: null, nextCursor: action.cursor ? null : state.nextCursor, status, error: action.message };
    }
  }
}
