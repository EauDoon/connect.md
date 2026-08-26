import type { AgentDelegation, AgentProposal } from "@/lib/agent-api";

export function mergeProposalFirstPage(existing: AgentProposal[], firstPage: AgentProposal[]) {
  const refreshedIds = new Set(firstPage.map((proposal) => proposal.id));
  return [...firstPage, ...existing.filter((proposal) => !refreshedIds.has(proposal.id))];
}

export function upsertDelegation(existing: AgentDelegation[], next: AgentDelegation) {
  const current = existing.find((grant) => grant.id === next.id);
  const merged = current
    ? { ...next, status: current.status, lastUsedAt: current.lastUsedAt }
    : next;
  return [merged, ...existing.filter((grant) => grant.id !== next.id)];
}

export async function commitProposalBaseMarkdownIfCurrent(response: Promise<string>, isCurrent: () => boolean, commit: (markdown: string) => void) {
  const markdown = await response;
  if (!isCurrent()) return false;
  commit(markdown);
  return true;
}

export type DelegationMutationResource = "grants" | "proposals";
export type DelegationReadResource = DelegationMutationResource | "documents" | "audit";
export type DelegationInventoryResource = "documents" | "audit";

export type DelegationMutationClaim = {
  id: number;
  scope: string;
  resource: DelegationMutationResource;
  generation: number;
};

export type DelegationMutationCoordinator = {
  scope: string;
  nextClaimId: number;
  ownerId: number | null;
  generations: Record<DelegationReadResource, number>;
  resourceReadGeneration: Record<DelegationReadResource, number | null>;
};

function delegationScope(subject: string): string {
  return `${subject.length}:${subject}`;
}

export function createDelegationMutationCoordinator(
  subject: string,
): DelegationMutationCoordinator {
  return {
    scope: delegationScope(subject),
    nextClaimId: 0,
    ownerId: null,
    generations: { grants: 0, documents: 0, audit: 0, proposals: 0 },
    resourceReadGeneration: { grants: null, documents: null, audit: null, proposals: null },
  };
}

export function resetDelegationMutationCoordinator(
  coordinator: DelegationMutationCoordinator,
  subject: string,
): void {
  coordinator.scope = delegationScope(subject);
  coordinator.ownerId = null;
  for (const resource of ["grants", "documents", "audit", "proposals"] as const) {
    coordinator.generations[resource] += 1;
    coordinator.resourceReadGeneration[resource] = null;
  }
}

export function beginDelegationResourceRead(
  coordinator: DelegationMutationCoordinator,
  subject: string,
  resource: DelegationReadResource,
): number | null {
  if (coordinator.scope !== delegationScope(subject)) return null;
  coordinator.generations[resource] += 1;
  const generation = coordinator.generations[resource];
  coordinator.resourceReadGeneration[resource] = generation;
  return generation;
}

export function beginDelegationInventoryRead(
  coordinator: DelegationMutationCoordinator,
  subject: string,
  resource: DelegationInventoryResource,
): number | null {
  if (
    coordinator.scope !== delegationScope(subject) ||
    coordinator.resourceReadGeneration[resource] !== null
  ) {
    return null;
  }
  return beginDelegationResourceRead(coordinator, subject, resource);
}

export function finishDelegationResourceRead(
  coordinator: DelegationMutationCoordinator,
  resource: DelegationReadResource,
  generation: number,
): void {
  if (coordinator.resourceReadGeneration[resource] === generation) {
    coordinator.resourceReadGeneration[resource] = null;
  }
}

export function isCurrentDelegationResource(
  coordinator: DelegationMutationCoordinator,
  subject: string,
  resource: DelegationReadResource,
  generation: number,
): boolean {
  return (
    coordinator.scope === delegationScope(subject) &&
    coordinator.generations[resource] === generation
  );
}

export function claimDelegationMutation(
  coordinator: DelegationMutationCoordinator,
  subject: string,
  resource: DelegationMutationResource,
): DelegationMutationClaim | null {
  if (
    coordinator.scope !== delegationScope(subject) ||
    coordinator.ownerId !== null ||
    coordinator.resourceReadGeneration[resource] !== null
  ) {
    return null;
  }
  const id = coordinator.nextClaimId + 1;
  coordinator.nextClaimId = id;
  coordinator.ownerId = id;
  coordinator.generations[resource] += 1;
  return {
    id,
    scope: coordinator.scope,
    resource,
    generation: coordinator.generations[resource],
  };
}

export function isCurrentDelegationMutation(
  coordinator: DelegationMutationCoordinator,
  claim: DelegationMutationClaim,
): boolean {
  return (
    coordinator.scope === claim.scope &&
    coordinator.generations[claim.resource] === claim.generation
  );
}

export function releaseDelegationMutation(
  coordinator: DelegationMutationCoordinator,
  claim: DelegationMutationClaim,
): boolean {
  if (coordinator.scope !== claim.scope || coordinator.ownerId !== claim.id) {
    return false;
  }
  coordinator.ownerId = null;
  return true;
}
