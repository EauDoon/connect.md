/** Keep the input buffer exact while typing; Markdown normalization belongs to an intentional commit. */
export const HUMAN_INPUT_DEBOUNCE_MS = 180;

export function nextBufferedInputValue(nextValue: string) {
  return nextValue;
}

export function commitBufferedInputValue(value: string) {
  return value.trim();
}

type BufferedCommitterOptions = {
  delay?: number | null;
  normalise?: (value: string) => string;
};

/**
 * Holds the latest local control value until it is deliberately committed.
 * `flush` is idempotent: after a timer, blur, or cleanup has committed the
 * pending value, later flushes do nothing.
 */
export function createBufferedCommitter(commit: (value: string) => void, { delay = null, normalise = (value) => value }: BufferedCommitterOptions = {}) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pendingValue: string | null = null;
  const cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  const flush = () => {
    cancel();
    if (pendingValue === null) return false;
    const value = pendingValue;
    pendingValue = null;
    commit(normalise(value));
    return true;
  };
  return {
    update(value: string) {
      pendingValue = value;
      cancel();
      if (delay !== null) timer = setTimeout(flush, delay);
    },
    flush,
    cancel
  };
}

export function createBufferedInputCommitter(commit: (value: string) => void, delay = HUMAN_INPUT_DEBOUNCE_MS) {
  return createBufferedCommitter(commit, { delay, normalise: commitBufferedInputValue });
}
