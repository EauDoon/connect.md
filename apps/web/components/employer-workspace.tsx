"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { ApplicationReview } from "@/components/employer-application-review";
import { JobActions } from "@/components/employer-job-actions";
import { OrganizationVerificationSubmissionForm } from "@/components/organization-verification-submission";
import { OrganizationVerificationStatusCard } from "@/components/organization-verification-status";
import { OrganizationManagement } from "@/components/employer-organization-management";
import { ManageableJobInventory, ManageableOrganizationInventory, MembershipInvitationInbox, OrganizationActions, OrganizationMemberInventory } from "@/components/employer-inventory-panels";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { acceptOrganizationMembership, appendCursorPage, authSubjectIsCurrent, changeJobLifecycle, createJob, createOrganization, decideApplication, getEmployerApplicationDetail, hasActiveRecruitingControl, inviteOrganizationMember, listJobApplications, listManageableJobs, listManageableOrganizations, listOrganizationMembers, listOrganizationMembershipInvitations, loadJobForOwner, loadOrganizationForOwner, presentRecruitmentError, removeOrganizationMember, updateJob, updateOrganization, type Application, type Job, type JobInput, type ManageableJobSummary, type ManageableOrganizationSummary, type Organization, type OrganizationInvitation, type OrganizationMembershipInvitation } from "@/lib/recruitment-api";

export type EmployerBusyClaim = { owner: string | null };

export function createEmployerBusyClaim(): EmployerBusyClaim {
  return { owner: null };
}

export function claimEmployerBusy(state: EmployerBusyClaim, owner: string): boolean {
  if (state.owner !== null) return false;
  state.owner = owner;
  return true;
}

export function releaseEmployerBusy(state: EmployerBusyClaim, owner: string): boolean {
  if (state.owner !== owner) return false;
  state.owner = null;
  return true;
}

export async function withApplicationSummaryLoadConsent<T>(cursor: string | null, confirm: () => boolean, dispatch: () => Promise<T>) {
  if (cursor === null && !confirm()) return null;
  return dispatch();
}

export type ApplicationSummaryRefreshState<T> = {
  applications: T[];
  applicationCursor: string | null;
  messages: Record<string, string>;
  deliveredCursors: Set<string>;
};

export async function refreshApplicationSummaryState<T>(
  currentState: ApplicationSummaryRefreshState<T>,
  confirm: () => boolean,
  dispatch: () => Promise<{ items: T[]; nextCursor: string | null } | null>,
  isCurrent: () => boolean,
): Promise<ApplicationSummaryRefreshState<T>> {
  const result = await withApplicationSummaryLoadConsent(null, confirm, dispatch);
  if (result === null || !isCurrent()) return currentState;
  return {
    applications: result.items,
    applicationCursor: result.nextCursor,
    messages: {},
    deliveredCursors: new Set(),
  };
}

export function EmployerWorkspace() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  if (!configured || !isLoaded || !isSignedIn || !subject) return <main className="mx-auto max-w-6xl px-5 py-16 lg:px-8"><h1 className="font-display text-4xl font-semibold text-white">Employer workspace</h1><p role="status" className="mt-4 max-w-xl text-mist">{!configured ? "This deployment has no signed-in employer workspace configured." : !isLoaded ? "Checking your session…" : "Sign in as an authorized human organization member to manage private organization and job records."}</p></main>;
  return <AuthenticatedEmployerWorkspace key={subject} subject={subject} subjectRef={subjectRef} getToken={getToken} />;
}

function AuthenticatedEmployerWorkspace({ subject, subjectRef, getToken }: { subject: string; subjectRef: { readonly current: string | null }; getToken: ReturnType<typeof useConnectmdAuth>["getToken"] }) {
  const [organization, setOrganization] = useState<Organization | null>(null); const [job, setJob] = useState<Job | null>(null); const [applications, setApplications] = useState<Application[]>([]); const [applicationCursor, setApplicationCursor] = useState<string | null>(null); const [messages, setMessages] = useState<Record<string, string>>({}); const [notice, setNotice] = useState<string | null>(null); const [busy, setBusy] = useState<string | null>(null);
  const [manageableOrganizations, setManageableOrganizations] = useState<ManageableOrganizationSummary[]>([]); const [organizationInventoryCursor, setOrganizationInventoryCursor] = useState<string | null>(null); const [organizationsLoaded, setOrganizationsLoaded] = useState(false); const [organizationsLoadFailed, setOrganizationsLoadFailed] = useState(false); const [organizationsMoreLoading, setOrganizationsMoreLoading] = useState(false);
  const [manageableJobs, setManageableJobs] = useState<ManageableJobSummary[]>([]); const [jobInventoryCursor, setJobInventoryCursor] = useState<string | null>(null); const [jobsLoaded, setJobsLoaded] = useState(false); const [jobsLoadFailed, setJobsLoadFailed] = useState(false); const [jobsMoreLoading, setJobsMoreLoading] = useState(false);
  const [verificationStatusRevision, setVerificationStatusRevision] = useState(0);
  const [invitations, setInvitations] = useState<OrganizationMembershipInvitation[]>([]); const [invitationCursor, setInvitationCursor] = useState<string | null>(null);
  const [invitationsLoaded, setInvitationsLoaded] = useState(false); const [invitationsLoadFailed, setInvitationsLoadFailed] = useState(false);
  const [members, setMembers] = useState<OrganizationInvitation[]>([]); const [memberCursor, setMemberCursor] = useState<string | null>(null);
  const applicationsRef = useRef(applications); applicationsRef.current = applications;
  const manageableOrganizationsRef = useRef(manageableOrganizations); manageableOrganizationsRef.current = manageableOrganizations;
  const manageableJobsRef = useRef(manageableJobs); manageableJobsRef.current = manageableJobs;
  const organizationsLoadedRef = useRef(organizationsLoaded); organizationsLoadedRef.current = organizationsLoaded;
  const jobsLoadedRef = useRef(jobsLoaded); jobsLoadedRef.current = jobsLoaded;
  const invitationsRef = useRef(invitations); invitationsRef.current = invitations;
  const invitationsLoadedRef = useRef(invitationsLoaded); invitationsLoadedRef.current = invitationsLoaded;
  const membersRef = useRef(members); membersRef.current = members;
  const deliveredCursorsRef = useRef(new Set<string>());
  const invitationCursorsRef = useRef(new Set<string>());
  const memberCursorsRef = useRef(new Set<string>());
  const organizationInventoryCursorsRef = useRef(new Set<string>());
  const jobInventoryCursorsRef = useRef(new Set<string>());
  const moreInFlightRef = useRef(false);
  const invitationMoreInFlightRef = useRef(false);
  const memberMoreInFlightRef = useRef(false);
  const organizationInventoryMoreInFlightRef = useRef(false);
  const jobInventoryMoreInFlightRef = useRef(false);
  const organizationInventoryInitialInFlightRef = useRef(false);
  const jobInventoryInitialInFlightRef = useRef(false);
  const busyClaimRef = useRef(createEmployerBusyClaim());
  const mutationAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const beginAttempt = (slot: string, requestSubject: string, intent: unknown) => {
    const attempt = beginLogicalMutationAttempt(mutationAttemptsRef.current.get(slot) ?? null, requestSubject, intent);
    mutationAttemptsRef.current.set(slot, attempt);
    return attempt;
  };
  const settleAttempt = (slot: string, attempt: LogicalMutationAttempt, error: unknown) => {
    const next = settleLogicalMutationAttempt(attempt, error);
    if (next) mutationAttemptsRef.current.set(slot, next);
    else mutationAttemptsRef.current.delete(slot);
    return next;
  };
  const report = (error: unknown) => setNotice(presentRecruitmentError(error));
  const beginBusy = useCallback((owner: string) => {
    if (!claimEmployerBusy(busyClaimRef.current, owner)) return false;
    setBusy(owner);
    return true;
  }, []);
  const endBusy = useCallback((owner: string, requestSubject: string) => {
    if (!releaseEmployerBusy(busyClaimRef.current, owner)) return;
    if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setBusy(null);
  }, [subjectRef]);
  const loadManageableOrganizations = useCallback(async (cursor: string | null = null) => {
    if (cursor && (organizationInventoryMoreInFlightRef.current || organizationInventoryCursorsRef.current.has(cursor))) return;
    if (!cursor && organizationInventoryInitialInFlightRef.current) return;
    const requestSubject = subject;
    if (cursor) {
      organizationInventoryMoreInFlightRef.current = true;
      setOrganizationsMoreLoading(true);
    } else {
      organizationInventoryInitialInFlightRef.current = true;
      setOrganizationsLoadFailed(false);
    }
    try {
      const result = await listManageableOrganizations(getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), cursor);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      if (!cursor) {
        organizationInventoryCursorsRef.current = new Set();
        manageableOrganizationsRef.current = result.items;
        setManageableOrganizations(result.items);
        setOrganizationInventoryCursor(result.nextCursor);
        organizationsLoadedRef.current = true;
        setOrganizationsLoaded(true);
        setOrganizationsLoadFailed(false);
        return;
      }
      const delivered = new Set(organizationInventoryCursorsRef.current);
      delivered.add(cursor);
      organizationInventoryCursorsRef.current = delivered;
      const next = appendCursorPage(manageableOrganizationsRef.current, result, cursor, delivered);
      manageableOrganizationsRef.current = next.items;
      setManageableOrganizations(next.items);
      setOrganizationInventoryCursor(next.nextCursor);
      if (next.cursorDidNotProgress) setNotice("The organization inventory returned a cursor that did not advance. Loaded organizations remain available.");
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) {
        if (!organizationsLoadedRef.current) setOrganizationsLoadFailed(true);
        setNotice(presentRecruitmentError(error));
      }
    } finally {
      if (cursor) {
        organizationInventoryMoreInFlightRef.current = false;
        setOrganizationsMoreLoading(false);
      } else organizationInventoryInitialInFlightRef.current = false;
    }
  }, [getToken, manageableOrganizationsRef, subject, subjectRef]);
  const loadManageableJobs = useCallback(async (cursor: string | null = null) => {
    if (cursor && (jobInventoryMoreInFlightRef.current || jobInventoryCursorsRef.current.has(cursor))) return;
    if (!cursor && jobInventoryInitialInFlightRef.current) return;
    const requestSubject = subject;
    if (cursor) {
      jobInventoryMoreInFlightRef.current = true;
      setJobsMoreLoading(true);
    } else {
      jobInventoryInitialInFlightRef.current = true;
      setJobsLoadFailed(false);
    }
    try {
      const result = await listManageableJobs(getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), cursor);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      if (!cursor) {
        jobInventoryCursorsRef.current = new Set();
        manageableJobsRef.current = result.items;
        setManageableJobs(result.items);
        setJobInventoryCursor(result.nextCursor);
        jobsLoadedRef.current = true;
        setJobsLoaded(true);
        setJobsLoadFailed(false);
        return;
      }
      const delivered = new Set(jobInventoryCursorsRef.current);
      delivered.add(cursor);
      jobInventoryCursorsRef.current = delivered;
      const next = appendCursorPage(manageableJobsRef.current, result, cursor, delivered);
      manageableJobsRef.current = next.items;
      setManageableJobs(next.items);
      setJobInventoryCursor(next.nextCursor);
      if (next.cursorDidNotProgress) setNotice("The job inventory returned a cursor that did not advance. Loaded jobs remain available.");
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) {
        if (!jobsLoadedRef.current) setJobsLoadFailed(true);
        setNotice(presentRecruitmentError(error));
      }
    } finally {
      if (cursor) {
        jobInventoryMoreInFlightRef.current = false;
        setJobsMoreLoading(false);
      } else jobInventoryInitialInFlightRef.current = false;
    }
  }, [getToken, manageableJobsRef, subject, subjectRef]);
  useEffect(() => {
    void Promise.all([loadManageableOrganizations(), loadManageableJobs()]);
  }, [loadManageableJobs, loadManageableOrganizations]);
  const loadInvitations = useCallback(async (cursor: string | null = null) => {
    if (cursor && (invitationMoreInFlightRef.current || invitationCursorsRef.current.has(cursor))) return;
    const requestSubject = subject;
    const busySlot = cursor ? "invitations-more" : "invitations";
    if (!beginBusy(busySlot)) return;
    if (cursor) invitationMoreInFlightRef.current = true;
    if (!invitationsLoadedRef.current) setInvitationsLoadFailed(false);
    try {
      const result = await listOrganizationMembershipInvitations(getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), cursor);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      invitationsLoadedRef.current = true;
      setInvitationsLoaded(true);
      setInvitationsLoadFailed(false);
      if (!cursor) {
        invitationCursorsRef.current = new Set();
        invitationsRef.current = result.items;
        setInvitations(result.items);
        setInvitationCursor(result.nextCursor);
        return;
      }
      const delivered = new Set(invitationCursorsRef.current);
      delivered.add(cursor);
      invitationCursorsRef.current = delivered;
      const next = appendCursorPage(invitationsRef.current, result, cursor, delivered);
      invitationsRef.current = next.items;
      setInvitations(next.items);
      setInvitationCursor(next.nextCursor);
      if (next.cursorDidNotProgress) setNotice("The invitation inbox returned a cursor that did not advance. Loaded invitations remain available.");
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) {
        if (!invitationsLoadedRef.current) setInvitationsLoadFailed(true);
        setNotice(presentRecruitmentError(error));
      }
    } finally {
      if (cursor) invitationMoreInFlightRef.current = false;
      endBusy(busySlot, requestSubject);
    }
  }, [beginBusy, endBusy, getToken, subject, subjectRef]);
  useEffect(() => { void loadInvitations(); }, [loadInvitations]);
  const acceptInvitation = async (invitation: OrganizationMembershipInvitation) => {
    if (!window.confirm(`Accept the ${invitation.role} invitation from ${invitation.organizationName}?`)) return;
    const requestSubject = subject;
    const busySlot = invitation.id;
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `membership-accept:${invitation.id}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "accept-membership", invitationId: invitation.id, organizationSlug: invitation.organizationSlug, role: invitation.role });
    try {
      await acceptOrganizationMembership(invitation, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      const next = invitationsRef.current.filter((item) => item.id !== invitation.id);
      invitationsRef.current = next;
      setInvitations(next);
      setNotice(`Membership in ${invitation.organizationName} accepted.`);
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "Membership acceptance may have succeeded but its acknowledgement was not received. Retry the unchanged invitation to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const inspectOrganization = async (slug: string) => {
    if (!subject) return;
    const requestSubject = subject;
    const busySlot = "organization-load";
    if (!beginBusy(busySlot)) return;
    try {
      const next = await loadOrganizationForOwner(slug.trim(), getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject));
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setOrganization(next); membersRef.current = []; setMembers([]); setMemberCursor(null); memberCursorsRef.current = new Set(); setJob(null); setApplications([]); setApplicationCursor(null); setMessages({}); deliveredCursorsRef.current = new Set();
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) report(error);
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const inspectJob = async (slug: string) => {
    if (!organization || !subject) return;
    const requestSubject = subject;
    const busySlot = "job-load";
    if (!beginBusy(busySlot)) return;
    try {
      const next = await loadJobForOwner(organization.slug, slug.trim(), getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject));
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setJob(next); setApplications([]); setApplicationCursor(null); setMessages({}); deliveredCursorsRef.current = new Set();
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) report(error);
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const inspectJobSummary = async (summary: ManageableJobSummary) => {
    if (!subject) return;
    const requestSubject = subject;
    const isSubjectCurrent = () => authSubjectIsCurrent(subjectRef.current, requestSubject);
    const busySlot = "job-load";
    if (!beginBusy(busySlot)) return;
    try {
      const nextOrganization = await loadOrganizationForOwner(summary.organizationSlug, getToken, isSubjectCurrent);
      if (!isSubjectCurrent()) return;
      const nextJob = await loadJobForOwner(summary.organizationSlug, summary.slug, getToken, isSubjectCurrent);
      if (!isSubjectCurrent()) return;
      setOrganization(nextOrganization); membersRef.current = []; setMembers([]); setMemberCursor(null); memberCursorsRef.current = new Set(); setJob(nextJob); setApplications([]); setApplicationCursor(null); setMessages({}); deliveredCursorsRef.current = new Set();
    } catch (error) {
      if (isSubjectCurrent()) report(error);
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const saveOrg = async (input: { name: string; description: string; websiteUrl: string }) => {
    if (!organization || !subject) return;
    const requestSubject = subject;
    const busySlot = "organization-save";
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `organization-save:${organization.slug}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "update-organization", organizationSlug: organization.slug, input });
    try {
      const next = await updateOrganization(organization, input, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setOrganization(next);
      setNotice("Organization details saved.");
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "Organization details may have been saved but the acknowledgement was not received. Retry the unchanged details to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const saveJob = async (input: JobInput) => {
    if (!job || !subject) return;
    const requestSubject = subject;
    const busySlot = "job-save";
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `job-save:${job.id}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "update-job", jobId: job.id, organizationSlug: job.organizationSlug, input });
    try {
      const next = await updateJob(job, input, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setJob(next);
      setNotice("Draft saved.");
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "The job draft may have been saved but the acknowledgement was not received. Retry the unchanged draft to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const lifecycle = async (action: "publish" | "close") => {
    if (!job || !subject || (action === "publish" && (!organization || !hasActiveRecruitingControl(organization)))) return;
    if (!window.confirm(`${action === "publish" ? "Publish" : "Close"} this job as the signed-in human?`)) return;
    const requestSubject = subject;
    const busySlot = action;
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `job-lifecycle:${job.id}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "change-job-lifecycle", jobId: job.id, action });
    try {
      const next = await changeJobLifecycle(job, action, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setJob(next);
      setNotice(`Job ${action === "publish" ? "published" : "closed"}.`);
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? `The job may have been ${action === "publish" ? "published" : "closed"} but the acknowledgement was not received. Retry the unchanged action to recover the same result.` : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const loadApplications = async () => {
    if (!job || !subject || busyClaimRef.current.owner !== null) return;
    const requestSubject = subject;
    const busySlot = "applications";
    let claimed = false;
    const currentState: ApplicationSummaryRefreshState<Application> = {
      applications,
      applicationCursor,
      messages,
      deliveredCursors: deliveredCursorsRef.current,
    };
    try {
      const result = await refreshApplicationSummaryState(currentState, () => window.confirm("Load application summaries solely for this job review?"), async () => {
        if (!beginBusy(busySlot)) return null;
        claimed = true;
        return listJobApplications(job, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject));
      }, () => authSubjectIsCurrent(subjectRef.current, requestSubject));
      if (result === currentState) return;
      setMessages(result.messages);
      deliveredCursorsRef.current = result.deliveredCursors;
      setApplications(result.applications);
      setApplicationCursor(result.applicationCursor);
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) report(error);
    } finally {
      if (claimed) endBusy(busySlot, requestSubject);
    }
  };
  const loadOlderApplications = async () => {
    if (!job || !subject || !applicationCursor || moreInFlightRef.current) return;
    const cursor = applicationCursor;
    if (deliveredCursorsRef.current.has(cursor)) {
      setApplicationCursor(null);
      setNotice("The application queue returned a cursor that did not advance. Loaded records remain available.");
      return;
    }
    const requestSubject = subject;
    const busySlot = "applications-more";
    if (!beginBusy(busySlot)) return;
    moreInFlightRef.current = true;
    try {
      const result = await listJobApplications(job, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), cursor);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      const delivered = new Set(deliveredCursorsRef.current);
      delivered.add(cursor);
      deliveredCursorsRef.current = delivered;
      const next = appendCursorPage(applicationsRef.current, result, cursor, delivered);
      setApplications(next.items);
      setApplicationCursor(next.nextCursor);
      if (next.cursorDidNotProgress) setNotice("The application queue returned a cursor that did not advance. Loaded records remain available.");
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) report(error);
    } finally {
      moreInFlightRef.current = false;
      endBusy(busySlot, requestSubject);
    }
  };
  const viewApplication = async (application: Application) => {
    if (!job || !subject || !window.confirm("Open this note solely to review this job application?")) return;
    const requestSubject = subject;
    const busySlot = application.id;
    if (!beginBusy(busySlot)) return;
    try {
      const detail = await getEmployerApplicationDetail(job, application.id, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject));
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setMessages((current) => ({ ...current, [application.id]: detail.message }));
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) report(error);
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const decide = async (application: Application, action: "review" | "accept" | "reject") => {
    if (!job || !subject || !window.confirm(`${action === "review" ? "Mark under review" : action === "accept" ? "Accept" : "Reject"} this application as the signed-in human?`)) return;
    const requestSubject = subject;
    const busySlot = application.id;
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `application-decision:${application.id}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "decide-application", jobId: job.id, applicationId: application.id, action });
    try {
      const updated = await decideApplication(job, application.id, action, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setApplications((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice(`Application ${action === "review" ? "marked under review" : `${action}ed`}.`);
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "The application decision may have been recorded but the acknowledgement was not received. Retry the unchanged action to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const establishOrganization = async (input: { slug: string; name: string; description: string; websiteUrl: string }) => {
    if (!subject) return;
    const requestSubject = subject;
    const busySlot = "organization-create";
    if (!beginBusy(busySlot)) return;
    const attempt = beginAttempt(busySlot, requestSubject, { operation: "create-organization", input });
    try {
      const next = await createOrganization(input, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(busySlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setOrganization(next); membersRef.current = []; setMembers([]); setMemberCursor(null); memberCursorsRef.current = new Set(); setJob(null); setApplications([]); setApplicationCursor(null); setMessages({}); deliveredCursorsRef.current = new Set();
      setNotice("Private, unverified organization created. It cannot be self-verified or published from this workspace.");
    } catch (error) {
      const retained = settleAttempt(busySlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "The organization may have been created but the acknowledgement was not received. Retry the unchanged details to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const inviteMember = async (memberProfileHandle: string, role: "admin" | "member") => {
    if (!organization || !subject) return;
    const requestSubject = subject;
    const busySlot = "invite";
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `membership-invite:${organization.slug}:${memberProfileHandle}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "invite-organization-member", organizationSlug: organization.slug, memberProfileHandle, role });
    try {
      const invitation = await inviteOrganizationMember(organization.slug, memberProfileHandle, role, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      const next = [invitation, ...membersRef.current.filter((item) => item.id !== invitation.id)];
      membersRef.current = next;
      setMembers(next);
      setNotice(`${invitation.role} invitation for @${invitation.memberProfileHandle ?? memberProfileHandle} recorded as ${invitation.status}; the recipient must accept it.`);
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "The membership invitation may have been recorded but the acknowledgement was not received. Retry the unchanged invitation to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const loadMembers = async (cursor: string | null = null) => {
    if (!organization || !subject || (cursor && (memberMoreInFlightRef.current || memberCursorsRef.current.has(cursor)))) return;
    const requestSubject = subject;
    const busySlot = cursor ? "members-more" : "members";
    if (!beginBusy(busySlot)) return;
    if (cursor) memberMoreInFlightRef.current = true;
    try {
      const result = await listOrganizationMembers(organization.slug, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), cursor);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      if (!cursor) {
        memberCursorsRef.current = new Set();
        membersRef.current = result.items;
        setMembers(result.items);
        setMemberCursor(result.nextCursor);
        return;
      }
      const delivered = new Set(memberCursorsRef.current);
      delivered.add(cursor);
      memberCursorsRef.current = delivered;
      const next = appendCursorPage(membersRef.current, result, cursor, delivered);
      membersRef.current = next.items;
      setMembers(next.items);
      setMemberCursor(next.nextCursor);
      if (next.cursorDidNotProgress) setNotice("The member inventory returned a cursor that did not advance. Loaded memberships remain available.");
    } catch (error) {
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) report(error);
    } finally {
      if (cursor) memberMoreInFlightRef.current = false;
      endBusy(busySlot, requestSubject);
    }
  };
  const removeMember = async (member: OrganizationInvitation) => {
    if (!organization || !subject || !window.confirm(`Remove ${member.memberProfileHandle ? `@${member.memberProfileHandle}` : "this member"} from ${organization.name}?`)) return;
    const requestSubject = subject;
    const busySlot = member.id;
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `membership-remove:${organization.slug}:${member.id}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "remove-organization-member", organizationSlug: organization.slug, membershipId: member.id });
    try {
      await removeOrganizationMember(organization.slug, member.id, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      const next = membersRef.current.filter((item) => item.id !== member.id);
      membersRef.current = next;
      setMembers(next);
      setNotice("Organization membership removed.");
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "The membership may have been removed but the acknowledgement was not received. Retry the unchanged action to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  const establishJob = async (input: JobInput) => {
    if (!organization || !subject) return;
    const requestSubject = subject;
    const busySlot = "job-create";
    if (!beginBusy(busySlot)) return;
    const attemptSlot = `job-create:${organization.slug}`;
    const attempt = beginAttempt(attemptSlot, requestSubject, { operation: "create-job", organizationSlug: organization.slug, input });
    try {
      const next = await createJob(organization.slug, input, getToken, () => authSubjectIsCurrent(subjectRef.current, requestSubject), attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(attemptSlot);
      if (!authSubjectIsCurrent(subjectRef.current, requestSubject)) return;
      setJob(next);
      setNotice("Job saved as a private draft.");
    } catch (error) {
      const retained = settleAttempt(attemptSlot, attempt, error);
      if (authSubjectIsCurrent(subjectRef.current, requestSubject)) setNotice(retained ? "The job draft may have been created but the acknowledgement was not received. Retry the unchanged draft to recover the same result." : presentRecruitmentError(error));
    } finally {
      endBusy(busySlot, requestSubject);
    }
  };
  return <main className="mx-auto max-w-6xl px-5 py-10 pb-16 lg:px-8"><p className="eyebrow">Private organization workspace</p><h1 className="mt-4 font-display text-5xl font-semibold tracking-[-.06em] text-white sm:text-6xl">Organizations and hiring, with human gates.</h1><p className="mt-4 max-w-3xl text-lg leading-8 text-mist">Start with the private inventories for organizations and jobs you manage. Exact-slug controls remain available below when you already know the authorized resource.</p>{notice && <p role="status" className="mt-6 rounded-xl border border-white/10 bg-panel p-4 text-sm text-mist">{notice}</p>}<div className="mt-8 grid gap-6 lg:grid-cols-2"><ManageableOrganizationInventory items={manageableOrganizations} loaded={organizationsLoaded} loadFailed={organizationsLoadFailed} nextCursor={organizationInventoryCursor} moreLoading={organizationsMoreLoading} busy={busy} retry={() => void loadManageableOrganizations()} loadOlder={() => void loadManageableOrganizations(organizationInventoryCursor)} open={(summary) => void inspectOrganization(summary.slug)} /><ManageableJobInventory items={manageableJobs} loaded={jobsLoaded} loadFailed={jobsLoadFailed} nextCursor={jobInventoryCursor} moreLoading={jobsMoreLoading} busy={busy} retry={() => void loadManageableJobs()} loadOlder={() => void loadManageableJobs(jobInventoryCursor)} open={(summary) => void inspectJobSummary(summary)} /></div><MembershipInvitationInbox invitations={invitations} loaded={invitationsLoaded} loadFailed={invitationsLoadFailed} nextCursor={invitationCursor} busy={busy} refresh={() => loadInvitations()} loadOlder={() => loadInvitations(invitationCursor)} accept={acceptInvitation} /><div className="mt-8 grid gap-6 lg:grid-cols-2"><OrganizationActions busy={busy} inspect={inspectOrganization} create={establishOrganization} />{organization && <OrganizationManagement key={organization.id} organization={organization} busy={busy} save={saveOrg} invite={inviteMember} />}{organization && subject && <OrganizationVerificationStatusCard key={`verification-status-${organization.id}-${subject}`} organization={organization} subject={subject} getToken={getToken} isSubjectCurrent={(requestSubject) => authSubjectIsCurrent(subjectRef.current, requestSubject)} refreshRevision={verificationStatusRevision} />}{organization && <OrganizationMemberInventory organization={organization} members={members} nextCursor={memberCursor} busy={busy} refresh={() => loadMembers()} loadOlder={() => loadMembers(memberCursor)} remove={removeMember} />}{organization && subject && <OrganizationVerificationSubmissionForm key={`verification-${organization.id}-${subject}`} organization={organization} subject={subject} getToken={getToken} isSubjectCurrent={(requestSubject) => authSubjectIsCurrent(subjectRef.current, requestSubject)} onSubmitted={() => setVerificationStatusRevision((current) => current + 1)} />}</div>{organization && <section className="mt-6 rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="text-xl font-semibold text-white">Jobs for {organization.name}</h2><p className="mt-1 text-sm text-mist">Draft and edit privately. Publication remains service-gated and requires a signed-in human.</p></div><span className={`rounded-full px-3 py-1 text-xs font-semibold ${hasActiveRecruitingControl(organization) ? "bg-acid/10 text-acid" : "bg-amber-300/10 text-amber-100"}`}>{hasActiveRecruitingControl(organization) ? "recruiting control active" : "recruiting control inactive"}</span></div><div className="mt-6 grid gap-6 lg:grid-cols-2"><JobActions key={employerJobWorkspaceKey(organization.id, job?.id ?? null)} organization={organization} job={job} busy={busy} inspect={inspectJob} create={establishJob} save={saveJob} lifecycle={lifecycle} /><ApplicationReview job={job} applications={applications} applicationCursor={applicationCursor} messages={messages} busy={busy} load={loadApplications} loadOlder={loadOlderApplications} view={viewApplication} decide={decide} /></div></section>}</main>;
}

export function employerJobWorkspaceKey(organizationId: string, jobId: string | null) {
  return `${organizationId}:${jobId ?? "new"}`;
}
