import { PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";

export type SearchOptions = { matchCase?: boolean; wholeWord?: boolean; bodyOnly?: boolean };

export function findSourceMatches(source: string, query: string, options: SearchOptions = {}) {
  const matches: number[] = [];
  if (!query) return { matches, limited: false };
  if (query.length > 256) throw new Error("Search text is limited to 256 characters.");
  if (new TextEncoder().encode(source).length > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("Find and replace is limited to 128 KiB drafts.");
  const bodyStart = options.bodyOnly && /^(?:\uFEFF)?---\r?\n/u.test(source)
    ? (source.match(/^(?:\uFEFF)?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/u)?.[0].length ?? source.length) : 0;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const pattern = new RegExp(escaped, options.matchCase === false ? "gi" : "g");
  for (const match of source.matchAll(pattern)) {
    const start = match.index;
    if (start < bodyStart) continue;
    if (options.wholeWord && (/[\p{L}\p{N}\p{M}_]$/u.test(source.slice(0, start)) || /^[\p{L}\p{N}\p{M}_]/u.test(source.slice(start + query.length)))) continue;
    if (matches.length === 1000) return { matches, limited: true };
    matches.push(start);
  }
  return { matches, limited: false };
}

export function replaceSourceMatches(source: string, query: string, replacement: string, target: number | "all", options: SearchOptions = {}) {
  const { matches, limited } = findSourceMatches(source, query, options);
  if (target === "all" && limited) throw new Error("More than 1,000 matches. Narrow the search before replacing all.");
  const chosen = target === "all" ? matches : [matches[target]];
  if (!chosen.length || chosen.some((offset) => offset === undefined)) throw new Error("Choose an available match before replacing.");
  const nextCodeUnits = source.length + chosen.length * (replacement.length - query.length);
  if (nextCodeUnits > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("Replacement would exceed the 128 KiB draft limit.");
  let cursor = 0;
  const parts: string[] = [];
  for (const offset of chosen) { parts.push(source.slice(cursor, offset), replacement); cursor = offset + query.length; }
  parts.push(source.slice(cursor));
  const result = parts.join("");
  // Search offsets use UTF-16. Recheck actual UTF-8 after replacements, including
  // edits that split or join surrogate pairs at a match boundary.
  if (new TextEncoder().encode(result).length > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("Replacement would exceed the 128 KiB draft limit.");
  return result;
}
