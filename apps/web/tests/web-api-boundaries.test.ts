import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import type { ContactPolicy, ContactPolicyMode, OutreachPage, OutreachStatus, OutreachThread, PublicDocumentInventoryItem } from "../lib/product-types";

function source(relativePath: string) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

const typeContract: [ContactPolicy | null, ContactPolicyMode, OutreachPage | null, OutreachStatus, OutreachThread | null, PublicDocumentInventoryItem | null] = [null, "closed", null, "pending", null, null];

describe("web API module boundaries", () => {
  it("keeps endpoint ownership and shared type contracts explicit", async () => {
    const endpoints = await import("../lib/product-endpoints");
    expect(endpoints.PRODUCT_ENDPOINTS).toEqual({
      search: "/v1/search",
      taxonomies: "/v1/taxonomies",
      capabilities: "/v1/capabilities",
      me: "/v1/me",
      documents: "/v1/documents",
      changes: "/v1/changes",
      recentChanges: "/v1/changes/recent",
      delegations: "/v1/agent-grants",
      proposals: "/v1/proposals",
      contactPolicy: "/v1/contact-policy",
      outreach: "/v1/contact-requests",
      outreachInbox: "/v1/contact-requests/inbox",
      publicDocuments: "/v1/public-documents"
    });
    expect(typeContract[1]).toBe("closed");
  });

  it("keeps search, taxonomy, outreach, inventory, and agent implementations in their domain modules", () => {
    const search = source("../lib/public-search-api.ts");
    const searchContract = source("../lib/public-search-contract.ts");
    const taxonomy = source("../lib/taxonomy-api.ts");
    const outreach = source("../lib/outreach-api.ts");
    const inventory = source("../lib/public-inventory-api.ts");
    const agent = source("../lib/agent-api.ts");
    
    expect(search).toContain("export async function searchDirectory");
    expect(search).toContain('export { INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY, parseDirectorySearchResponse, SEARCH_MODES } from "@/lib/public-search-contract";');
    expect(searchContract).toContain("export function parseDirectorySearchResponse");
    expect(searchContract).not.toContain("public-search-api");
    expect(search).not.toContain("getContactPolicy");
    expect(search).not.toContain("createDelegation");
    expect(taxonomy).toContain("export async function listTaxonomies");
    expect(taxonomy).toContain("export function parseTaxonomyFacets");
    expect(taxonomy).not.toContain("searchDirectory");
    expect(outreach).toContain("withSubjectBoundToken");
    expect(outreach).toContain('"Idempotency-Key": idempotencyKey');
    expect(outreach).not.toContain("searchDirectory");
    expect(inventory).toContain("PRODUCT_ENDPOINTS.publicDocuments");
    expect(agent).toContain("export async function createDelegation");
    expect(agent).not.toContain("searchDirectory");
  });

  it("removes the obsolete facade and mixed tests", () => {
    const legacy = ["product", "api"].join("-");
    expect(existsSync(new URL(`../lib/${legacy}.ts`, import.meta.url))).toBe(false);
    expect(existsSync(new URL(`./${legacy}.test.ts`, import.meta.url))).toBe(false);
    expect(existsSync(new URL(`./${legacy}-boundaries.test.ts`, import.meta.url))).toBe(false);
  });
});
