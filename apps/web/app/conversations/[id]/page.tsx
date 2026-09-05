import type { Metadata } from "next";

import { ConversationView } from "@/components/network/conversation-view";
import { currentSession } from "@/lib/network/http";

export const metadata: Metadata = {
  title: "Conversation",
  description: "A consent-gated conversation between two connected accounts.",
  robots: { index: false, follow: false },
};

export const dynamic = "force-dynamic";

export default async function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const session = await currentSession();
  if (session === null || !/^[0-9a-f-]{36}$/i.test(id)) {
    return (
      <main className="mx-auto max-w-7xl px-5 py-16">
        <p className="text-mist" data-testid="conversation-unavailable">
          Sign in to view your conversations.
        </p>
      </main>
    );
  }
  return (
    <main className="pb-16">
      <section className="mx-auto max-w-4xl px-5 py-12 lg:px-8">
        <ConversationView conversationId={id} />
      </section>
    </main>
  );
}
