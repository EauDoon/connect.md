export type CursorPage<T> = { items: T[]; nextCursor: string | null };

export function appendCursorPage<T extends { id: string }>(
  existing: T[],
  page: CursorPage<T>,
  currentCursor: string,
  deliveredCursors: ReadonlySet<string> = new Set(),
) {
  const known = new Set(existing.map((item) => item.id));
  const cursorDidNotProgress =
    page.nextCursor !== null &&
    (page.nextCursor === currentCursor || deliveredCursors.has(page.nextCursor));
  return {
    items: [
      ...existing,
      ...page.items.filter((item) => {
        if (known.has(item.id)) return false;
        known.add(item.id);
        return true;
      }),
    ],
    nextCursor: cursorDidNotProgress ? null : page.nextCursor,
    cursorDidNotProgress,
  };
}
