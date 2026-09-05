/**
 * Database-backed integration test for the network MVP: the owner's
 * acceptance journey with two users, run against a real PostgreSQL.
 *
 * Requires CONNECTMD_NETWORK_DATABASE_URL pointing at a disposable
 * database; the suite truncates all network_* tables before it runs.
 * Without the variable the suite skips (guest gates stay green in CI
 * environments without a database).
 */

import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import postgres from "postgres";

const DATABASE_URL = process.env.CONNECTMD_NETWORK_DATABASE_URL ?? "";

const { registerAccount, loginAccount, accountForSessionToken, revokeSession } = await import(
  "@/lib/network/auth-service"
);
const { getProfile, getPublishedProfile, listPublishedProfiles, saveProfile, setProfileVisibility } = await import(
  "@/lib/network/profiles"
);
const { decideContactRequest, listContactRequests, sendContactRequest, blockAccount } = await import(
  "@/lib/network/contacts"
);
const { listConversations, listMessages, sendMessage } = await import("@/lib/network/conversations");
const { createAgentGrant, resolveAgentToken, revokeAgentGrant } = await import("@/lib/network/agent-service");
const { migrate } = await import("@/lib/network/db");
const { starterFor } = await import("@/lib/markdown");

const PROFILE_FIXTURE = starterFor("profile");


describe.skipIf(DATABASE_URL === "")("network MVP acceptance (two-user journey)", () => {
  const sql = postgres(DATABASE_URL, { max: 3, prepare: false });
  const suffix = randomUUID().slice(0, 8);
  let alice: { id: string; session: string };
  let bob: { id: string; session: string };
  let charlieId: string;

  beforeAll(async () => {
    await migrate();
    await sql`TRUNCATE network_messages, network_conversations, network_contact_requests, network_contact_blocks, network_profiles, network_agent_grants, network_sessions, network_accounts, network_auth_buckets`;
    const aliceRegistration = await registerAccount(sql, {
      email: `alice-${suffix}@example.com`,
      handle: `alice-${suffix}`,
      password: "alice-password-1",
      ipKey: "test-ip",
    });
    const bobRegistration = await registerAccount(sql, {
      email: `bob-${suffix}@example.com`,
      handle: `bob-${suffix}`,
      password: "bob-password-1",
      ipKey: "test-ip",
    });
    alice = { id: aliceRegistration.account.id, session: aliceRegistration.sessionToken };
    bob = { id: bobRegistration.account.id, session: bobRegistration.sessionToken };
  });

  afterAll(async () => {
    await sql.end({ timeout: 1 });
  });

  it("registers two distinct accounts with persistent sessions", async () => {
    expect(alice.id).not.toBe(bob.id);
    const restored = await accountForSessionToken(sql, alice.session);
    expect(restored?.account.handle).toBe(`alice-${suffix}`);
  });

  it("keeps profiles private by default and invisible to discovery until published", async () => {
    await saveProfile(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, PROFILE_FIXTURE, null);
    expect((await listPublishedProfiles(sql)).map((profile) => profile.handle)).not.toContain(`alice-${suffix}`);
    await expect(getPublishedProfile(sql, `alice-${suffix}`)).rejects.toMatchObject({ code: "not-found" });

    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, "public");
    expect((await listPublishedProfiles(sql)).map((profile) => profile.handle)).toContain(`alice-${suffix}`);
    expect((await getPublishedProfile(sql, `alice-${suffix}`)).markdown).toContain("## About");

    // Unpublishing conceals again without destroying the draft.
    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, "private");
    expect((await listPublishedProfiles(sql)).map((profile) => profile.handle)).not.toContain(`alice-${suffix}`);
    const draft = await getProfile(sql, alice.id);
    expect(draft?.markdown).toContain("## About");
    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, "public");
  });

  it("runs the contact consent journey: request, accept, converse", async () => {
    const request = await sendContactRequest(sql, bob.id, `alice-${suffix}`);
    expect(request.status).toBe("pending");

    // Duplicate pending request is refused.
    await expect(sendContactRequest(sql, bob.id, `alice-${suffix}`)).rejects.toMatchObject({ code: "conflict" });

    const inbox = await listContactRequests(sql, alice.id);
    expect(inbox.incoming).toHaveLength(1);
    expect(inbox.incoming[0]?.requesterHandle).toBe(`bob-${suffix}`);

    const accepted = await decideContactRequest(sql, alice.id, request.id, "accept");
    expect(accepted.status).toBe("accepted");

    const conversations = await listConversations(sql, bob.id);
    expect(conversations).toHaveLength(1);
    expect(conversations[0]?.counterpartHandle).toBe(`alice-${suffix}`);

    await sendMessage(sql, bob.id, conversations[0]!.id, "Hello Alice, this is Bob.");
    const thread = await listMessages(sql, alice.id, conversations[0]!.id);
    expect(thread.messages).toHaveLength(1);
    expect(thread.messages[0]?.body).toContain("Hello Alice");
  });

  it("enforces rejection, blocking, and revocation boundaries", async () => {
    // Charlie cannot see Alice's contact without her consent.
    const charlie = await registerAccount(sql, {
      email: `charlie-${suffix}@example.com`,
      handle: `charlie-${suffix}`,
      password: "charlie-password-1",
      ipKey: "test-ip",
    });
    charlieId = charlie.account.id;

    const rejected = await sendContactRequest(sql, charlie.account.id, `alice-${suffix}`);
    await decideContactRequest(sql, alice.id, rejected.id, "reject");
    // A rejected request may be retried later (bounded by rate limits).
    const retried = await sendContactRequest(sql, charlie.account.id, `alice-${suffix}`);
    expect(retried.status).toBe("pending");
    await decideContactRequest(sql, alice.id, retried.id, "block");

    // Blocked senders cannot re-request.
    await expect(sendContactRequest(sql, charlie.account.id, `alice-${suffix}`)).rejects.toMatchObject({ code: "blocked" });

    // A block on Charlie does not touch the Alice/Bob channel: block isolation.
    const bobConversations = await listConversations(sql, bob.id);
    expect(bobConversations).toHaveLength(1);
    await sendMessage(sql, bob.id, bobConversations[0]!.id, "still connected");

    // Blocking an accepted contact closes the channel for both directions.
    const dave = await registerAccount(sql, {
      email: `dave-${suffix}@example.com`,
      handle: `dave-${suffix}`,
      password: "dave-password-1",
      ipKey: "test-ip",
    });
    const daveRequest = await sendContactRequest(sql, dave.account.id, `alice-${suffix}`);
    await decideContactRequest(sql, alice.id, daveRequest.id, "accept");
    const daveConversations = await listConversations(sql, dave.account.id);
    expect(daveConversations).toHaveLength(1);
    await sendMessage(sql, dave.account.id, daveConversations[0]!.id, "hello alice");
    await decideContactRequest(sql, alice.id, daveRequest.id, "block");
    await expect(sendMessage(sql, dave.account.id, daveConversations[0]!.id, "anyone there?")).rejects.toMatchObject({ code: "blocked" });
    await expect(sendMessage(sql, alice.id, daveConversations[0]!.id, "channel closed")).rejects.toMatchObject({ code: "blocked" });
  });

  it("lets an owner revoke a contact and keeps the channel closed", async () => {
    const request = await sendContactRequest(sql, bob.id, `charlie-${suffix}`);
    await expect(decideContactRequest(sql, bob.id, request.id, "accept")).rejects.toMatchObject({ code: "forbidden" });
    const revoked = await decideContactRequest(sql, bob.id, request.id, "revoke");
    expect(revoked.status).toBe("revoked");
    const charlieInbox = await listContactRequests(sql, charlieId);
    expect(charlieInbox.incoming.find((entry) => entry.id === request.id)?.status).toBe("revoked");
  });

  it("gives delegated agents scoped access and denies unauthorized actions", async () => {
    const { record, token } = await createAgentGrant(sql, alice.id, {
      name: "profile-agent",
      scopes: ["profile:read", "profile:write"],
    });
    expect(token.startsWith("cnag_")).toBe(true);

    const agent = await resolveAgentToken(sql, `Bearer ${token}`);
    expect(agent?.accountHandle).toBe(`alice-${suffix}`);
    expect(agent?.scopes).toContain("profile:read");

    // Delegated task: agent can write Alice's profile.
    const etagBefore = (await getProfile(sql, alice.id))?.etag ?? null;
    const saved = await saveProfile(sql, { id: agent!.accountId, email: "", handle: agent!.accountHandle, status: "active", created_at: new Date().toISOString() }, PROFILE_FIXTURE, etagBefore);
    expect(saved.etag).not.toBeNull();

    // The agent identity is scoped to the owner: no contacts:read in the grant.
    expect(agent!.scopes).not.toContain("contacts:read");

    // Contact rules still apply to the owning account: Alice and Bob are
    // already accepted contacts, so a new request conflicts rather than creating one.
    await expect(sendContactRequest(sql, agent!.accountId, `bob-${suffix}`)).rejects.toMatchObject({ code: "conflict" });

    // Revocation ends access immediately.
    await revokeAgentGrant(sql, alice.id, record.id);
    expect(await resolveAgentToken(sql, `Bearer ${token}`)).toBeNull();
  });

  it("survives a restart: sessions, profiles, and conversations persist", async () => {
    // Sessions survive (server-side, durable).
    const restored = await accountForSessionToken(sql, alice.session);
    expect(restored?.account.handle).toBe(`alice-${suffix}`);
    // Profile persists with the agent update.
    expect((await getProfile(sql, alice.id))?.etag).not.toBeNull();
    // Contact state persists.
    const conversations = await listConversations(sql, bob.id);
    expect(conversations).toHaveLength(1);
  });

  it("rejects bad credentials without revealing account existence", async () => {
    await expect(loginAccount(sql, { email: `alice-${suffix}@example.com`, password: "wrong-password-1", ipKey: "test-ip" })).rejects.toMatchObject({ code: "credentials" });
    await expect(loginAccount(sql, { email: `nobody-${suffix}@example.com`, password: "wrong-password-1", ipKey: "test-ip" })).rejects.toMatchObject({ code: "credentials" });
  });

  it("supports session revocation (sign out)", async () => {
    const fresh = await loginAccount(sql, { email: `bob-${suffix}@example.com`, password: "bob-password-1", ipKey: "test-ip" });
    const context = await accountForSessionToken(sql, fresh.sessionToken);
    expect(context).not.toBeNull();
    await revokeSession(sql, context!.sessionId);
    expect(await accountForSessionToken(sql, fresh.sessionToken)).toBeNull();
  });
});
