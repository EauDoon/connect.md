import { act, createElement, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

const authFixture = vi.hoisted(() => ({
  current: { configured: true, isLoaded: true, isSignedIn: true, subject: "alpha", getToken: async () => null } as {
    configured: boolean;
    isLoaded: boolean;
    isSignedIn: boolean;
    subject: string | null;
    getToken: () => Promise<string | null>;
  }
}));

vi.mock("@/components/auth-provider", () => ({ useConnectmdAuth: () => authFixture.current }));

import { DraftProvider, draftAuthBoundaryKey, useDraft } from "../components/draft-provider";

type FakeNode = {
  nodeType: number;
  nodeName: string;
  tagName?: string;
  namespaceURI?: string;
  ownerDocument?: FakeNode;
  documentElement?: FakeNode;
  defaultView?: object;
  addEventListener: () => void;
  removeEventListener: () => void;
  appendChild: () => void;
  insertBefore: () => void;
  removeChild: () => void;
  firstChild: null;
  lastChild: null;
  textContent: string;
};

function fakeReactContainer() {
  const noop = () => undefined;
  const documentNode: FakeNode = {
    nodeType: 9,
    nodeName: "#document",
    addEventListener: noop,
    removeEventListener: noop,
    appendChild: noop,
    insertBefore: noop,
    removeChild: noop,
    firstChild: null,
    lastChild: null,
    textContent: ""
  };
  const container: FakeNode = {
    nodeType: 1,
    nodeName: "DIV",
    tagName: "DIV",
    namespaceURI: "http://www.w3.org/1999/xhtml",
    ownerDocument: documentNode,
    addEventListener: noop,
    removeEventListener: noop,
    appendChild: noop,
    insertBefore: noop,
    removeChild: noop,
    firstChild: null,
    lastChild: null,
    textContent: ""
  };
  documentNode.documentElement = container;
  const windowNode = { document: documentNode, HTMLIFrameElement: class HTMLIFrameElement {}, addEventListener: noop, removeEventListener: noop };
  documentNode.defaultView = windowNode;
  return { container, documentNode, windowNode };
}

describe("dual-mode canonical draft continuity", () => {
  it("keeps ordinary current-page links, responsive touch targets, and a reduced-motion-aware active rail", async () => {
    const { ModeSwitch } = await import("../components/mode-switch");
    const markup = renderToStaticMarkup(createElement(DraftProvider, null, createElement(ModeSwitch, { mode: "human" })));

    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('href="/human"');
    expect(markup).toContain('href="/md"');
    expect(markup).toContain("min-h-11");
    expect(markup).toContain("inline-flex w-full");
    expect(markup).toContain("flex-1");
    expect(markup).toContain("sm:min-w-32");
    expect(markup).toContain("whitespace-nowrap");
    expect(markup).not.toContain("truncate");
    expect(markup).toContain("Canonical buffer connected");
    expect(markup).not.toContain('role="tab"');

    const source = readFileSync(new URL("../components/mode-switch.tsx", import.meta.url), "utf8");
    expect(source).toContain("useReducedMotion()");
    expect(source).toContain('layoutId="editing-mode-active-rail"');
    expect(source).toContain("duration: reducedMotion ? 0 : 0.22");
  });

  it("flushes every mounted guided buffer synchronously before either editing-mode link routes", () => {
    const modeSwitch = readFileSync(new URL("../components/mode-switch.tsx", import.meta.url), "utf8");
    const humanBuilder = readFileSync(new URL("../components/human-builder.tsx", import.meta.url), "utf8");

    expect(modeSwitch).toContain("onClick={onBeforeNavigate}");
    expect(humanBuilder).toContain('<ModeSwitch mode="human" onBeforeNavigate={flushBufferedFields} />');
    expect(humanBuilder).toContain("for (const flush of [...bufferedFlushersRef.current]) flush();");
    expect(humanBuilder.indexOf("flushBufferedFields();")).toBeLessThan(humanBuilder.indexOf("setHumanStage(stage);"));
  });

  it("keeps only the non-sensitive Human chapter in memory and resets it with an authenticated-subject draft reset", () => {
    const provider = readFileSync(new URL("../components/draft-provider.tsx", import.meta.url), "utf8");
    const humanBuilder = readFileSync(new URL("../components/human-builder.tsx", import.meta.url), "utf8");

    expect(provider).toContain('useState<HumanJourneyStage>("foundation")');
    expect(provider).toContain('updateHumanStage("foundation");');
    expect(provider).toContain('humanStage: maskDraft ? "foundation" as const : humanStage');
    expect(provider).toContain("if (!maskDraftRef.current) updateHumanStage(stage);");
    expect(humanBuilder).toContain("humanStage: activeStage");
    expect(humanBuilder).not.toContain('useState<HumanJourneyStage>("foundation")');
    expect(provider).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/u);
  });

  it("remounts draft descendants at every auth boundary without breaking same-subject mode continuity", async () => {
    expect([
      draftAuthBoundaryKey(false, true, null),
      draftAuthBoundaryKey(true, false, "alpha"),
      draftAuthBoundaryKey(true, true, null),
      draftAuthBoundaryKey(true, true, "alpha")
    ]).toEqual(["unconfigured", "loading", "signed-out", "user:alpha"]);

    const previousDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
    const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
    const previousActEnvironment = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    const { container, documentNode, windowNode } = fakeReactContainer();
    Object.defineProperty(globalThis, "document", { configurable: true, value: documentNode });
    Object.defineProperty(globalThis, "window", { configurable: true, value: windowNode });
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    const { createRoot } = await import("react-dom/client");
    const root = createRoot(container as unknown as Element);
    let mountSequence = 0;
    let observed = { surface: "", instance: 0, adjacent: "", markdown: "", masked: false, stage: "foundation" };
    let setMarkdown: (markdown: string) => void = () => undefined;
    let setHumanStage: (stage: "foundation" | "shape" | "review" | "release") => void = () => undefined;
    let setAdjacent: (value: string) => void = () => undefined;

    function HumanSurface() {
      const draft = useDraft();
      const [instance] = useState(() => ++mountSequence);
      const [adjacent, updateAdjacent] = useState("clean");
      observed = { surface: "human", instance, adjacent, markdown: draft.markdown, masked: draft.masked, stage: draft.humanStage };
      setMarkdown = draft.setMarkdown;
      setHumanStage = draft.setHumanStage;
      setAdjacent = updateAdjacent;
      return null;
    }

    function MarkdownSurface() {
      const draft = useDraft();
      observed = { surface: "markdown", instance: 0, adjacent: "clean", markdown: draft.markdown, masked: draft.masked, stage: draft.humanStage };
      return null;
    }

    const renderSurface = (surface: "human" | "markdown") => createElement(DraftProvider, null, surface === "human" ? createElement(HumanSurface) : createElement(MarkdownSurface));

    try {
      await act(async () => { root.render(renderSurface("human")); });
      await act(async () => {
        setMarkdown("# Alpha private Markdown");
        setHumanStage("review");
        setAdjacent("alpha-owned-filename.pdf");
      });
      expect(observed).toMatchObject({ surface: "human", adjacent: "alpha-owned-filename.pdf", markdown: "# Alpha private Markdown\n", stage: "review" });

      await act(async () => { root.render(renderSurface("markdown")); });
      expect(observed).toMatchObject({ surface: "markdown", markdown: "# Alpha private Markdown\n", stage: "review" });
      await act(async () => { root.render(renderSurface("human")); });
      await act(async () => { setAdjacent("alpha-owned-provenance"); });
      const sameSubjectInstance = observed.instance;

      authFixture.current = { ...authFixture.current, isLoaded: false };
      await act(async () => { root.render(renderSurface("human")); });
      expect(observed.instance).toBeGreaterThan(sameSubjectInstance);
      expect(observed).toMatchObject({ adjacent: "clean", masked: true, stage: "foundation" });
      expect(observed.markdown).not.toContain("Alpha private");
      expect(observed.adjacent).not.toContain("alpha-owned");

      const loadingInstance = observed.instance;
      authFixture.current = { ...authFixture.current, isLoaded: true, subject: "beta" };
      await act(async () => { root.render(renderSurface("human")); });
      expect(observed.instance).toBeGreaterThan(loadingInstance);
      expect(observed).toMatchObject({ adjacent: "clean", markdown: expect.not.stringContaining("Alpha private"), stage: "foundation" });

      await act(async () => {
        setMarkdown("# Beta private Markdown");
        setAdjacent("beta-owned-inventory");
      });
      const betaInstance = observed.instance;
      authFixture.current = { ...authFixture.current, isSignedIn: false, subject: null };
      await act(async () => { root.render(renderSurface("human")); });
      expect(observed.instance).toBeGreaterThan(betaInstance);
      expect(observed.markdown).not.toContain("Beta private");
      expect(observed.adjacent).toBe("clean");

      const signedOutInstance = observed.instance;
      authFixture.current = { ...authFixture.current, configured: false };
      await act(async () => { root.render(renderSurface("human")); });
      expect(observed.instance).toBeGreaterThan(signedOutInstance);
      expect(observed).toMatchObject({ adjacent: "clean", masked: true, stage: "foundation" });
    } finally {
      await act(async () => { root.unmount(); });
      if (previousDocument) Object.defineProperty(globalThis, "document", previousDocument);
      else Reflect.deleteProperty(globalThis, "document");
      if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
      else Reflect.deleteProperty(globalThis, "window");
      (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
      authFixture.current = { configured: true, isLoaded: true, isSignedIn: true, subject: "alpha", getToken: async () => null };
    }
  });
});
