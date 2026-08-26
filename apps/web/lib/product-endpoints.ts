export const PRODUCT_ENDPOINTS = {
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
} as const;
