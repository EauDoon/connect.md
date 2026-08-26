import { emptySearchFilters, searchFiltersFromParams, searchParamsFromFilters, type SearchFilters } from "@/lib/public-search-api";
import { publicProtocolUrl } from "@/lib/api";

export const REPRESENTATIVE_STATUSES = ["authorized_representative", "organization"] as const;
export const DEFAULT_REPRESENTATIVE_STATUS = REPRESENTATIVE_STATUSES[0];

export const REPRESENTATIVE_PROTOCOL_LINKS = [
  { href: "/llms.txt", label: "Protocol guide", detail: "Human- and agent-readable platform contract" },
  { href: "/.well-known/agent-card.json", label: "A2A agent card", detail: "Published agent capabilities and boundaries" },
  { href: "/.well-known/oauth-protected-resource/mcp", label: "MCP resource metadata", detail: "Protected-resource discovery for MCP clients" }
] as const;

export function representativeProtocolLinks() {
  return REPRESENTATIVE_PROTOCOL_LINKS.map((link) => ({
    ...link,
    href: publicProtocolUrl(link.href) ?? link.href,
  }));
}

export function representativeFiltersFromParams(params: URLSearchParams): SearchFilters {
  const parsed = searchFiltersFromParams(params);
  const representationStatus = REPRESENTATIVE_STATUSES.includes(parsed.representationStatus as (typeof REPRESENTATIVE_STATUSES)[number])
    ? parsed.representationStatus
    : DEFAULT_REPRESENTATIVE_STATUS;
  return { ...emptySearchFilters, ...parsed, kind: "profile", representationStatus };
}

export function representativeHref(filters: SearchFilters, offset = filters.offset) {
  const params = searchParamsFromFilters({ ...filters, kind: "profile", offset });
  const query = params.toString();
  return query ? `/representatives?${query}` : "/representatives";
}
