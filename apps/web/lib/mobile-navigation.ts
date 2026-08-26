export type KeyboardListenerTarget = Pick<EventTarget, "addEventListener" | "removeEventListener">;

export function bindEscapeToCloseMobileNavigation(target: KeyboardListenerTarget, onEscape: () => void) {
  const onKeyDown: EventListener = (event) => {
    if ((event as KeyboardEvent).key === "Escape") onEscape();
  };
  target.addEventListener("keydown", onKeyDown);
  return () => target.removeEventListener("keydown", onKeyDown);
}

export function closeMobileNavigationAndRestoreFocus(close: () => void, toggle: Pick<HTMLElement, "focus"> | null) {
  close();
  toggle?.focus();
}
