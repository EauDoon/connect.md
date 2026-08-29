"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, CheckCircle2, Compass, Eye, FileText, MapPin, ShieldAlert, Sparkles } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BufferedCommitRegistry, BufferedInput, BufferedTextarea, FieldLabel, type BufferedFlush, type BufferedFlushRegistry } from "@/components/human-buffered-fields";
import { GuidedEntriesEditor, StructuredV2Fields } from "@/components/human-guided-fields";
import { useDraft } from "@/components/draft-provider";
import { ModeSwitch } from "@/components/mode-switch";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/field";
import { shouldConfirmDraftReplacement } from "@/lib/draft-replacement";
import { HUMAN_JOURNEY, humanJourneyPosition, type HumanJourneyStage } from "@/lib/human-journey";
import { frontmatterParseIssue, humanFieldsFromMarkdown, patchHumanFields, type DocumentKind, type HumanFields } from "@/lib/markdown";
import { SCHEMA_LIMITS, hasValidationErrors, validateDraft } from "@/lib/validation";
import { cn } from "@/lib/utils";

const MarkdownPreview = dynamic(() => import("@/components/markdown-preview").then((module) => module.MarkdownPreview));
const PublishPanel = dynamic(() => import("@/components/publish-panel").then((module) => module.PublishPanel));
const ValidationPanel = dynamic(() => import("@/components/validation-panel").then((module) => module.ValidationPanel));

const documentOptions: Array<{ kind: DocumentKind; title: string; description: string; icon: typeof Sparkles }> = [
  { kind: "profile", title: "Profile", description: "A living public introduction", icon: Sparkles },
  { kind: "resume", title: "Resume", description: "A structured career record", icon: FileText }
];

function JourneyChapter({ stage, title, description, children }: { stage: HumanJourneyStage; title: string; description: string; children: ReactNode }) {
  const step = HUMAN_JOURNEY.find((item) => item.id === stage);
  return <section
    id={`human-stage-${stage}`}
    aria-labelledby={`human-stage-${stage}-title`}
    className="min-w-0 rounded-3xl border border-acid/20 bg-acid/[.035] p-3 shadow-[0_0_0_1px_rgba(215,255,95,.035)] sm:p-8 lg:p-10"
  >
    <div className="min-w-0 max-w-3xl">
      <span className="inline-flex items-center gap-2 text-[11px] font-bold uppercase tracking-[.16em] text-acid"><span className="grid size-7 place-items-center rounded-full border border-acid/25 bg-acid/10 text-[10px]">{step?.number}</span>{step?.label}</span>
      <h2 id={`human-stage-${stage}-title`} tabIndex={-1} className="mt-5 scroll-mt-24 font-display text-3xl font-semibold leading-[.98] tracking-[-.04em] text-white outline-none focus-visible:ring-2 focus-visible:ring-acid/60 sm:text-4xl">{title}</h2>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-mist sm:text-base">{description}</p>
    </div>
    <div className="mt-8">{children}</div>
  </section>;
}

function ChapterNavigation({ stage, onNavigate }: { stage: HumanJourneyStage; onNavigate: (stage: HumanJourneyStage) => void }) {
  const position = HUMAN_JOURNEY.findIndex((step) => step.id === stage);
  const previous = HUMAN_JOURNEY[position - 1];
  const next = HUMAN_JOURNEY[position + 1];
  const nextLabel: Record<HumanJourneyStage, string> = {
    foundation: "Next: shape",
    shape: "Review document",
    review: "Download document",
    release: ""
  };

  return <nav aria-label={`${HUMAN_JOURNEY[position]?.label ?? "Human Mode"} chapter navigation`} className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-5">
    {previous ? <Button variant="ghost" onClick={() => onNavigate(previous.id)}>Back to {previous.label}</Button> : <span aria-hidden />}
    {next ? <Button onClick={() => onNavigate(next.id)}>{nextLabel[stage]} <ArrowRight className="size-4" aria-hidden /></Button> : <Button variant="secondary" onClick={() => onNavigate("review")}>Back to review</Button>}
  </nav>;
}

export function HumanBuilder() {
  const { kind, markdown, savedDocument, humanStage: activeStage, setHumanStage, setKind, setMarkdown } = useDraft();
  const fields = useMemo(() => humanFieldsFromMarkdown(markdown, kind), [kind, markdown]);
  const issues = useMemo(() => validateDraft(markdown, kind), [kind, markdown]);
  const guidedEditIssue = useMemo(() => frontmatterParseIssue(markdown), [markdown]);
  const guidedEditsBlocked = guidedEditIssue !== null;
  const identifierImmutable = savedDocument?.kind === kind;
  const reducedMotion = useReducedMotion();
  const [previewVersion, setPreviewVersion] = useState(0);
  const [previewUpdating, setPreviewUpdating] = useState(false);
  const canonicalMarkdownRef = useRef(markdown);
  const bufferedFlushersRef = useRef(new Set<BufferedFlush>());
  const pendingStageFocusRef = useRef<HumanJourneyStage | null>(null);
  const journeyPosition = humanJourneyPosition(activeStage);
  const readyForDownload = !guidedEditsBlocked && !hasValidationErrors(issues);
  canonicalMarkdownRef.current = markdown;

  const registerBufferedFlush = useCallback<BufferedFlushRegistry>((flush) => {
    bufferedFlushersRef.current.add(flush);
    return () => bufferedFlushersRef.current.delete(flush);
  }, []);

  const flushBufferedFields = useCallback(() => {
    for (const flush of [...bufferedFlushersRef.current]) flush();
  }, []);

  useEffect(() => {
    if (reducedMotion) {
      setPreviewUpdating(false);
      return;
    }
    setPreviewUpdating(true);
    const timer = window.setTimeout(() => {
      setPreviewVersion((version) => version + 1);
      setPreviewUpdating(false);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [kind, markdown, reducedMotion]);

  function patch(patchFields: Partial<HumanFields>) {
    if (guidedEditsBlocked) return;
    const nextMarkdown = patchHumanFields(canonicalMarkdownRef.current, kind, patchFields);
    canonicalMarkdownRef.current = nextMarkdown;
    setMarkdown(nextMarkdown);
  }

  function activateStage(stage: HumanJourneyStage) {
    flushBufferedFields();
    if (stage === activeStage) return;
    pendingStageFocusRef.current = stage;
    setHumanStage(stage);
  }

  useEffect(() => {
    if (pendingStageFocusRef.current !== activeStage) return;
    const heading = document.getElementById(`human-stage-${activeStage}-title`);
    heading?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    heading?.focus({ preventScroll: true });
    pendingStageFocusRef.current = null;
  }, [activeStage, reducedMotion]);

  function selectDocumentKind(nextKind: DocumentKind) {
    if (nextKind === kind) {
      activateStage("shape");
      return;
    }
    if (shouldConfirmDraftReplacement(markdown, kind, savedDocument)
      && !window.confirm("Convert this local draft to the other document type? Unsaved content and saved-document identity will be replaced.")) return;
    setKind(nextKind);
    activateStage("shape");
  }

  return (
    <main className="overflow-x-hidden pb-14">
      <section className="relative mx-auto min-w-0 max-w-7xl px-5 pb-7 pt-9 lg:px-8 lg:pb-10 lg:pt-14">
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-3 -z-10 h-64 bg-[radial-gradient(ellipse_at_60%_0%,rgba(215,255,95,.14),transparent_62%)]" />
        <motion.div initial={false} animate="visible" variants={{ hidden: {}, visible: { transition: { staggerChildren: 0.09 } } }}>
          <motion.p variants={{ hidden: { opacity: 0, y: 10 }, visible: { opacity: 1, y: 0 } }} className="eyebrow">Human mode · guided composition</motion.p>
          <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
            <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }}>
              <h1 className="max-w-3xl font-display text-4xl font-semibold leading-[.94] tracking-[-.055em] text-white sm:text-6xl">Make your work read like a signal.</h1>
              <p className="mt-4 max-w-2xl text-base leading-7 text-mist">Move through the essentials, then watch the same canonical Markdown respond as a finished document. Download it when ready; nothing is uploaded.</p>
            </motion.div>
            <motion.div variants={{ hidden: { opacity: 0, scale: 0.96 }, visible: { opacity: 1, scale: 1 } }} className="rounded-2xl border border-acid/20 bg-acid/[.07] px-4 py-3 text-right">
              <p className="text-[11px] font-bold uppercase tracking-[.14em] text-acid">Journey position</p>
              <p className="mt-1 text-sm font-semibold text-white">{journeyPosition.current} of {journeyPosition.total} chapters</p>
            </motion.div>
          </div>
        </motion.div>

        <nav aria-label="Human Mode chapter navigation" className="mt-8 min-w-0">
          <ol className="grid min-w-0 grid-cols-1 gap-2 min-[300px]:grid-cols-2 sm:grid-cols-4">
            {HUMAN_JOURNEY.map((step) => {
              const active = step.id === activeStage;
              const beforeActive = HUMAN_JOURNEY.findIndex((item) => item.id === step.id) < journeyPosition.current - 1;
              return <li key={step.id} className="min-w-0"><button type="button" aria-current={active ? "step" : undefined} onClick={() => activateStage(step.id)} className={cn("group relative min-h-20 w-full min-w-0 overflow-hidden rounded-2xl border px-3 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid min-[360px]:min-h-24 sm:min-h-20", active ? "border-acid/40 bg-acid/[.09]" : "border-white/10 bg-black/15 hover:border-white/25")}>
                <motion.span aria-hidden className="absolute inset-x-0 bottom-0 h-0.5 origin-left bg-acid" initial={false} animate={{ scaleX: beforeActive || active ? 1 : 0 }} transition={{ duration: reducedMotion ? 0 : 0.22, ease: "easeOut" }} />
                <span className="text-[10px] font-bold tracking-[.14em] text-acid">{step.number}</span>
                <span className="mt-1 block break-words text-sm font-semibold text-white">{step.label}</span>
                <span className="mt-1 hidden break-words text-xs leading-4 text-mist min-[360px]:block">{step.detail}</span>
              </button></li>;
            })}
          </ol>
        </nav>
      </section>

      <section className="mx-auto max-w-7xl px-2 sm:px-5 lg:px-8">
        <Card className="overflow-hidden">
          <ModeSwitch mode="human" onBeforeNavigate={flushBufferedFields} />
          <div className="sticky top-16 z-20 flex min-w-0 flex-wrap items-center justify-between gap-3 border-b border-white/10 bg-panel/95 px-4 py-3 backdrop-blur md:hidden" aria-label="Compact stage controls">
            <p className="min-w-0 text-xs font-semibold uppercase tracking-[.12em] text-mist">Step {journeyPosition.current} of {journeyPosition.total}</p>
            <Button className="px-4" onClick={() => activateStage(activeStage === "foundation" ? "shape" : activeStage === "shape" ? "review" : activeStage === "review" ? "release" : "review")}>{activeStage === "shape" ? "Preview" : activeStage === "review" ? "Download" : activeStage === "release" ? "Review" : "Next"} <ArrowRight className="size-4" aria-hidden /></Button>
          </div>
          <BufferedCommitRegistry.Provider value={registerBufferedFlush}>
            <div className="min-w-0 p-3 sm:p-6 lg:p-8">
              <motion.div key={activeStage} initial={reducedMotion ? false : { opacity: 0, y: 12 }} animate={reducedMotion ? undefined : { opacity: 1, y: 0 }} transition={{ duration: reducedMotion ? 0 : 0.22, ease: "easeOut" }}>
                  {activeStage === "foundation" && <JourneyChapter stage="foundation" title="Start with the right document" description="Choose a profile or resume. To continue an existing file, paste its complete Markdown in direct mode.">
                    <div className="grid min-w-0 gap-3 sm:grid-cols-2" aria-label="Document type">
                      {documentOptions.map((option) => {
                        const Icon = option.icon;
                        const selected = kind === option.kind;
                        return <motion.label key={option.kind} htmlFor={`human-document-kind-${option.kind}`} whileHover={reducedMotion ? undefined : { y: -3 }} whileTap={reducedMotion ? undefined : { scale: 0.98 }} className={cn("min-w-0 rounded-2xl border p-4 text-left transition focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-acid", guidedEditsBlocked ? "cursor-not-allowed opacity-50" : "cursor-pointer", selected ? "border-acid/50 bg-acid/[.08]" : "border-white/10 bg-black/15 hover:border-white/25")}>
                          <input id={`human-document-kind-${option.kind}`} type="radio" name="human-document-kind" value={option.kind} checked={selected} disabled={guidedEditsBlocked} onClick={() => { if (selected) selectDocumentKind(option.kind); }} onChange={() => selectDocumentKind(option.kind)} className="peer sr-only" />
                          <Icon className={cn("size-5", selected ? "text-acid" : "text-mist")} aria-hidden />
                          <span className="mt-5 block break-words font-semibold text-white">{option.title}</span>
                          <span className="mt-1 block break-words text-sm text-mist">{option.description}</span>
                        </motion.label>;
                      })}
                    </div>
                    <aside aria-label="Agent draft paste guidance" className="mt-4 min-w-0 rounded-xl border border-acid/20 bg-acid/[.045] p-4">
                      <p className="text-sm leading-6 text-mist">Have an existing or agent-produced draft? Paste the complete file in Markdown Mode, then return here for recognized-field editing. It stays in this browser session until you download it.</p>
                      <Link href="/md" className="mt-3 inline-flex min-h-11 min-w-0 max-w-full items-center justify-center break-words whitespace-normal rounded-full border border-acid/30 px-4 text-center text-sm font-semibold text-acid transition hover:bg-acid/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Open Markdown Mode to paste the draft</Link>
                    </aside>
                    <ChapterNavigation stage="foundation" onNavigate={activateStage} />
                  </JourneyChapter>}

                  {activeStage === "shape" && <JourneyChapter stage="shape" title="Give the document its essential shape" description="Every input patches the one Markdown buffer. Unknown frontmatter and unedited sections stay intact.">
                    {guidedEditsBlocked && <p role="alert" className="flex gap-2 rounded-xl border border-red-400/25 bg-red-400/[.08] p-3 text-sm text-red-100"><ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />Human Mode is locked because the frontmatter cannot be safely parsed. Open MD Mode and repair it before using guided fields. {guidedEditIssue}</p>}
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div>
                        <FieldLabel htmlFor="name">Name</FieldLabel>
                        <BufferedInput id="name" autoComplete="name" value={fields.name} onCommit={(value) => patch({ name: value })} placeholder="Your name" maxLength={SCHEMA_LIMITS.name} disabled={guidedEditsBlocked} />
                      </div>
                      <div>
                        <FieldLabel htmlFor="identifier">{kind === "profile" ? "Public handle" : "Resume slug"}</FieldLabel>
                        <Input id="identifier" autoCapitalize="none" spellCheck={false} maxLength={63} disabled={guidedEditsBlocked || identifierImmutable} value={kind === "profile" ? fields.handle : fields.slug} onChange={(event) => patch(kind === "profile" ? { handle: event.target.value.toLowerCase().replace(/\s+/g, "-") } : { slug: event.target.value.toLowerCase().replace(/\s+/g, "-") })} placeholder={kind === "profile" ? "your-handle" : "your-name-resume"} aria-describedby="identifier-help" />
                        <p id="identifier-help" className="mt-1 text-xs text-mist/75">{identifierImmutable ? `A saved ${kind} identifier is immutable.` : "Lowercase letters, numbers, and hyphens."}</p>
                      </div>
                      {kind === "resume" && <div className="sm:col-span-2">
                        <FieldLabel htmlFor="title">Professional title</FieldLabel>
                        <BufferedInput id="title" value={fields.title} onCommit={(value) => patch({ title: value })} placeholder="Product leader" maxLength={SCHEMA_LIMITS.title} disabled={guidedEditsBlocked} />
                      </div>}
                      <div className={kind === "resume" ? "sm:col-span-2" : ""}>
                        <FieldLabel htmlFor="headline">Headline</FieldLabel>
                        <BufferedInput id="headline" value={fields.headline} onCommit={(value) => patch({ headline: value })} placeholder="What you do, in one clear line" maxLength={SCHEMA_LIMITS.headline} disabled={guidedEditsBlocked} />
                      </div>
                      <div>
                        <FieldLabel htmlFor="location">Location</FieldLabel>
                        <div className="relative"><MapPin className="pointer-events-none absolute left-3 top-3.5 size-4 text-mist" aria-hidden /><BufferedInput id="location" className="pl-9" value={fields.location} onCommit={(value) => patch({ location: value })} placeholder="City, Country" maxLength={SCHEMA_LIMITS.location} disabled={guidedEditsBlocked} /></div>
                      </div>
                      <div>
                        <FieldLabel htmlFor="visibility">Visibility</FieldLabel>
                        <select id="visibility" aria-describedby="visibility-help" disabled={guidedEditsBlocked} value={fields.visibility} onChange={(event) => patch({ visibility: event.target.value as HumanFields["visibility"] })} className="w-full rounded-xl border border-white/12 bg-black/25 px-3.5 py-3 text-sm text-white outline-none transition focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:cursor-not-allowed disabled:opacity-60">
                          <option value="private">Private draft</option>
                          <option value="public">Public-ready metadata</option>
                        </select>
                        <p id="visibility-help" className="mt-1 text-xs text-mist/75">Metadata only. This site never publishes the file.</p>
                      </div>
                      <div className="sm:col-span-2">
                        <FieldLabel htmlFor="skills">Skills</FieldLabel>
                        <BufferedInput id="skills" value={fields.skills.join(", ")} onCommit={(value) => patch({ skills: value.split(",").map((skill) => skill.trim()).filter(Boolean) })} placeholder="Strategy, Product, Design" maxLength={(SCHEMA_LIMITS.skills * SCHEMA_LIMITS.skill) + ((SCHEMA_LIMITS.skills - 1) * 2)} disabled={guidedEditsBlocked} describedBy="skills-help" />
                        <p id="skills-help" className="mt-1 text-xs text-mist/75">Separate skills with commas.</p>
                      </div>
                      {fields.schemaVersion === 2 && <StructuredV2Fields fields={fields} patch={patch} disabled={guidedEditsBlocked} />}
                      <div className="sm:col-span-2">
                        <FieldLabel htmlFor="narrative">{kind === "profile" ? "About" : "Professional summary"}</FieldLabel>
                        <BufferedTextarea id="narrative" disabled={guidedEditsBlocked} value={fields.narrative} onCommit={(narrative) => patch({ narrative })} placeholder={kind === "profile" ? "The work you want people to understand." : "The through-line of your experience."} describedBy="narrative-help" />
                        <p id="narrative-help" className="mt-1 text-xs text-mist/75">Your text commits to canonical Markdown when focus leaves this field or you change chapter.</p>
                      </div>
                      <GuidedEntriesEditor kind="experience" value={fields.experience} disabled={guidedEditsBlocked} onChange={(experience) => patch({ experience })} />
                      <GuidedEntriesEditor kind="education" value={fields.education} disabled={guidedEditsBlocked} onChange={(education) => patch({ education })} />
                    </div>
                    <ChapterNavigation stage="shape" onNavigate={activateStage} />
                  </JourneyChapter>}

                  {activeStage === "review" && <JourneyChapter stage="review" title="Watch the document resolve" description="This rendered response is generated from the same in-memory Markdown buffer—sanitized for preview, with no separate form state.">
                    <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 px-1 pb-3">
                      <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Eye className="size-4 text-acid" aria-hidden /> Live document</h3>
                      <span aria-live="polite" className="inline-flex items-center gap-1.5 text-xs text-mist">{previewUpdating && <motion.span aria-hidden className="size-1.5 rounded-full bg-acid" animate={reducedMotion ? undefined : { opacity: [0.35, 1, 0.35] }} transition={{ duration: 0.8, repeat: Infinity }} />}{previewUpdating ? "Updating" : "Sanitized preview"}</span>
                    </div>
                    <div className="relative max-h-[68vh] overflow-auto rounded-2xl border border-white/10 bg-[#f6f7f3] p-5 text-slate-950 shadow-glow sm:p-7">
                      <AnimatePresence mode="wait" initial={false}>
                        <motion.div key={`${kind}-${previewVersion}`} initial={reducedMotion ? false : { opacity: 0, y: 8 }} animate={reducedMotion ? undefined : { opacity: 1, y: 0 }} exit={reducedMotion ? undefined : { opacity: 0, y: -5 }} transition={{ duration: reducedMotion ? 0 : 0.22, ease: "easeOut" }}>
                          <MarkdownPreview markdown={markdown} className="light-preview" headingOffset={3} />
                        </motion.div>
                      </AnimatePresence>
                    </div>
                    <ChapterNavigation stage="review" onNavigate={activateStage} />
                  </JourneyChapter>}

                  {activeStage === "release" && <JourneyChapter stage="release" title="Download the file you reviewed" description="Client-side validation explains what needs attention. Downloading is the only release action in this standalone site.">
                    <div className={cn("mb-4 flex gap-3 rounded-xl border p-3", readyForDownload ? "border-acid/20 bg-acid/[.06]" : "border-amber-300/20 bg-amber-300/[.06]")}>
                      {readyForDownload ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden /> : <Compass className="mt-0.5 size-5 shrink-0 text-amber-100" aria-hidden />}
                      <div><p className="text-sm font-semibold text-white">{readyForDownload ? "Ready to download" : "A few signals need attention"}</p><p className="mt-1 text-xs leading-5 text-mist">{readyForDownload ? "Preflight is clear. The file still exists only in this browser session." : "Resolve the validation list before downloading the file."}</p></div>
                    </div>
                    <div className="space-y-4">
                      <ValidationPanel issues={issues} />
                      <PublishPanel issues={issues} />
                    </div>
                    <ChapterNavigation stage="release" onNavigate={activateStage} />
                  </JourneyChapter>}
              </motion.div>
            </div>
          </BufferedCommitRegistry.Provider>
        </Card>
      </section>
    </main>
  );
}
