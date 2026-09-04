import type { Metadata } from "next";
import Link from "next/link";
import {
  Download,
  Eye,
  FileText,
  LockKeyhole,
  RefreshCcw,
  ShieldCheck,
} from "lucide-react";

export const metadata: Metadata = {
  title: "Privacy and data",
  description: "What the standalone connect.md site serves and what stays in your browser.",
  alternates: { canonical: "/trust" },
};

const localData = [
  "Everything you type into the guided builder or Markdown editor.",
  "Any .md file you explicitly open; the browser reads it directly into memory.",
  "The preview generated from your current Markdown buffer.",
  "The filename and file contents created when you choose Download.",
] as const;

const publicData = [
  "The site interface, styles, JavaScript, and self-hosted editor assets.",
  "The public agent drafting runbook and concise llms.txt site map.",
  "No personal profile, resume, account, message, or uploaded source data.",
] as const;

export default function TrustPage() {
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8 lg:py-20">
          <p className="eyebrow">Privacy and data</p>
          <h1 className="mt-4 max-w-5xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">
            Your draft stays in your browser.
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-mist">
            This Vercel deployment is a standalone drafting site. It has no account, publishing API, database, file-upload service, messaging system, or analytics code.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/human" className="inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
              Build a local draft
            </Link>
            <a href="/agent-readme.md" type="text/markdown" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
              Read the agent runbook
            </a>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-5 px-5 py-10 lg:grid-cols-2 lg:px-8">
        <BoundaryCard icon={LockKeyhole} title="Held in this browser session" items={localData} />
        <BoundaryCard icon={Eye} title="Served publicly by Vercel" items={publicData} />
      </section>

      <section className="mx-auto grid max-w-7xl gap-5 px-5 py-4 lg:grid-cols-3 lg:px-8">
        <article className="rounded-[1.6rem] border border-white/10 bg-panel p-6">
          <ShieldCheck className="size-6 text-acid" aria-hidden />
          <h2 className="mt-5 text-2xl font-semibold text-white">No hidden persistence</h2>
          <p className="mt-3 text-sm leading-7 text-mist">
            The draft provider uses in-memory React state. Opening a local .md file reads it directly in the browser. The site does not write your content to localStorage, sessionStorage, IndexedDB, cookies, a server action, or an API route.
          </p>
        </article>

        <article className="rounded-[1.6rem] border border-white/10 bg-panel p-6">
          <Download className="size-6 text-acid" aria-hidden />
          <h2 className="mt-5 text-2xl font-semibold text-white">Download is local</h2>
          <p className="mt-3 text-sm leading-7 text-mist">
            When you choose Download, the browser creates a temporary Markdown blob, starts a local file download, and immediately revokes the temporary object URL.
          </p>
        </article>

        <article className="rounded-[1.6rem] border border-white/10 bg-panel p-6">
          <RefreshCcw className="size-6 text-acid" aria-hidden />
          <h2 className="mt-5 text-2xl font-semibold text-white">Save before leaving</h2>
          <p className="mt-3 text-sm leading-7 text-mist">
            Moving between the guided and Markdown views keeps the current buffer. A full reload, browser crash, or closed tab can erase it, so download the file before leaving.
          </p>
        </article>
      </section>

      <section className="mx-auto max-w-7xl px-5 py-10 lg:px-8">
        <div className="grid gap-6 rounded-[1.7rem] border border-acid/20 bg-acid/[.045] p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div>
            <FileText className="size-6 text-acid" aria-hidden />
            <h2 className="mt-4 text-2xl font-semibold text-white">The file remains yours</h2>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-mist">
              A downloaded <code className="font-mono text-white">.md</code> file is ordinary UTF-8 text. The site does not track where you store it or what you choose to do with it afterward.
            </p>
          </div>
          <Link href="/md" className="inline-flex min-h-11 items-center justify-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
            Open Markdown editor
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 lg:px-8">
        <div className="rounded-[1.6rem] border border-white/10 bg-black/20 p-6">
          <FileText className="size-6 text-acid" aria-hidden />
          <h2 className="mt-4 text-2xl font-semibold text-white">Inspect the public instructions</h2>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-mist">These static files describe the exact standalone workflow and grant no authority over any person or file.</p>
          <nav aria-label="Public site instructions" className="mt-5 flex flex-wrap gap-x-6 gap-y-3 text-sm font-semibold">
            <a href="/agent-readme.md" type="text/markdown" className="inline-flex min-h-11 items-center text-acid underline-offset-4 hover:underline">Agent drafting runbook</a>
            <a href="/llms.txt" type="text/plain" className="inline-flex min-h-11 items-center text-acid underline-offset-4 hover:underline">llms.txt</a>
          </nav>
        </div>
      </section>
    </main>
  );
}
function BoundaryCard({ icon: Icon, title, items }: { icon: typeof Eye; title: string; items: readonly string[] }) {
  return (
    <article className="rounded-[1.7rem] border border-white/10 bg-panel p-6 sm:p-7">
      <Icon className="size-6 text-acid" aria-hidden />
      <h2 className="mt-5 text-2xl font-semibold text-white">{title}</h2>
      <ul className="mt-5 space-y-3">
        {items.map((item) => <li key={item} className="flex gap-3 text-sm leading-6 text-mist"><span className="mt-2 size-1.5 shrink-0 rounded-full bg-acid" aria-hidden />{item}</li>)}
      </ul>
    </article>
  );
}
