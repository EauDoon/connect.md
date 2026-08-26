"""Fail-closed repository checks for the Human Mode interaction contract."""

from __future__ import annotations

import re
from pathlib import Path

try:
    from .platform_checker_source import (
        append_error,
        read_anchor_source,
        require_source_markers,
    )
except ImportError:
    from platform_checker_source import (
        append_error,
        read_anchor_source,
        require_source_markers,
    )


def human_mode_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/components/human-builder.tsx": {
            "Experience guided cards": 'GuidedEntriesEditor kind="experience"',
            "Education guided cards": 'GuidedEntriesEditor kind="education"',
            "buffered field module": 'from "@/components/human-buffered-fields"',
            "guided field module": 'from "@/components/human-guided-fields"',
            "native document-kind radio": 'type="radio"',
            "shared document-kind radio name": 'name="human-document-kind"',
            "document-kind checked binding": "checked={selected}",
            "visible radio-card focus": "focus-within:outline",
            "document conversion guard": "shouldConfirmDraftReplacement(markdown, kind, savedDocument)",
            "single active progressive stages": 'activeStage === "foundation"',
            "accessible current-stage stepper": 'aria-current={active ? "step" : undefined}',
            "explicit back chapter action": "Back to {previous.label}",
            "explicit next review release actions": 'shape: "Review document"',
            "stage heading target": "id={`human-stage-${stage}-title`} tabIndex={-1}",
            "one-shot pending stage focus": "pendingStageFocusRef.current !== activeStage",
            "reduced-motion-aware journey scroll": 'behavior: reducedMotion ? "auto" : "smooth"',
            "journey heading focus transfer": "heading?.focus({ preventScroll: true });",
            "one-shot focus reset": "pendingStageFocusRef.current = null;",
            "mobile compact stage dock": 'aria-label="Compact stage controls"',
            "mobile dock breakpoint": "backdrop-blur md:hidden",
            "44px mobile controls": "min-h-11",
            "buffer registry provider": "<BufferedCommitRegistry.Provider value={registerBufferedFlush}>",
            "flush before stage change": "function activateStage(stage: HumanJourneyStage) {\n    flushBufferedFields();",
            "stage state transition": "setHumanStage(stage);",
            "canonical Markdown ref": "const canonicalMarkdownRef = useRef(markdown);",
            "buffered canonical serialization": "patchHumanFields(canonicalMarkdownRef.current, kind, patchFields)",
        },
        "apps/web/components/human-buffered-fields.tsx": {
            "buffer registry context": "export const BufferedCommitRegistry",
            "buffered input cleanup flush": "useEffect(() => () => { committer.flush(); }, [committer]);",
            "buffered input blur flush": "onBlur={() => { const committed = commitBufferedInputValue(draftValue); setDraftValue(committed); committer.flush(); }}",
            "buffer registration": "return registerBufferedFlush(() => committer.flush());",
            "exact pending input": "nextBufferedInputValue(event.target.value)",
        },
        "apps/web/components/human-guided-fields.tsx": {
            "advanced raw Markdown disclosure": "Advanced Markdown preserved",
            "card removal": "removeGuidedEntry",
            "card reordering": "swapGuidedEntries",
            "guided experience distinction": 'kind === "experience" ? "role" : "education"',
            "work-mode fieldset": '<fieldset className="sm:col-span-2">',
            "work-mode legend": '<legend className="mb-1.5 block text-sm font-medium text-white">Work modes</legend>',
        },
        "apps/web/lib/guided-sections.ts": {
            "range-scoped replacement": "export function replaceGuidedEntry",
            "append": "export function appendGuidedEntry",
            "range-scoped removal": "export function removeGuidedEntry",
            "range-preserving reorder": "export function swapGuidedEntries",
            "raw block classification": '{ type: "raw"',
        },
        "apps/web/lib/human-input.ts": {
            "debounced input buffer": "export const HUMAN_INPUT_DEBOUNCE_MS = 180;",
            "exact pending input": "export function nextBufferedInputValue(nextValue: string)",
            "timer flush": "if (delay !== null) timer = setTimeout(flush, delay);",
            "idempotent buffered flush": "if (pendingValue === null) return false;",
            "intentional normalization only": "normalise: commitBufferedInputValue",
        },
        "apps/web/components/load-existing-panel.tsx": {
            "bounded inventory states": 'useState<"idle" | "loading" | "success" | "error">',
            "loading state": "Loading saved documents…",
            "empty state": "Continue below to create your first one.",
            "error retry": "Retry list",
            "stale request guard": "inventoryRequestRef.current !== requestId",
            "account-change guard": "authIdentityRef.current !== authIdentity",
            "dirty-draft guard": "shouldConfirmDraftReplacement",
        },
        "apps/web/tests/guided-sections.test.ts": {
            "losslessness suite": "lossless guided career sections",
            "raw Markdown fixture": "<!-- exact raw anchor -->",
            "exact removal assertion": 'toBe("RAW PREFIX\\n\\n\\n\\nRAW SUFFIX")',
        },
        "apps/web/tests/human-builder-v2.test.ts": {
            "guided card surface test": "Advanced Markdown preserved",
            "reorder surface test": "swapGuidedEntries",
            "native radio assertion": 'name="human-document-kind"',
            "manual radio-role rejection": "not.toContain('role=\"radio\"')",
            "journey scroll-order assertion": "expect(focusIndex).toBeGreaterThan(scrollIndex)",
        },
        "apps/web/tests/human-input.test.ts": {
            "timer flush test": "commits after its debounce",
            "cleanup before blur test": "cleans up before blur",
            "blur cleanup deduplication": "does not duplicate a pending scalar after blur then cleanup",
            "exact multiline preservation": "keeps exact multiline and empty removal values",
            "malformed Markdown fail-closed": "keeps malformed Markdown fail-closed",
            "unmount cleanup flush wiring": "useEffect(() => () => { committer.flush(); }, [committer]);",
        },
        "apps/web/tests/load-existing-panel.test.ts": {
            "inventory loading test": "Loading saved documents…",
            "inventory empty test": "Continue below to create your first one.",
            "inventory error test": "Retry list",
            "late identity guard test": "authIdentityRef.current !== authIdentity",
        },
        "apps/web/e2e/public-release.spec.ts": {
            "anonymous landing and discovery browser gate": 'test("anonymous landing and discovery expose safe current paths"',
            "profile Markdown parity browser gate": 'test("profile HTML and canonical Markdown remain byte-parity linked"',
            "self-hosted Markdown browser gate": 'test("self-hosted Markdown Mode loads and shares the Guided draft"',
            "Human Mode stage browser gate": 'test("Human Mode preserves the canonical stage journey and signed-out release boundary"',
            "anonymous mobile boundary browser gate": 'test("anonymous mobile navigation and auth boundaries remain keyboard-safe"',
            "anonymous private-route browser gate": 'test("anonymous private routes fail closed without protected API reads"',
            "WCAG A or AA accessibility browser gate": 'test("public release pages have no WCAG A or AA accessibility violations"',
            "aria-current browser assertion": 'button[aria-current="step"]',
            "focus transfer browser assertion": "toBeFocused()",
            "buffered narrative browser fixture": 'const editedNarrative = "Browser progression keeps this canonical narrative.";',
            "buffered narrative stage action": 'reviewButton.dispatchEvent("click")',
            "canonical preview browser assertion": 'page.locator(".light-preview")',
            "reduced-motion mobile browser assertion": 'page.emulateMedia({ reducedMotion: "reduce" })',
            "signed-out no-write browser assertion": "assertNoExternalWritesOrCredentials(audit);",
            "protected request browser assertion": "expect(protectedRequests).toEqual([]);",
            "accessibility helper invocation": "await assertA11y(page);",
        },
        "apps/web/e2e/production-runtime.mjs": {
            "production build receipt prerequisite": "loadAndValidateBrowserReleaseBuildReceipt();",
            "same-origin fixture environment": "const environment = safeEnvironment(apiOrigin, proxyOrigin);",
            "Playwright production config": '"--config=playwright.config.ts"',
            "browser gate failure": 'if (result.code !== 0) throw new Error("browser release gate failed");',
        },
        "apps/web/package.json": {
            "production browser script": '"test:e2e": "node e2e/production-harness.mjs"',
        },
    }
    for relative_path, markers in required.items():
        source = read_anchor_source(root, relative_path, errors)
        require_source_markers(source, relative_path, markers, errors)
    e2e_path = "apps/web/e2e/public-release.spec.ts"
    e2e = read_anchor_source(root, e2e_path, errors)
    expected_tests = [
        "anonymous landing and discovery expose safe current paths",
        "public crawler contracts expose only bounded canonical sitemap URLs",
        "default-off public recruiting routes are hidden before release enablement",
        "profile HTML and canonical Markdown remain byte-parity linked",
        "self-hosted Markdown Mode loads and shares the Guided draft",
        "Human Mode preserves the canonical stage journey and signed-out release boundary",
        "anonymous mobile navigation and auth boundaries remain keyboard-safe",
        "anonymous private routes fail closed without protected API reads",
        "public release pages have no WCAG A or AA accessibility violations",
    ]
    actual_tests = re.findall(r'^test\("([^"]+)"', e2e, flags=re.MULTILINE)
    missing_tests = [
        test_name for test_name in expected_tests if test_name not in actual_tests
    ]
    unexpected_tests = [
        test_name for test_name in actual_tests if test_name not in expected_tests
    ]
    if len(actual_tests) != len(expected_tests) or missing_tests or unexpected_tests:
        details = [
            f"expected exactly {len(expected_tests)} named tests, found {len(actual_tests)}"
        ]
        if missing_tests:
            details.append("missing: " + ", ".join(missing_tests))
        if unexpected_tests:
            details.append("unexpected: " + ", ".join(unexpected_tests))
        append_error(
            errors,
            f"repository.anchors.{e2e_path}",
            "must retain exactly nine named production Playwright release tests; "
            + "; ".join(details),
        )
    builder_path = "apps/web/components/human-builder.tsx"
    builder = read_anchor_source(root, builder_path, errors)
    stage_start = builder.find("function activateStage(stage: HumanJourneyStage)")
    stage_end = builder.find("\n  useEffect(() => {", stage_start)
    stage_change = (
        builder[stage_start:stage_end] if stage_start >= 0 and stage_end >= 0 else ""
    )
    flush_position = stage_change.find("flushBufferedFields();")
    set_stage_position = stage_change.find("setHumanStage(stage);")
    if (
        flush_position < 0
        or set_stage_position < 0
        or flush_position >= set_stage_position
    ):
        append_error(
            errors,
            f"repository.anchors.{builder_path}",
            "must flush BufferedCommitRegistry before any stage or release transition",
        )
    if len(re.findall(r'\{activeStage === "[^"]+" && <JourneyChapter', builder)) != 4:
        append_error(
            errors,
            f"repository.anchors.{builder_path}",
            "must render exactly one of the four progressive Human Mode stages",
        )
    if "monaco" in builder.lower():
        append_error(
            errors,
            f"repository.anchors.{builder_path}",
            "must not import or embed Monaco in Human Mode",
        )
    return errors
