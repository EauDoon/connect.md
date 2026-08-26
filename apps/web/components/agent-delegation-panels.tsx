"use client";

import { Check, Copy, Eye, LoaderCircle, Octagon, PauseCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { continuousAgentHandoff } from "@/lib/agent-contract-guides";
import type { AgentDelegation, AgentProposal } from "@/lib/agent-api";

type LoadState = "loading" | "loaded" | "error";

export function PrivateLoadFailure({
  label,
  error,
  onRetry,
}: {
  label: string;
  error: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="mt-4 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"
    >
      <p className="font-semibold text-amber-50">{label}</p>
      <p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p>
      <Button variant="secondary" className="mt-3" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

export function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function Status({ status }: { status: AgentDelegation["status"] }) {
  return (
    <span
      className={
        "rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide " +
        (status === "active"
          ? "bg-acid/10 text-acid"
          : "bg-white/[.07] text-mist")
      }
    >
      {status}
    </span>
  );
}

function CopyGrantHandoff({ grant }: { grant: AgentDelegation }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(continuousAgentHandoff(grant));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Button
      variant="secondary"
      className="min-h-11 px-3"
      onClick={() => void copy()}
    >
      {copied ? (
        <Check className="size-4" aria-hidden />
      ) : (
        <Copy className="size-4" aria-hidden />
      )}
      {copied ? "Copied handoff" : "Copy handoff"}
    </Button>
  );
}

export function AgentGrantInventoryPanel({
  delegations,
  active,
  busy,
  loadState,
  loadError,
  onEmergencyStop,
  onRevoke,
  onRetry,
}: {
  delegations: AgentDelegation[];
  active: AgentDelegation[];
  busy: string | null;
  loadState: LoadState;
  loadError: string;
  onEmergencyStop: () => void;
  onRevoke: (id: string) => void;
  onRetry: () => void;
}) {
  return (
    <section
      aria-labelledby="active-agents-title"
      className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="active-agents-title" className="text-lg font-semibold text-white">
            Agent grants
          </h2>
          <p className="mt-1 text-sm text-mist">
            {active.length} active · {delegations.length} total
          </p>
        </div>
        <Button
          variant="danger"
          disabled={busy !== null || active.length === 0}
          onClick={onEmergencyStop}
        >
          {busy === "emergency" ? (
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
          ) : (
            <Octagon className="size-4" aria-hidden />
          )}
          Emergency revoke all
        </Button>
      </div>
      {loadState === "loading" && delegations.length === 0 && (
        <p
          role="status"
          className="mt-5 inline-flex items-center gap-2 text-sm text-mist"
        >
          <LoaderCircle className="size-4 animate-spin" aria-hidden />
          Loading grants
        </p>
      )}
      {loadState === "error" && delegations.length === 0 && (
        <PrivateLoadFailure
          label="Agent grants could not be loaded"
          error={loadError}
          onRetry={onRetry}
        />
      )}
      {loadState === "loaded" && delegations.length === 0 && (
        <p className="mt-5 rounded-xl border border-dashed border-white/15 p-5 text-sm text-mist">
          No agent grants have been created.
        </p>
      )}
      <ul className="mt-5 space-y-3">
        {delegations.map((grant) => (
          <li
            key={grant.id}
            className="rounded-2xl border border-white/10 bg-black/15 p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold text-white">{grant.name}</h3>
                  <Status status={grant.status} />
                </div>
                <p className="mt-2 text-xs leading-5 text-mist">
                  {grant.mode === "proposal"
                    ? "Proposal only"
                    : "Direct full-document updates"}{" "}
                  ·{" "}
                  {grant.resourceType === "document"
                    ? "Document " + (grant.resourceId ?? "unknown")
                    : "All owned documents"}
                </p>
                <p className="mt-1 text-xs text-mist/75">
                  Expires {formatTime(grant.expiresAt)} · last used{" "}
                  {grant.lastUsedAt ? formatTime(grant.lastUsedAt) : "never"}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <CopyGrantHandoff grant={grant} />
                {grant.status === "active" && (
                  <Button
                    variant="danger"
                    className="min-h-11 px-3"
                    disabled={busy !== null}
                    onClick={() => onRevoke(grant.id)}
                  >
                    {busy === grant.id ? (
                      <LoaderCircle className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <PauseCircle className="size-4" aria-hidden />
                    )}
                    Revoke
                  </Button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {loadState === "error" && delegations.length > 0 && (
        <PrivateLoadFailure
          label="Agent grants could not be refreshed"
          error={loadError}
          onRetry={onRetry}
        />
      )}
    </section>
  );
}

export function AgentProposalReviewPanel({
  proposals,
  proposalBases,
  proposalCursor,
  busy,
  loading,
  loadState,
  loadError,
  onCompare,
  onDecide,
  onRetry,
  onLoadOlder,
}: {
  proposals: AgentProposal[];
  proposalBases: Record<string, string>;
  proposalCursor: string | null;
  busy: string | null;
  loading: boolean;
  loadState: LoadState;
  loadError: string;
  onCompare: (proposal: AgentProposal) => void;
  onDecide: (proposal: AgentProposal, action: "accepted" | "rejected") => void;
  onRetry: () => void;
  onLoadOlder: () => void;
}) {
  return (
    <section
      aria-labelledby="proposal-review-title"
      className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"
    >
      <div>
        <h2
          id="proposal-review-title"
          className="inline-flex items-center gap-2 text-lg font-semibold text-white"
        >
          <Eye className="size-5 text-acid" aria-hidden /> Proposal review
        </h2>
        <p className="mt-1 text-sm leading-6 text-mist">
          Review the exact candidate Markdown. Accepting rechecks its base
          version; stale proposals remain unpublished.
        </p>
      </div>
      {loadState === "loading" && proposals.length === 0 && (
        <p
          role="status"
          className="mt-5 inline-flex items-center gap-2 text-sm text-mist"
        >
          <LoaderCircle className="size-4 animate-spin" aria-hidden />
          Loading proposals
        </p>
      )}
      {loadState === "error" && proposals.length === 0 && (
        <PrivateLoadFailure
          label="Agent proposals could not be loaded"
          error={loadError}
          onRetry={onRetry}
        />
      )}
      {loadState === "loaded" && proposals.length === 0 && (
        <p className="mt-5 rounded-xl border border-dashed border-white/15 p-5 text-sm text-mist">
          No agent proposals are awaiting review.
        </p>
      )}
      <ol className="mt-5 space-y-4">
        {proposals.map((proposal) => (
          <li key={proposal.id}>
            <ProposalCard
              proposal={proposal}
              baseMarkdown={proposalBases[proposal.id] ?? null}
              busy={busy}
              resourceLoading={loadState === "loading"}
              onCompare={() => onCompare(proposal)}
              onDecide={(action) => onDecide(proposal, action)}
            />
          </li>
        ))}
      </ol>
      {loadState === "error" && proposals.length > 0 && (
        <PrivateLoadFailure
          label="Agent proposals could not be refreshed"
          error={loadError}
          onRetry={onRetry}
        />
      )}
      {proposalCursor && (
        <Button
          variant="secondary"
          className="mt-5"
          disabled={loading || busy !== null}
          onClick={onLoadOlder}
        >
          {busy === "proposals:more" && (
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
          )}
          Load older proposals
        </Button>
      )}
    </section>
  );
}

function ProposalCard({
  proposal,
  baseMarkdown,
  busy,
  resourceLoading,
  onCompare,
  onDecide,
}: {
  proposal: AgentProposal;
  baseMarkdown: string | null;
  busy: string | null;
  resourceLoading: boolean;
  onCompare: () => void;
  onDecide: (action: "accepted" | "rejected") => void;
}) {
  const decisionBusy = busy === "proposal:" + proposal.id;
  const comparisonBusy = busy === "compare:" + proposal.id;
  return (
    <article className="rounded-2xl border border-white/10 bg-black/15 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-white">
            {proposal.kind} · {proposal.identifier}
          </h3>
          <p className="mt-1 text-xs leading-5 text-mist">
            Submitted by {proposal.submitterActorId} ·{" "}
            <time dateTime={proposal.createdAt}>
              {formatTime(proposal.createdAt)}
            </time>
          </p>
        </div>
        <span
          className={
            "rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide " +
            (proposal.status === "pending"
              ? "bg-acid/10 text-acid"
              : "bg-white/[.07] text-mist")
          }
        >
          {proposal.status}
        </span>
      </div>
      <p className="mt-3 text-xs leading-5 text-mist/75">
        Base validator: <code className="break-all">{proposal.ifMatch}</code>
      </p>
      <details className="mt-4 rounded-xl border border-white/10 bg-black/20 p-3">
        <summary className="min-h-11 cursor-pointer text-sm font-semibold text-white">
          Candidate Markdown
        </summary>
        <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/30 p-3 text-xs leading-5 text-[#d5d9e0]">
          <code>{proposal.markdown}</code>
        </pre>
      </details>
      {baseMarkdown ? (
        <details className="mt-3 rounded-xl border border-white/10 bg-black/20 p-3">
          <summary className="min-h-11 cursor-pointer text-sm font-semibold text-white">
            Line comparison with current canonical Markdown
          </summary>
          <MarkdownDiff
            baseMarkdown={baseMarkdown}
            candidateMarkdown={proposal.markdown}
          />
        </details>
      ) : (
        <Button
          variant="secondary"
          className="mt-4 min-h-11 px-3"
          disabled={busy !== null}
          onClick={onCompare}
        >
          {comparisonBusy && (
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
          )}
          Compare with current canonical Markdown
        </Button>
      )}
      {proposal.status === "pending" && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            disabled={busy !== null || resourceLoading}
            onClick={() => onDecide("accepted")}
          >
            {decisionBusy && (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            )}
            Accept and publish
          </Button>
          <Button
            variant="danger"
            disabled={busy !== null || resourceLoading}
            onClick={() => onDecide("rejected")}
          >
            Reject
          </Button>
        </div>
      )}
    </article>
  );
}

function MarkdownDiff({
  baseMarkdown,
  candidateMarkdown,
}: {
  baseMarkdown: string;
  candidateMarkdown: string;
}) {
  const lines = lineDiff(baseMarkdown, candidateMarkdown);
  if (lines === null) {
    return (
      <p className="mt-3 text-xs leading-5 text-mist">
        The documents are too large for an in-browser line diff. Review the
        complete candidate Markdown above before deciding.
      </p>
    );
  }
  return (
    <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/30 p-3 text-xs leading-5">
      {lines.map((line, index) => (
        <span
          key={line.type + "-" + index + "-" + line.value}
          className={
            line.type === "added"
              ? "block bg-emerald-300/10 text-emerald-100"
              : line.type === "removed"
                ? "block bg-red-300/10 text-red-100"
                : "block text-mist/75"
          }
        >
          {line.type === "added" ? "+ " : line.type === "removed" ? "- " : "  "}
          {line.value}{"\n"}
        </span>
      ))}
    </pre>
  );
}

function lineDiff(
  baseMarkdown: string,
  candidateMarkdown: string,
): Array<{ type: "same" | "added" | "removed"; value: string }> | null {
  const base = baseMarkdown.split("\n");
  const candidate = candidateMarkdown.split("\n");
  if (base.length > 400 || candidate.length > 400) return null;
  const table = Array.from(
    { length: base.length + 1 },
    () => new Uint16Array(candidate.length + 1),
  );
  for (let baseIndex = base.length - 1; baseIndex >= 0; baseIndex -= 1) {
    for (
      let candidateIndex = candidate.length - 1;
      candidateIndex >= 0;
      candidateIndex -= 1
    ) {
      table[baseIndex][candidateIndex] =
        base[baseIndex] === candidate[candidateIndex]
          ? table[baseIndex + 1][candidateIndex + 1] + 1
          : Math.max(
              table[baseIndex + 1][candidateIndex],
              table[baseIndex][candidateIndex + 1],
            );
    }
  }
  const result: Array<{
    type: "same" | "added" | "removed";
    value: string;
  }> = [];
  let baseIndex = 0;
  let candidateIndex = 0;
  while (baseIndex < base.length && candidateIndex < candidate.length) {
    if (base[baseIndex] === candidate[candidateIndex]) {
      result.push({ type: "same", value: base[baseIndex] });
      baseIndex += 1;
      candidateIndex += 1;
    } else if (
      table[baseIndex + 1][candidateIndex] >=
      table[baseIndex][candidateIndex + 1]
    ) {
      result.push({ type: "removed", value: base[baseIndex] });
      baseIndex += 1;
    } else {
      result.push({ type: "added", value: candidate[candidateIndex] });
      candidateIndex += 1;
    }
  }
  while (baseIndex < base.length) {
    result.push({ type: "removed", value: base[baseIndex] });
    baseIndex += 1;
  }
  while (candidateIndex < candidate.length) {
    result.push({ type: "added", value: candidate[candidateIndex] });
    candidateIndex += 1;
  }
  return result;
}
