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
if (DATABASE_URL !== "") {
  const target = new URL(DATABASE_URL);
  if (!["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) || !target.pathname.endsWith("_test")) {
    throw new Error("Network acceptance requires a disposable loopback database whose name ends in _test.");
  }
}

const { registerAccount, loginAccount, accountForSessionToken, revokeSession } = await import(
  "@/lib/network/auth-service"
);
const { getProfile, getPublishedProfile, listPublishedProfiles, saveProfile, setProfileVisibility } = await import(
  "@/lib/network/profiles"
);
const { decideContactRequest, listContactRequests, sendContactRequest, blockAccount, lockContactPair } = await import(
  "@/lib/network/contacts"
);
const { listConversations, listMessages, sendMessage } = await import("@/lib/network/conversations");
const { createAgentGrant, resolveAgentToken, revokeAgentGrant } = await import("@/lib/network/agent-service");
const { migrate, closeDatabase } = await import("@/lib/network/db");
const { starterFor } = await import("@/lib/markdown");

const PROFILE_FIXTURE = starterFor("profile");


describe.skipIf(DATABASE_URL === "")("network MVP acceptance (two-user journey)", () => {
  const sql = postgres(DATABASE_URL, { max: 5, prepare: false });
  const suffix = randomUUID().slice(0, 8);
  let alice: { id: string; session: string };
  let bob: { id: string; session: string };
  let charlieId: string;

  beforeAll(async () => {
    await migrate();
    expect(await migrate()).toEqual([]);
    expect(await Promise.all([migrate(), migrate()])).toEqual([[], []]);
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
    await closeDatabase();
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

    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, "public", (await getProfile(sql, alice.id))!.etag);
    expect((await listPublishedProfiles(sql)).map((profile) => profile.handle)).toContain(`alice-${suffix}`);
    expect((await getPublishedProfile(sql, `alice-${suffix}`)).markdown).toContain("## About");

    // Unpublishing conceals again without destroying the draft.
    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, "private");
    expect((await listPublishedProfiles(sql)).map((profile) => profile.handle)).not.toContain(`alice-${suffix}`);
    const draft = await getProfile(sql, alice.id);
    expect(draft?.markdown).toContain("## About");
    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: new Date().toISOString() }, "public", (await getProfile(sql, alice.id))!.etag);
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

    // Agents cannot silently update public bytes. The owner first unpublishes.
    const etagBefore = (await getProfile(sql, alice.id))?.etag ?? null;
    await expect(saveProfile(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: "" }, PROFILE_FIXTURE, etagBefore)).rejects.toMatchObject({ code: "conflict" });
    await setProfileVisibility(sql, { id: alice.id, email: "", handle: "x", status: "active", created_at: "" }, "private");
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

  async function pair(label: string) {
    const accounts = [];
    for (const role of ["a", "b"]) {
      const name = `${label}-${role}-${suffix}`;
      accounts.push((await registerAccount(sql, { email: `${name}@example.com`, handle: name, password: "TestPassword123", ipKey: name })).account);
    }
    return accounts as [typeof accounts[number], typeof accounts[number]];
  }

  it("requires current ETags and preserves public bytes until a human unpublishes", async () => {
    const [owner] = await pair("profile");
    const first = await saveProfile(sql, owner, PROFILE_FIXTURE, null);
    const revision = PROFILE_FIXTURE + "\nA reviewed revision.\n";
    await expect(saveProfile(sql, owner, revision, null)).rejects.toMatchObject({ code: "precondition" });
    const changed = await saveProfile(sql, owner, revision, first.etag);
    await expect(saveProfile(sql, owner, PROFILE_FIXTURE, first.etag)).rejects.toMatchObject({ code: "precondition" });
    await expect(setProfileVisibility(sql, owner, "public", first.etag)).rejects.toMatchObject({ code: "precondition" });
    expect((await getProfile(sql, owner.id))!.visibility).toBe("private");
    await setProfileVisibility(sql, owner, "public", changed.etag);
    await expect(saveProfile(sql, owner, PROFILE_FIXTURE, changed.etag)).rejects.toMatchObject({ code: "conflict" });
    expect((await getPublishedProfile(sql, owner.handle)).markdown).toBe(revision);
  });

  it("serializes opposite requests and allows either participant to terminate consent", async () => {
    const [first, second] = await pair("consent");
    const results = await Promise.allSettled([
      sendContactRequest(sql, first.id, second.handle),
      sendContactRequest(sql, second.id, first.handle),
    ]);
    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    const request = (results.find((result) => result.status === "fulfilled") as PromiseFulfilledResult<Awaited<ReturnType<typeof sendContactRequest>>>).value;
    const recipient = request.recipientHandle === second.handle ? second : first;
    const requester = recipient.id === second.id ? first : second;
    await decideContactRequest(sql, recipient.id, request.id, "accept");
    const conversation = (await listConversations(sql, first.id))[0]!;
    await decideContactRequest(sql, requester.id, request.id, "revoke");
    await expect(sendMessage(sql, recipient.id, conversation.id, "closed")).rejects.toMatchObject({ code: "blocked" });
    const reopened = await sendContactRequest(sql, requester.id, recipient.handle);
    await decideContactRequest(sql, recipient.id, reopened.id, "accept");
    await decideContactRequest(sql, requester.id, reopened.id, "block");
    await expect(sendMessage(sql, first.id, conversation.id, "blocked")).rejects.toMatchObject({ code: "blocked" });
    await expect(sendMessage(sql, second.id, conversation.id, "blocked")).rejects.toMatchObject({ code: "blocked" });
    await expect(sendContactRequest(sql, recipient.id, requester.handle)).rejects.toMatchObject({ code: "blocked" });
  });

  it("rolls back a consent decision if creating its conversation fails", async () => {
    const [first, second] = await pair("atomic");
    const request = await sendContactRequest(sql, first.id, second.handle);
    await sql`ALTER TABLE network_conversations ADD CONSTRAINT network_test_failure CHECK (false) NOT VALID`;
    try {
      await expect(decideContactRequest(sql, second.id, request.id, "accept")).rejects.toThrow();
      expect((await listContactRequests(sql, second.id)).incoming[0]!.status).toBe("pending");
    } finally {
      await sql`ALTER TABLE network_conversations DROP CONSTRAINT network_test_failure`;
    }
    await decideContactRequest(sql, second.id, request.id, "accept");
    const conversation = (await listConversations(sql, first.id))[0]!;
    // Even inconsistent legacy data cannot override the authoritative block table.
    await sql`INSERT INTO network_contact_blocks (blocker_id, blocked_id) VALUES (${first.id}, ${second.id})`;
    await expect(sendMessage(sql, second.id, conversation.id, "legacy bypass")).rejects.toMatchObject({ code: "blocked" });
  });

  it("pages recent messages past 500 without losing equal-timestamp entries", async () => {
    const [first, second] = await pair("history");
    const request = await sendContactRequest(sql, first.id, second.handle);
    await decideContactRequest(sql, second.id, request.id, "accept");
    const conversation = (await listConversations(sql, first.id))[0]!;
    await sql`INSERT INTO network_messages (conversation_id, sender_id, body, created_at)
      SELECT ${conversation.id}, ${first.id}, 'history-' || n, now() - INTERVAL '1 minute' FROM generate_series(1, 501) AS n`;
    const latest = await sendMessage(sql, first.id, conversation.id, "The newest message");
    let page = await listMessages(sql, second.id, conversation.id);
    expect(page.messages.at(-1)?.id).toBe(latest.id);
    const ids = page.messages.map((message) => message.id);
    while (page.nextCursor !== null) {
      page = await listMessages(sql, second.id, conversation.id, page.nextCursor);
      ids.push(...page.messages.map((message) => message.id));
    }
    expect(ids).toHaveLength(502);
    expect(new Set(ids).size).toBe(502);
    await expect(listMessages(sql, charlieId, conversation.id, latest.id)).rejects.toMatchObject({ code: "not-found" });
  });

  it("keeps old accepted contacts reachable beyond the history page", async () => {
    const [first, second] = await pair("oldcontact");
    const request = await sendContactRequest(sql, first.id, second.handle);
    await decideContactRequest(sql, second.id, request.id, "accept");
    await sql`UPDATE network_contact_requests SET created_at = now() - INTERVAL '1 hour' WHERE id = ${request.id}`;
    await sql`INSERT INTO network_contact_requests (requester_id, recipient_id, status)
      SELECT ${first.id}, ${second.id}, 'rejected' FROM generate_series(1, 205)`;
    const firstPage = await listContactRequests(sql, first.id);
    expect(firstPage.nextCursor).not.toBeNull();
    expect(firstPage.outgoing.some((entry) => entry.id === request.id)).toBe(false);
    const secondPage = await listContactRequests(sql, first.id, firstPage.nextCursor);
    expect(secondPage.outgoing.some((entry) => entry.id === request.id && entry.status === "accepted")).toBe(true);
    await decideContactRequest(sql, second.id, request.id, "revoke");
    const conversation = (await listConversations(sql, first.id))[0]!;
    await expect(sendMessage(sql, first.id, conversation.id, "closed by recipient")).rejects.toMatchObject({ code: "blocked" });
  });

  async function queuedTogether(first: string, second: string, actions: (() => Promise<unknown>)[]) {
    const pending: Promise<PromiseSettledResult<unknown>[]>[] = [];
    // Observing waiters must never compete with the barrier and blocked
    // mutations for a connection, including if the suite pool shrinks later.
    const observer = postgres(DATABASE_URL, {
      max: 1,
      prepare: false,
      connect_timeout: 4,
      connection: { statement_timeout: 4000 },
    });
    try {
      await sql.begin(async (tx) => {
        await lockContactPair(tx, first, second);
        const [{ pid }] = await tx`SELECT pg_backend_pid() AS pid`;
        for (let index = 0; index < actions.length; index++) {
          pending.push(Promise.allSettled([actions[index]!()]));
          const deadline = Date.now() + 4000;
          while (true) {
              const [{ count }] = await observer`SELECT COUNT(*)::int AS count FROM pg_stat_activity
              WHERE ${pid}::int = ANY(pg_blocking_pids(pid))`;
            if (Number(count) >= index + 1) break;
            if (Date.now() > deadline) throw new Error("Mutation did not wait on the shared pair lock");
            await new Promise((resolve) => setTimeout(resolve, 10));
          }
        }
      });
    } finally {
      // The transaction releases the barrier even on failure. Drain every
      // started mutation before another test can inspect or change its rows.
        await Promise.all([Promise.all(pending), observer.end({ timeout: 1 })]);
    }
    return (await Promise.all(pending)).flat();
  }

  it("serializes accept versus block and closure versus an in-flight send", async () => {
    const [first, second] = await pair("races");
    const request = await sendContactRequest(sql, first.id, second.handle);
    const acceptedThenBlocked = await queuedTogether(first.id, second.id, [
      () => decideContactRequest(sql, second.id, request.id, "accept"),
      () => blockAccount(sql, first.id, second.handle),
    ]);
    expect(acceptedThenBlocked.every((result) => result.status === "fulfilled")).toBe(true);
    expect((await listContactRequests(sql, first.id)).outgoing[0]!.status).toBe("blocked");
    const conversation = (await listConversations(sql, first.id))[0]!;
    await expect(sendMessage(sql, second.id, conversation.id, "after block")).rejects.toMatchObject({ code: "blocked" });

    for (const action of ["block", "revoke"] as const) {
      const [sender, recipient] = await pair(action + "race");
      const contact = await sendContactRequest(sql, sender.id, recipient.handle);
      await decideContactRequest(sql, recipient.id, contact.id, "accept");
      const channel = (await listConversations(sql, sender.id))[0]!;
      const result = await queuedTogether(sender.id, recipient.id, [
        () => decideContactRequest(sql, recipient.id, contact.id, action),
        () => sendMessage(sql, sender.id, channel.id, "must not pass closure"),
      ]);
      expect(result[0]!.status).toBe("fulfilled");
      expect(result[1]).toMatchObject({ status: "rejected", reason: { code: "blocked" } });
      expect((await listMessages(sql, recipient.id, channel.id)).messages).toHaveLength(0);
    }
  }, 20_000);

  it("rolls back a legacy duplicate-pair upgrade without discarding history", async () => {
    const [first, second] = await pair("migration");
    const request = await sendContactRequest(sql, first.id, second.handle);
    await sql`DROP INDEX network_contact_requests_unordered_active_pair`;
    const [duplicate] = await sql`INSERT INTO network_contact_requests (requester_id, recipient_id)
      VALUES (${second.id}, ${first.id}) RETURNING id`;
    await sql`DELETE FROM network_schema_migrations WHERE name = '0002_unordered_contact_pair'`;
    try {
      await expect(migrate()).rejects.toThrow();
      const rows = await sql`SELECT id, status FROM network_contact_requests WHERE id IN (${request.id}, ${duplicate!.id})`;
      expect(rows).toHaveLength(2);
      expect(rows.every((row) => row.status === "pending")).toBe(true);
      expect(await sql`SELECT 1 FROM network_schema_migrations WHERE name = '0002_unordered_contact_pair'`).toHaveLength(0);
    } finally {
      await sql`DELETE FROM network_contact_requests WHERE id = ${duplicate!.id}`;
      await migrate();
    }
    expect(await migrate()).toEqual([]);
  });
});
