export const AGENT_DIRECTORY_LIMIT = 20;

export type AgentDirectoryFilters = {
  q: string;
  profileHandle: string | null;
  cursor: string | null;
  invalidMessage: string | null;
};

function singleValue(params: URLSearchParams, name: string) {
  const values = params.getAll(name);
  return values.length === 1 ? values[0] : "";
}

export function agentDirectoryFiltersFromParams(params: URLSearchParams): AgentDirectoryFilters {
  const q = singleValue(params, "q").trim();
  const rawProfileHandle = singleValue(params, "profile_handle");
  const profileHandle = rawProfileHandle.trim() || null;
  const cursorValues = params.getAll("cursor");
  const rawCursor = cursorValues.length === 1 ? cursorValues[0] : null;
  const invalidCursor = cursorValues.length > 1 || (rawCursor !== null && (!rawCursor.trim() || rawCursor.length > 500));
  const cursor = invalidCursor ? null : rawCursor;
  if (q.length > 100) return { q, profileHandle, cursor: null, invalidMessage: "Search text must be 100 characters or fewer." };
  if (rawProfileHandle && !profileHandle) return { q, profileHandle: null, cursor: null, invalidMessage: "A profile handle cannot be empty." };
  if (profileHandle && profileHandle.length > 100) return { q, profileHandle, cursor: null, invalidMessage: "A profile handle must be 100 characters or fewer." };
  if (invalidCursor) return { q, profileHandle, cursor: null, invalidMessage: "This pagination link is not valid. Start the search again." };
  return { q, profileHandle, cursor, invalidMessage: null };
}

export function agentDirectoryHref(filters: Pick<AgentDirectoryFilters, "q" | "profileHandle">, cursor: string | null = null) {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.profileHandle) params.set("profile_handle", filters.profileHandle);
  if (cursor) params.set("cursor", cursor);
  const query = params.toString();
  return query ? `/agent-directory?${query}` : "/agent-directory";
}
