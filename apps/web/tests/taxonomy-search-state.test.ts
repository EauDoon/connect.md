import { describe, expect, it } from "vitest";

import { appendTaxonomyPage, canStartTaxonomyRequest, createTaxonomySearchState, taxonomySearchReducer } from "../lib/taxonomy-search-state";
import type { TaxonomyPage, TaxonomyTerm } from "../lib/taxonomy-api";

const alias = "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const secondAlias = "tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
function term(filterValue = alias): TaxonomyTerm { return { taxonomy: "occupation", scheme: "isco", externalId: filterValue.slice(-6), canonicalId: `isco:${filterValue.slice(-6)}`, filterValue, label: "Product", labelConflict: false, vocabularyVersion: "2026", versionConflict: false }; }
function page(terms: TaxonomyTerm[], nextCursor: string | null = null, revision = 1): TaxonomyPage { return { terms, nextCursor, revision }; }

describe("taxonomy search state", () => {
  it("distinguishes a valid empty page from an unavailable request", () => {
    let state = createTaxonomySearchState("occupation");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "first", query: "none", cursor: null });
    state = taxonomySearchReducer(state, { type: "success", requestKey: "first", query: "none", cursor: null, page: page([]) });
    expect(state.status).toBe("empty");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "retry", query: "none", cursor: null });
    state = taxonomySearchReducer(state, { type: "failure", requestKey: "retry", query: "none", cursor: null, status: 503, message: "Registry unavailable" });
    expect(state.status).toBe("unavailable");
    expect(state.error).toBe("Registry unavailable");
  });

  it("prevents duplicate in-flight and delivered-cursor requests", () => {
    let state = createTaxonomySearchState("occupation");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "first", query: "", cursor: null });
    expect(canStartTaxonomyRequest(state, "duplicate", null)).toBe(false);
    state = taxonomySearchReducer(state, { type: "success", requestKey: "first", query: "", cursor: null, page: page([term()], "next") });
    expect(canStartTaxonomyRequest(state, "next-request", "next")).toBe(true);
    state = taxonomySearchReducer(state, { type: "start", requestKey: "next-request", query: "", cursor: "next" });
    state = taxonomySearchReducer(state, { type: "success", requestKey: "next-request", query: "", cursor: "next", page: page([term(secondAlias)], "next-2") });
    expect(state.deliveredCursors).toEqual(new Set(["next"]));
    expect(canStartTaxonomyRequest(state, "duplicate-next", "next")).toBe(false);
    expect(state.terms).toHaveLength(2);
  });

  it("fails closed when a cursor repeats or a registry revision changes", () => {
    const repeated = appendTaxonomyPage([term()], page([term(secondAlias)], "cursor"), "cursor", new Set(["cursor"]));
    expect(repeated.cursorDidNotProgress).toBe(true);
    expect(repeated.nextCursor).toBeNull();

    let state = createTaxonomySearchState("occupation");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "first", query: "", cursor: null });
    state = taxonomySearchReducer(state, { type: "success", requestKey: "first", query: "", cursor: null, page: page([term()], "next", 1) });
    state = taxonomySearchReducer(state, { type: "start", requestKey: "next", query: "", cursor: "next" });
    state = taxonomySearchReducer(state, { type: "success", requestKey: "next", query: "", cursor: "next", page: page([term(secondAlias)], "later", 2) });
    expect(state.status).toBe("stale");
    expect(state.terms).toHaveLength(1);
    expect(state.nextCursor).toBeNull();
  });

  it("ignores late responses for a different request key or query", () => {
    let state = createTaxonomySearchState("occupation");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "first", query: "old", cursor: null });
    const late = taxonomySearchReducer(state, { type: "success", requestKey: "other", query: "new", cursor: null, page: page([term()]) });
    expect(late).toBe(state);
  });

  it("maps malformed bounds and stale cursor responses to visible states", () => {
    let state = createTaxonomySearchState("occupation");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "first", query: "", cursor: null });
    state = taxonomySearchReducer(state, { type: "failure", requestKey: "first", query: "", cursor: null, status: 422, message: "Invalid query" });
    expect(state.status).toBe("invalid");
    state = taxonomySearchReducer(state, { type: "start", requestKey: "retry", query: "", cursor: null });
    state = taxonomySearchReducer(state, { type: "failure", requestKey: "retry", query: "", cursor: null, status: 409, message: "Stale cursor" });
    expect(state.status).toBe("stale");
  });
});
