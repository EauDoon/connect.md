import { PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";

export function findSourceMatches(source: string, query: string) {
  const matches: number[] = [];
  if (!query) return { matches, limited: false };
  if (query.length > 256) throw new Error("Search text is limited to 256 characters.");
  if (new TextEncoder().encode(source).length > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("Find and replace is limited to 128 KiB drafts.");
  let start = source.indexOf(query);
  while (start >= 0 && matches.length < 1000) {
    matches.push(start);
    start = source.indexOf(query, start + query.length);
  }
  return { matches, limited: start >= 0 };
}

export function replaceSourceMatches(source: string, query: string, replacement: string, target: number | "all") {
  const { matches, limited } = findSourceMatches(source, query);
  if (target === "all" && limited) throw new Error("More than 1,000 matches. Narrow the search before replacing all.");
  const chosen = target === "all" ? matches : [matches[target]];
  if (!chosen.length || chosen.some((offset) => offset === undefined)) throw new Error("Choose an available match before replacing.");
  const nextBytes = new TextEncoder().encode(source).length + chosen.length * (new TextEncoder().encode(replacement).length - new TextEncoder().encode(query).length);
  if (nextBytes > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("Replacement would exceed the 128 KiB draft limit.");
  let cursor = 0;
  const parts: string[] = [];
  for (const offset of chosen) { parts.push(source.slice(cursor, offset), replacement); cursor = offset + query.length; }
  parts.push(source.slice(cursor));
  return parts.join("");
}
