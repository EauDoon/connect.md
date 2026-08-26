export type PublicDocumentInventoryItem = {
  kind: "profile" | "resume";
  slug: string;
  updatedAt: string;
};

export type ContactPolicyMode = "open" | "request" | "representative_only" | "closed";
export type ContactPolicy = {
  mode: ContactPolicyMode;
  allowAgentMessages: boolean;
  dailyRequestLimit: number;
  representativeLabel: string | null;
  representativeUrl: string | null;
  etag: string;
};

export type OutreachStatus = "pending" | "accepted" | "rejected" | "blocked" | "reported";
export type OutreachThread = {
  id: string;
  senderName: string;
  senderAgent: string | null;
  subject: string;
  preview: string;
  targetIdentifier: string;
  receivedAt: string;
  status: OutreachStatus;
};
export type OutreachPage = { threads: OutreachThread[]; nextCursor: string | null };
