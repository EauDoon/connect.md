import { readFileSync } from "node:fs";
import React, { createElement, Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import GlobalError from "../app/error";

const source = readFileSync(new URL("../app/error.tsx", import.meta.url), "utf8");

vi.stubGlobal("React", React);

type ElementWithChildren = ReactElement<{
  children?: ReactNode;
  onClick?: () => void;
}>;

function findAction(node: ReactNode, label: string): ElementWithChildren | null {
  if (!isValidElement(node)) return null;
  const element = node as ElementWithChildren;
  if (element.props.children === label && typeof element.props.onClick === "function") return element;
  for (const child of Children.toArray(element.props.children)) {
    const action = findAction(child, label);
    if (action) return action;
  }
  return null;
}

describe("global error boundary privacy contract", () => {
  it("renders bounded recovery copy without exposing an error or digest", () => {
    const privateDetail = "private upstream failure: account=owner@example.test";
    const markup = renderToStaticMarkup(createElement(GlobalError, {
      error: Object.assign(new Error(privateDetail), { digest: "private-digest" }),
      reset: () => undefined,
    }));

    expect(markup).toContain("This view is temporarily unavailable.");
    expect(markup).toContain("Try again shortly. If the problem continues, return home and reopen the page.");
    expect(markup).toContain("Try again");
    expect(markup).toContain('href="/"');
    expect(markup).not.toContain(privateDetail);
    expect(markup).not.toContain("private-digest");
    expect(markup).not.toMatch(/\bAPI\b|server-side configuration|No document was changed/iu);
  });

  it("binds the retry control directly to Next's reset callback", () => {
    let resets = 0;
    const boundary = GlobalError({
      error: new Error("private failure"),
      reset: () => { resets += 1; },
    });
    const retry = findAction(boundary, "Try again");

    expect(retry).not.toBeNull();
    retry?.props.onClick?.();
    expect(resets).toBe(1);
  });

  it("does not log, persist, place in a URL, or interpolate raw errors", () => {
    expect(source.startsWith('"use client";')).toBe(true);
    expect(source).not.toMatch(/\b(?:console|logger|useEffect)\b/u);
    expect(source).not.toMatch(/\b(?:localStorage|sessionStorage|indexedDB|URL(?:SearchParams)?|location)\b/u);
    expect(source).not.toMatch(/\{\s*error(?:\?\.|\.|\s*\})/u);
  });
});
