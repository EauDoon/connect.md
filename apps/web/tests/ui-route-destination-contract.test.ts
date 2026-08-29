import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("UI route destination source contracts", () => {
  it("proves /agents composes the three bounded agent management surfaces", () => {
    const agentsPageSource = readFileSync(new URL("../app/agents/page.tsx", import.meta.url), "utf8");

    expect(agentsPageSource).toContain("robots: { index: false, follow: false }");
    expect(agentsPageSource).toContain('import { AgentDelegationManager } from "@/components/agent-delegation-manager";');
    expect(agentsPageSource).toContain('import { AgentIdentityManager } from "@/components/agent-identity-manager";');
    expect(agentsPageSource).toContain('import { AgentIntegrationPanel } from "@/components/agent-integration-panel";');
    expect(agentsPageSource).toContain("<AgentIdentityManager />");
    expect(agentsPageSource).toContain("<AgentIntegrationPanel />");
    expect(agentsPageSource).toContain("<AgentDelegationManager />");
  });

  it("proves /md renders Markdown Mode with its current metadata", () => {
    const markdownModePageSource = readFileSync(new URL("../app/md/page.tsx", import.meta.url), "utf8");

    expect(markdownModePageSource).toContain('import { MarkdownEditor } from "@/components/markdown-editor";');
    expect(markdownModePageSource).toContain('export const metadata = { title: "MD Mode" };');
    expect(markdownModePageSource).toContain("return <MarkdownEditor />;");
  });

  it("proves /representatives owns canonical dynamic success and error rendering", () => {
    const representativesPageSource = readFileSync(new URL("../app/representatives/page.tsx", import.meta.url), "utf8");

    expect(representativesPageSource).toContain('alternates: { canonical: "/representatives" }');
    expect(representativesPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(representativesPageSource).toContain("representativeFiltersFromParams");
    expect(representativesPageSource).toContain("searchDirectory(filters)");
    expect(representativesPageSource).toContain("response={await searchDirectory(filters)} error={null}");
    expect(representativesPageSource).toContain("response={null} error={presentPublicReadError(error)}");
  });

  it("proves /agents/{handle} binds cached identity, canonical contact, and notFound rendering", () => {
    const publicAgentIdentityPageSource = readFileSync(new URL("../app/agents/[handle]/page.tsx", import.meta.url), "utf8");

    expect(publicAgentIdentityPageSource).toContain("fetchPublicAgentIdentity");
    expect(publicAgentIdentityPageSource).toContain("const getAgentIdentity = cache(fetchPublicAgentIdentity);");
    expect(publicAgentIdentityPageSource).toContain("const path = `/agents/${encodeURIComponent(identity.handle)}`;");
    expect(publicAgentIdentityPageSource).toContain("notFound();");
    expect(publicAgentIdentityPageSource).toContain("buildInboxContactReturnPath(identity.profileHandle)");
    expect(publicAgentIdentityPageSource).toContain("agentIdentityJsonLd(identity)");
    expect(publicAgentIdentityPageSource).toContain("Public Agent Identity");
  });

  it("proves /posts/{id} binds cached post metadata, Markdown, notFound, and rendering", () => {
    const publicPostPageSource = readFileSync(new URL("../app/posts/[id]/page.tsx", import.meta.url), "utf8");

    expect(publicPostPageSource).toContain("fetchPublicPost");
    expect(publicPostPageSource).toContain("const getPost = cache(fetchPublicPost);");
    expect(publicPostPageSource).toContain("const path = `/posts/${encodeURIComponent(post.id)}`;");
    expect(publicPostPageSource).toContain("publicMarkdownAlternate(post.markdownUrl)");
    expect(publicPostPageSource).toContain("notFound();");
    expect(publicPostPageSource).toContain("<PublicPostPage post={post} />");
  });

  it("proves / preserves the standalone static agent handoff", () => {
    const homePageSource = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

    expect(homePageSource).toContain('import { absoluteSiteUrl } from "@/lib/public-document";');
    expect(homePageSource).toContain('<AgentHandoff agentReadmeUrl={absoluteSiteUrl("/agent-readme.md")} />');
    expect(homePageSource).not.toMatch(/force-dynamic|publicDiscoveryUrl|NEXT_PUBLIC_API_BASE_URL/u);
  });

  it("proves /human preserves Human Mode metadata and builder composition", () => {
    const humanModePageSource = readFileSync(new URL("../app/human/page.tsx", import.meta.url), "utf8");

    expect(humanModePageSource).toContain('export const metadata = { title: "Human Mode" };');
    expect(humanModePageSource).toContain('import { HumanBuilder } from "@/components/human-builder";');
    expect(humanModePageSource).toContain("return <HumanBuilder />;");
  });

  it("proves /p/{handle} fetches a cached profile and preserves notFound rendering", () => {
    const publicProfilePageSource = readFileSync(new URL("../app/p/[handle]/page.tsx", import.meta.url), "utf8");

    expect(publicProfilePageSource).toContain("fetchPublicProfile");
    expect(publicProfilePageSource).toContain("const getProfile = cache(fetchPublicProfile);");
    expect(publicProfilePageSource).toContain("notFound();");
    expect(publicProfilePageSource).toContain("<PublicDocumentPage document={document} agentIdentities={identities?.identities ?? []} agentIdentitiesUnavailable={identities === null} privateWorkspacesEnabled={privateWorkspaceConfiguredFromEnvironment()} />");
  });

  it("proves /r/{slug} fetches a cached resume and preserves notFound rendering", () => {
    const publicResumePageSource = readFileSync(new URL("../app/r/[slug]/page.tsx", import.meta.url), "utf8");

    expect(publicResumePageSource).toContain("fetchPublicResume");
    expect(publicResumePageSource).toContain("const getResume = cache(fetchPublicResume);");
    expect(publicResumePageSource).toContain("publicDocumentMetadata(document, `/r/${encodeURIComponent(document.identifier)}`)");
    expect(publicResumePageSource).toContain("notFound();");
  });

  it("proves /agent-directory owns filtered success and error rendering", () => {
    const agentDirectoryPageSource = readFileSync(new URL("../app/agent-directory/page.tsx", import.meta.url), "utf8");

    expect(agentDirectoryPageSource).toContain('alternates: { canonical: "/agent-directory" }');
    expect(agentDirectoryPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(agentDirectoryPageSource).toContain("listPublicAgentDirectory(filters)");
    expect(agentDirectoryPageSource).toContain("<AgentDirectory filters={filters} response={null} error={presentPublicReadError(error)} />");
  });

  it("proves /inbox keeps private metadata, intent parsing, and outreach composition", () => {
    const inboxPageSource = readFileSync(new URL("../app/inbox/page.tsx", import.meta.url), "utf8");

    expect(inboxPageSource).toContain('robots: { index: false, follow: false }');
    expect(inboxPageSource).toContain("parseInboxContactProfileIntent");
    expect(inboxPageSource).toContain("<OutreachInbox prefillProfileHandle={prefillProfileHandle} />");
    expect(inboxPageSource).toContain('href="/network"');
  });

  it("proves /feed is private and renders the professional feed", () => {
    const feedPageSource = readFileSync(new URL("../app/feed/page.tsx", import.meta.url), "utf8");

    expect(feedPageSource).toContain('title: "Private feed"');
    expect(feedPageSource).toContain('robots: { index: false, follow: false }');
    expect(feedPageSource).toContain('import { ProfessionalFeed } from "@/components/professional-feed";');
    expect(feedPageSource).toContain("return <ProfessionalFeed />;");
  });

  it("proves /moderation is private and renders case status", () => {
    const moderationPageSource = readFileSync(new URL("../app/moderation/page.tsx", import.meta.url), "utf8");

    expect(moderationPageSource).toContain('title: "Private post case status"');
    expect(moderationPageSource).toContain('robots: { index: false, follow: false }');
    expect(moderationPageSource).toContain('import { ModerationCaseManager } from "@/components/moderation-case-manager";');
    expect(moderationPageSource).toContain("return <ModerationCaseManager />;");
  });

  it("proves /discover gates recruiting sources and renders discovery", () => {
    const discoverPageSource = readFileSync(new URL("../app/discover/page.tsx", import.meta.url), "utf8");

    expect(discoverPageSource).toContain('alternates: { canonical: "/discover" }');
    expect(discoverPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(discoverPageSource).toContain("const recruitingEnabled = recruitingReleaseEnabled();");
    expect(discoverPageSource).toContain("<DiscoverHub");
    expect(discoverPageSource).toContain("searchDirectory(emptySearchFilters)");
  });

  it("proves /search maps filters to bounded success and error states", () => {
    const searchPageSource = readFileSync(new URL("../app/search/page.tsx", import.meta.url), "utf8");

    expect(searchPageSource).toContain('alternates: { canonical: "/search" }');
    expect(searchPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(searchPageSource).toContain("const filters = searchFiltersFromParams(serverSearchParams(await searchParams));");
    expect(searchPageSource).toContain("<SearchExperience filters={filters} response={await searchDirectory(filters)} error={null} />");
    expect(searchPageSource).toContain("<SearchExperience filters={filters} response={null} error={presentPublicReadError(error)} />");
  });

  it("proves /trust exposes the standalone browser privacy boundary", () => {
    const trustPageSource = readFileSync(new URL("../app/trust/page.tsx", import.meta.url), "utf8");

    expect(trustPageSource).toContain('title: "Privacy and data"');
    expect(trustPageSource).toContain('alternates: { canonical: "/trust" }');
    expect(trustPageSource).toContain("const localData =");
    expect(trustPageSource).toContain("const publicData =");
    expect(trustPageSource).not.toMatch(/recruitingReleaseEnabled|privateWorkspaceConfiguredFromEnvironment|NEXT_PUBLIC_API_BASE_URL/u);
  });

  it("proves /applications is private and renders candidate-owned applications", () => {
    const applicationsPageSource = readFileSync(new URL("../app/applications/page.tsx", import.meta.url), "utf8");

    expect(applicationsPageSource).toContain('title: "My applications"');
    expect(applicationsPageSource).toContain('robots: { index: false, follow: false }');
    expect(applicationsPageSource).toContain('import { CandidateApplications } from "@/components/candidate-applications";');
    expect(applicationsPageSource).toContain("return <CandidateApplications />;");
  });

  it("proves /employer is private and renders the employer workspace", () => {
    const employerPageSource = readFileSync(new URL("../app/employer/page.tsx", import.meta.url), "utf8");

    expect(employerPageSource).toContain('title: "Employer workspace"');
    expect(employerPageSource).toContain('robots: { index: false, follow: false }');
    expect(employerPageSource).toContain('import { EmployerWorkspace } from "@/components/employer-workspace";');
    expect(employerPageSource).toContain("return <EmployerWorkspace />;");
  });

  it("proves /jobs is release-gated with bounded directory error handling", () => {
    const jobsPageSource = readFileSync(new URL("../app/jobs/page.tsx", import.meta.url), "utf8");

    expect(jobsPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(jobsPageSource).toContain("if (!recruitingReleaseEnabled()) notFound();");
    expect(jobsPageSource).toContain("listPublicJobs(filters)");
    expect(jobsPageSource).toContain("<JobDirectory filters={filters} response={null} error={presentPublicReadError(error)} />");
  });

  it("proves /jobs/{organizationSlug}/{jobSlug} gates public job and application rendering", () => {
    const publicJobPageSource = readFileSync(new URL("../app/jobs/[organizationSlug]/[jobSlug]/page.tsx", import.meta.url), "utf8");

    expect(publicJobPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(publicJobPageSource).toContain("fetchPublicJob(values.organizationSlug, values.jobSlug)");
    expect(publicJobPageSource).toContain("if (!hasActiveRecruitingControl(organization)) notFound();");
    expect(publicJobPageSource).toContain("<JobPublicPage job={job} applicationPanel={<JobApplicationPanel job={job} />} />");
  });

  it("proves /organizations is release-gated with bounded directory states", () => {
    const organizationsPageSource = readFileSync(new URL("../app/organizations/page.tsx", import.meta.url), "utf8");

    expect(organizationsPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(organizationsPageSource).toContain("if (!recruitingReleaseEnabled()) notFound();");
    expect(organizationsPageSource).toContain("listPublicOrganizations(query, cursor)");
    expect(organizationsPageSource).toContain("<OrganizationDirectory query={query} cursor={cursor} response={null} error={presentPublicReadError(error)} />");
  });

  it("proves /organizations/{slug} gates verified organization and jobs", () => {
    const publicOrganizationPageSource = readFileSync(new URL("../app/organizations/[slug]/page.tsx", import.meta.url), "utf8");

    expect(publicOrganizationPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(publicOrganizationPageSource).toContain("fetchPublicOrganization(slug)");
    expect(publicOrganizationPageSource).toContain("if (!hasActiveRecruitingControl(organization)) notFound();");
    expect(publicOrganizationPageSource).toContain("<OrganizationPublicPage organization={organization} jobs={jobs.items} />");
  });

  it("proves /messages/{conversationId} is private and binds the conversation", () => {
    const messagesPageSource = readFileSync(new URL("../app/messages/[conversationId]/page.tsx", import.meta.url), "utf8");

    expect(messagesPageSource).toContain('title: "Private conversation"');
    expect(messagesPageSource).toContain('robots: { index: false, follow: false }');
    expect(messagesPageSource).toContain('import { ConversationThread } from "@/components/conversation-thread";');
    expect(messagesPageSource).toContain("conversationId={(await params).conversationId}");
  });

  it("proves /network is private and renders the bounded network hub", () => {
    const networkPageSource = readFileSync(new URL("../app/network/page.tsx", import.meta.url), "utf8");

    expect(networkPageSource).toContain('title: "Private network"');
    expect(networkPageSource).toContain('robots: { index: false, follow: false }');
    expect(networkPageSource).toContain('import { NetworkHub } from "@/components/network-hub";');
    expect(networkPageSource).toContain("return <NetworkHub />;");
  });

  it("proves /workspace is private and renders navigation without private records", () => {
    const workspacePageSource = readFileSync(new URL("../app/workspace/page.tsx", import.meta.url), "utf8");

    expect(workspacePageSource).toContain('title: "Your workspace"');
    expect(workspacePageSource).toContain('robots: { index: false, follow: false }');
    expect(workspacePageSource).toContain('description: "Private navigation for your connect.md documents, network, work, and safety controls."');
    expect(workspacePageSource).toContain("return <WorkspaceHub />;");
  });

  it("proves /account is feature-gated and private", () => {
    const accountPageSource = readFileSync(new URL("../app/account/page.tsx", import.meta.url), "utf8");

    expect(accountPageSource).toContain('title: "Account privacy"');
    expect(accountPageSource).toContain('robots: { index: false, follow: false }');
    expect(accountPageSource).toContain("if (!accountLifecycleFeatureEnabled()) notFound();");
    expect(accountPageSource).toContain("return <AccountPrivacyCenter />;");
  });

  it("proves /appeal-review is private and renders independent appeal review", () => {
    const appealReviewPageSource = readFileSync(new URL("../app/appeal-review/page.tsx", import.meta.url), "utf8");

    expect(appealReviewPageSource).toContain('title: "Private appeal review"');
    expect(appealReviewPageSource).toContain('robots: { index: false, follow: false }');
    expect(appealReviewPageSource).toContain('import { ModerationAppealReviewQueue } from "@/components/moderation-appeal-review-queue";');
    expect(appealReviewPageSource).toContain("return <ModerationAppealReviewQueue />;");
  });

  it("proves /moderation-review is private and renders moderation review", () => {
    const moderationReviewPageSource = readFileSync(new URL("../app/moderation-review/page.tsx", import.meta.url), "utf8");

    expect(moderationReviewPageSource).toContain('title: "Private moderation review"');
    expect(moderationReviewPageSource).toContain('robots: { index: false, follow: false }');
    expect(moderationReviewPageSource).toContain('import { ModerationCaseReviewQueue } from "@/components/moderation-case-review-queue";');
    expect(moderationReviewPageSource).toContain("return <ModerationCaseReviewQueue />;");
  });

  it("proves /p/{handle}/posts binds the public archive and notFound boundary", () => {
    const profilePostsPageSource = readFileSync(new URL("../app/p/[handle]/posts/page.tsx", import.meta.url), "utf8");

    expect(profilePostsPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(profilePostsPageSource).toContain("listProfilePostsOnServer(handle)");
    expect(profilePostsPageSource).toContain("notFound();");
    expect(profilePostsPageSource).toContain("<ProfilePostArchive handle={handle} initialPage={initialPage} />");
  });

  it("proves /verification-review is private and renders verification review", () => {
    const verificationReviewPageSource = readFileSync(new URL("../app/verification-review/page.tsx", import.meta.url), "utf8");

    expect(verificationReviewPageSource).toContain('title: "Private verification review"');
    expect(verificationReviewPageSource).toContain('robots: { index: false, follow: false }');
    expect(verificationReviewPageSource).toContain('import { VerificationReviewQueue } from "@/components/verification-review-queue";');
    expect(verificationReviewPageSource).toContain("return <VerificationReviewQueue />;");
  });
});
