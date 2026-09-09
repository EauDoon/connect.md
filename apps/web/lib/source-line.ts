export function sourceLineRange(markdown: string, line: number): { start: number; end: number } | null {
  if (!Number.isSafeInteger(line) || line < 1) return null;
  let start = 0;
  for (let index = 1; index < line; index += 1) {
    const end = markdown.indexOf("\n", start);
    if (end < 0) return null;
    start = end + 1;
  }
  const end = markdown.indexOf("\n", start);
  return { start, end: end < 0 ? markdown.length : end };
}
