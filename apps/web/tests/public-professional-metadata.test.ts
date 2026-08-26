import { describe, expect, it } from "vitest";

import type { DocumentResponse } from "../lib/api";
import { personJsonLd, publicDocumentView, safeJsonLd } from "../lib/public-document";

describe("public professional metadata", () => {
  const markdown = `---
schema: connect.md/profile
schema_version: 2
id: doc-1
owner_id: owner-1
handle: ari-chen
version: 3
updated_at: '2026-08-03T00:00:00Z'
name: Ari Chen
headline: Product leader
occupations:
  - scheme: connectmd-occupation
    id: product-manager
    label: Product manager
industries:
  - scheme: connectmd-industry
    id: fintech
    label: Financial technology
location:
  scheme: geonames
  id: '1880252'
  label: Singapore
  country_code: SG
  city: Singapore
  timezone: Asia/Singapore
skills:
  - scheme: connectmd-skill
    id: payments
    label: Payments
languages:
  - scheme: iso-639-1
    id: en
    label: English
    proficiency: native_or_bilingual
seniority:
  scheme: connectmd-seniority
  id: executive
  label: Executive
work_modes: [hybrid]
availability:
  status: available_now
open_to:
  - scheme: connectmd-opportunity
    id: advisory
    label: Advisory
organizations:
  - scheme: connectmd-organization
    id: connect-md
    label: connect.md
    relationship: current_employer
  - scheme: connectmd-organization
    id: former-co
    label: Former Co
    relationship: past_employer
public_representation:
  status: authorized_representative
  representative:
    scheme: connectmd-agent
    id: agent-one
    label: Agent One
  public_url: https://example.test/agent-one
contact:
  disclosure: public
  channels:
    - type: url
      value: https://example.test/contact
      label: Request contact
visibility: public
---
# Ari Chen

## About

Profile.

## Experience

Experience.

## Skills

- Payments
`;
  const document: DocumentResponse = { id: "doc-1", kind: "profile", identifier: "ari-chen", visibility: "public", version: 3, etag: "profile-etag-v3", updated_at: "2026-08-03T00:00:00Z", markdown, markdown_url: "/v1/profiles/ari-chen.md" };

  it("projects schema-v2 discovery, representation, and contact fields", () => {
    expect(publicDocumentView(document)).toMatchObject({
      schemaVersion: 2,
      occupationIds: ["connectmd-occupation:product-manager"],
      industries: ["Financial technology"],
      locationCountryCode: "SG",
      representationStatus: "authorized_representative",
      representativeName: "Agent One",
      representativeUrl: "https://example.test/agent-one",
      contactDisclosure: "public",
      contactRoutes: [{ label: "Request contact", url: "https://example.test/contact" }]
    });
  });

  it("escapes markup in JSON-LD payloads", () => {
    expect(safeJsonLd({ value: "</script>" })).not.toContain("</script>");
  });

  it("omits owner-declared organization relationships from JSON-LD worksFor", () => {
    expect(personJsonLd(document, "https://connect.md/p/ari-chen")).not.toHaveProperty("worksFor");
  });
});

