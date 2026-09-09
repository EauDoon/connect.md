/** Linear-time comparison of the changed region, not a minimal edit script. */
export function compareDrafts(before: string, after: string) {
  const previous = before.split("\n");
  const current = after.split("\n");
  let prefix = 0;
  while (prefix < previous.length && prefix < current.length && previous[prefix] === current[prefix]) prefix++;
  let suffix = 0;
  while (suffix < previous.length - prefix && suffix < current.length - prefix
    && previous[previous.length - suffix - 1] === current[current.length - suffix - 1]) suffix++;
  const removed = previous.slice(prefix, previous.length - suffix);
  const added = current.slice(prefix, current.length - suffix);
  return {
    identical: before === after,
    firstChangedLine: prefix + 1,
    removedLines: removed.length,
    addedLines: added.length,
    // Bound the rendered output independently of total line count and length.
    before: removed.slice(0, 80).join("\n").slice(0, 12000),
    after: added.slice(0, 80).join("\n").slice(0, 12000),
    truncated: removed.length > 80 || added.length > 80 || removed.join("\n").length > 12000 || added.join("\n").length > 12000,
  };
}
