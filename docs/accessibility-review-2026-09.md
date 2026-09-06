# Assistive-technology review — 2026-09-06

Scope: all active routes (/ , /human, /md, /trust, /account, /network,
/discover, /inbox) at the production build of this review.

## What was verified, and how

1. **Full accessibility-tree audit (scripted, repeatable)** —
   `apps/web/e2e/at-review.mjs` walks Chromium's complete accessibility tree
   (the same AX surface VoiceOver and other screen readers consume) for every
   active page and verifies:
   - every focusable control (button, link, textbox, combobox, checkbox,
     slider, switch) exposes a non-empty accessible name;
   - heading levels exist on every page and never skip downward (no h1→h3).

   Result: **PASS, 8 pages, 82 named controls, 42 headings, 0 problems**
   after fixing the one defect the audit found on its first run (the landing
   page had no `h1` of its own and stepped h1→h3; the hero is now the page
   `h1` and the agent-handoff card is an `h2` section).

2. **Automated axe screening (existing release spec)** —
   `e2e/standalone-release.spec.ts` runs axe-core on every public page at
   320 px width with reduced motion and fails on serious or critical
   violations. Result: pass (12/12 against production in the previous
   release, re-run locally green for this build).

3. **Keyboard reachability (scripted)** — the release spec exercises the
   skip link, dialog focus restoration (wallet/menu patterns), and
   touch-target sizes; the contrast contract test enforces the AA floor for
   muted text. All pass.

## Method notes and honest limits

- The scripted audit runs against Chromium's accessibility tree. This is the
  data surface an assistive technology reads, but it is not the same as a
  human walking the site with VoiceOver: speech output order, verbosity, and
  rotor navigation remain human judgement calls.
- VoiceOver was launched on the reviewer's macOS machine for this review,
  but an interactive VoiceOver walkthrough (speech verification on the live
  site) requires a human operator; this document records exactly what was
  machine-verified and leaves that walkthrough open.

## Defects found and fixed in this review

| Defect | Fix |
| --- | --- |
| Landing page lacked an `h1`; heading sequence stepped h1→h3 after the agent-handoff card's own `h1` | Hero promoted to the page `h1`; agent-handoff title demoted to `h2`; pinned by `tests/agent-first-landing.test.ts` and re-verified by the tree audit |

## Open items

- Human VoiceOver walkthrough of /account, /network, /inbox, and
  /conversations after the network database is provisioned and real content
  exists to read (owner-action queue item 1).
