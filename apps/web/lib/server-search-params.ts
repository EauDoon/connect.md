export type ServerSearchParams = Record<string, string | string[] | undefined>;

/**
 * Preserve the exact query shape supplied by Next server pages while exposing
 * the standard URLSearchParams interface expected by the route-specific
 * parsers. Validation and normalization remain the parser's responsibility.
 */
export function serverSearchParams(input: ServerSearchParams): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, item);
    } else if (value !== undefined) {
      params.append(key, value);
    }
  }
  return params;
}
