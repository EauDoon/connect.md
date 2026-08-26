import { afterEach, describe, expect, it, vi } from "vitest";

import { MESSAGE_MAX_LENGTH, appendCursorPage, authSubjectIsCurrent, createConnectionRequest, decideConnectionRequest, listConnectionRequestInboxForSubject, listConnectionsForSubject, listConversationsForSubject, listMessages, listMessagesForSubject, listNotificationsForSubject, notificationHubAction, sendMessage } from "../lib/social-api";

const request = { id: "11111111-1111-1111-1111-111111111111", counterparty_owner_id: "owner-hidden", counterparty_profile_handle: "ari-chen", direction: "inbound", messaging_requested: true, messaging_consent: true, status: "accepted", created_at: "2026-08-03T00:00:00Z", decided_at: "2026-08-03T00:01:00Z", retention_expires_at: "2027-08-03T00:00:00Z" };
const sentMessage = { id: "22222222-2222-2222-2222-222222222222", conversation_id: "33333333-3333-3333-3333-333333333333", created_at: "2026-08-03T00:00:00Z", retention_expires_at: "2027-08-03T00:00:00Z" };
const connection = { id: "44444444-4444-4444-4444-444444444444", counterparty_profile_handle: "ari-chen", messaging_enabled: true, created_at: "2026-08-03T00:00:00Z", retention_expires_at: "2027-08-03T00:00:00Z" };
const conversation = { id: sentMessage.conversation_id, connection_id: connection.id, counterparty_profile_handle: "ari-chen", created_at: "2026-08-03T00:00:00Z", retention_expires_at: "2027-08-03T00:00:00Z" };
const notification = { id: "55555555-5555-5555-5555-555555555555", type: "message.received", resource_type: "conversation", created_at: "2026-08-03T00:00:00Z", read_at: null };

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
function configure(response: unknown) { vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test"); vi.stubGlobal("crypto", { randomUUID: () => "social-request-1" }); const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status: 200, headers: { "Content-Type": "application/json" } })); vi.stubGlobal("fetch", fetchMock); return fetchMock; }

describe("private social API boundaries", () => {
  it("maps only recognized generic notification pairs to literal private hubs", () => {
    expect(notificationHubAction({ type: "connection_request.received", resourceType: "connection_request" })).toEqual({ href: "/network?view=requests", label: "Open connection requests" });
    expect(notificationHubAction({ type: "connection_request.accepted", resourceType: "connection" })).toEqual({ href: "/network?view=connections", label: "Open connections" });
    expect(notificationHubAction({ type: "connection_request.rejected", resourceType: "connection_request" })).toEqual({ href: "/network?view=requests", label: "Open connection requests" });
    expect(notificationHubAction({ type: "conversation.created", resourceType: "conversation" })).toEqual({ href: "/network?view=conversations", label: "Open conversations" });
    expect(notificationHubAction({ type: "message.received", resourceType: "conversation" })).toEqual({ href: "/network?view=conversations", label: "Open conversations" });
    expect(notificationHubAction({ type: "application.under_review", resourceType: "application" })).toEqual({ href: "/applications", label: "Open applications" });
    expect(notificationHubAction({ type: "application.accepted", resourceType: "application" })).toEqual({ href: "/applications", label: "Open applications" });
    expect(notificationHubAction({ type: "application.rejected", resourceType: "application" })).toEqual({ href: "/applications", label: "Open applications" });
    expect(notificationHubAction({ type: "message.received", resourceType: "conversation/secret-id?actor=hidden" })).toBeNull();
    expect(notificationHubAction({ type: "message.received", resourceType: "message" })).toBeNull();
    expect(notificationHubAction({ type: "application.accepted", resourceType: "application/secret-id?reviewer=hidden" })).toBeNull();
    expect(notificationHubAction({ type: "application.withdrawn", resourceType: "application" })).toBeNull();
    expect(notificationHubAction({ type: "unknown.event", resourceType: "connection_request" })).toBeNull();
  });

  it("creates a human connection request with an explicit messaging choice and idempotency", async () => {
    const fetchMock = configure({ ...request, direction: "outbound", status: "pending", messaging_consent: null });
    const created = await createConnectionRequest("ari-chen", true, async () => "clerk-human-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/connection-requests");
    expect(JSON.parse(String(init.body))).toEqual({ recipient_profile_handle: "ari-chen", messaging_requested: true });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("social-request-1");
    expect(created.counterpartyProfileHandle).toBe("ari-chen");
    expect(created).not.toHaveProperty("counterpartyOwnerId");
  });

  it("records explicit consent only on acceptance", async () => {
    const fetchMock = configure(request);
    await decideConnectionRequest(request.id, "accept", true, async () => "clerk-human-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.connect.test/v1/connection-requests/${request.id}/accept`);
    expect(JSON.parse(String(init.body))).toEqual({ messaging_consent: true });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("social-request-1");
  });

  it("sends bounded Markdown only through a conversation and guards account transitions", async () => {
    const fetchMock = configure(sentMessage);
    await sendMessage(sentMessage.conversation_id, "Hello **there**", async () => "clerk-human-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.connect.test/v1/conversations/${sentMessage.conversation_id}/messages`);
    expect(JSON.parse(String(init.body))).toEqual({ markdown: "Hello **there**" });
    expect(MESSAGE_MAX_LENGTH).toBe(4000);
    expect(authSubjectIsCurrent("human-b", "human-a")).toBe(false);
  });

  it("does not dispatch a private mutation when the signed-in subject changes while obtaining a token", async () => {
    const fetchMock = configure({ ...request, direction: "outbound", status: "pending", messaging_consent: null });
    let current = true;
    await expect(createConnectionRequest("ari-chen", false, async () => { current = false; return "clerk-human-b-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the server-derived message direction without accepting sender owner subjects", async () => {
    configure({ messages: [{ ...sentMessage, direction: "received", markdown: "Hello" }], next_cursor: null });
    const page = await listMessages(sentMessage.conversation_id, async () => "clerk-human-token");
    expect(page.items).toEqual([expect.objectContaining({ direction: "received", markdown: "Hello" })]);
    expect(page.items[0]).not.toHaveProperty("senderOwnerId");
  });

  it("follows opaque cursors for each private collection and stops a non-progressing page", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ requests: [request], next_cursor: "requests-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ connections: [connection], next_cursor: "connections-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ conversations: [conversation], next_cursor: "conversations-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ notifications: [notification], next_cursor: "notifications-next" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await listConnectionRequestInboxForSubject(async () => "clerk-human-token", () => true, "requests-cursor");
    await listConnectionsForSubject(async () => "clerk-human-token", () => true, "connections-cursor");
    await listConversationsForSubject(async () => "clerk-human-token", () => true, "conversations-cursor");
    await listNotificationsForSubject(async () => "clerk-human-token", () => true, "notifications-cursor");

    for (const [url, cursor] of [
      [fetchMock.mock.calls[0]?.[0], "requests-cursor"],
      [fetchMock.mock.calls[1]?.[0], "connections-cursor"],
      [fetchMock.mock.calls[2]?.[0], "conversations-cursor"],
      [fetchMock.mock.calls[3]?.[0], "notifications-cursor"],
    ]) {
      expect(new URL(String(url)).searchParams.get("cursor")).toBe(cursor);
    }
    expect(appendCursorPage([{ id: "known" }], { items: [{ id: "known" }, { id: "older" }], nextCursor: "known-cursor" }, "known-cursor")).toEqual({ items: [{ id: "known" }, { id: "older" }], nextCursor: null, cursorDidNotProgress: true });
  });

  it("does not dispatch private message history after a signed-in subject changes", async () => {
    const fetchMock = configure({ messages: [], next_cursor: null });
    let current = true;
    await expect(listMessagesForSubject(sentMessage.conversation_id, async () => { current = false; return "clerk-human-b-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch any private network collection after a subject changes during token resolution", async () => {
    const calls = [
      (token: () => Promise<string>, guard: () => boolean) => listConnectionRequestInboxForSubject(token, guard),
      (token: () => Promise<string>, guard: () => boolean) => listConnectionsForSubject(token, guard),
      (token: () => Promise<string>, guard: () => boolean) => listConversationsForSubject(token, guard),
      (token: () => Promise<string>, guard: () => boolean) => listNotificationsForSubject(token, guard),
    ];
    for (const call of calls) {
      const fetchMock = configure({ next_cursor: null });
      let current = true;
      await expect(call(async () => { current = false; return "clerk-human-b-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
      expect(fetchMock).not.toHaveBeenCalled();
    }
  });

  it("rejects missing private collection arrays instead of treating them as empty", async () => {
    const calls = [
      () => listConnectionRequestInboxForSubject(async () => "clerk-human-token", () => true),
      () => listConnectionsForSubject(async () => "clerk-human-token", () => true),
      () => listConversationsForSubject(async () => "clerk-human-token", () => true),
      () => listNotificationsForSubject(async () => "clerk-human-token", () => true),
      () => listMessages(sentMessage.conversation_id, async () => "clerk-human-token"),
    ];
    for (const call of calls) {
      configure({ next_cursor: null });
      await expect(call()).rejects.toMatchObject({ code: "server" });
    }
  });
});
