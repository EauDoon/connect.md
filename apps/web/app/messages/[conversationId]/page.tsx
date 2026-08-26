import type { Metadata } from "next";
import { ConversationThread } from "@/components/conversation-thread";

export const metadata: Metadata = { title: "Private conversation", robots: { index: false, follow: false } };
export default async function MessagesPage({ params }: { params: Promise<{ conversationId: string }> }) { return <ConversationThread conversationId={(await params).conversationId} />; }
