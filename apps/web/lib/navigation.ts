export const PRIMARY_NAVIGATION = [
  { href: "/discover", label: "Discover" },
  { href: "/human", label: "Create" },
  { href: "/network", label: "Network" },
  { href: "/agents", label: "Agents" },
] as const;

export const PUBLIC_PRIMARY_NAVIGATION = [
  { href: "/discover", label: "Discover" },
  { href: "/human", label: "Create" },
  { href: "/agent-directory", label: "Agent directory" },
] as const;

export const PUBLIC_UTILITY_NAVIGATION = [
  { href: "/trust", label: "Trust & data" },
] as const;

/**
 * Private destinations are deliberately separate from the public primary
 * navigation. The workspace is only an orientation layer: every destination
 * remains responsible for its own authenticated, server-authorized reads.
 */
export const WORKSPACE_NAVIGATION = [
  { href: "/human", label: "Documents", description: "Create, reopen, and maintain your canonical Profile or Resume Markdown." },
  { href: "/network", label: "Network", description: "Review private connection requests, conversations, and notifications." },
  { href: "/inbox", label: "Inbox", description: "Set a contact policy and manage mediated contact requests." },
  { href: "/feed", label: "Feed", description: "Publish and read the private chronological professional feed." },
  { href: "/applications", label: "Applications", description: "Review your own submitted application records and eligible withdrawals." },
  { href: "/employer", label: "Employer", description: "Manage organizations, roles, memberships, and application review when authorized." },
  { href: "/agents", label: "Agents", description: "Create bounded grants, review proposals, and revoke agent access." },
  { href: "/moderation", label: "Safety", description: "Review your private professional-post case status and eligible appeals." },
] as const;
