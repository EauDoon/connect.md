"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { TokenGetter } from "@/lib/api";
import { appendCursorPage, listConnectionRequestInboxForSubject, listConnectionsForSubject, listConversationsForSubject, listNotificationsForSubject, presentSocialError, type Connection, type ConnectionRequest, type Conversation, type Notification } from "@/lib/social-api";
import { beginReadyPrivateRead, createReadyPrivateReadEpoch, finishPrivateRead, markPrivateReadReady, privateReadAllowsDependentAction, privateReadIsCurrent, type ReadyPrivateReadEpoch } from "@/lib/private-read-epoch";

export type PrivateNetworkReadEpoch = ReadyPrivateReadEpoch;

export function createPrivateNetworkReadEpoch(): PrivateNetworkReadEpoch { return createReadyPrivateReadEpoch(); }
export function beginPrivateNetworkRead(state: PrivateNetworkReadEpoch): number { return beginReadyPrivateRead(state); }
export function markPrivateNetworkReadReady(state: PrivateNetworkReadEpoch, requestEpoch: number): void { markPrivateReadReady(state, requestEpoch); }
export function finishPrivateNetworkRead(state: PrivateNetworkReadEpoch, requestEpoch: number): void { finishPrivateRead(state, requestEpoch); }
export function privateNetworkReadIsCurrent(state: PrivateNetworkReadEpoch, requestEpoch: number): boolean { return privateReadIsCurrent(state, requestEpoch); }
export function privateNetworkReadAllowsDependentAction(state: PrivateNetworkReadEpoch): boolean { return privateReadAllowsDependentAction(state); }

type PrivateLoadState = "loading" | "loaded" | "error";
type NetworkSlice = "requests" | "connections" | "conversations" | "notifications";
type CurrentSubject = (requestSubject: string) => boolean;

const emptySliceEpochs = (): Record<NetworkSlice, PrivateNetworkReadEpoch> => ({
  requests: createPrivateNetworkReadEpoch(),
  connections: createPrivateNetworkReadEpoch(),
  conversations: createPrivateNetworkReadEpoch(),
  notifications: createPrivateNetworkReadEpoch(),
});

export function usePrivateNetworkReads({ subject, getToken, current, beginBusy, endBusy, onNotice }: { subject: string; getToken: TokenGetter; current: CurrentSubject; beginBusy: (slot: string) => boolean; endBusy: (slot: string) => void; onNotice: (notice: string | null) => void }) {
  const [requests, setRequests] = useState<ConnectionRequest[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [requestCursor, setRequestCursor] = useState<string | null>(null);
  const [connectionCursor, setConnectionCursor] = useState<string | null>(null);
  const [conversationCursor, setConversationCursor] = useState<string | null>(null);
  const [notificationCursor, setNotificationCursor] = useState<string | null>(null);
  const requestsRef = useRef(requests); requestsRef.current = requests;
  const connectionsRef = useRef(connections); connectionsRef.current = connections;
  const conversationsRef = useRef(conversations); conversationsRef.current = conversations;
  const notificationsRef = useRef(notifications); notificationsRef.current = notifications;
  const requestCursorRef = useRef(requestCursor); requestCursorRef.current = requestCursor;
  const connectionCursorRef = useRef(connectionCursor); connectionCursorRef.current = connectionCursor;
  const conversationCursorRef = useRef(conversationCursor); conversationCursorRef.current = conversationCursor;
  const notificationCursorRef = useRef(notificationCursor); notificationCursorRef.current = notificationCursor;
  const [requestLoadState, setRequestLoadState] = useState<PrivateLoadState>("loading");
  const [connectionLoadState, setConnectionLoadState] = useState<PrivateLoadState>("loading");
  const [conversationLoadState, setConversationLoadState] = useState<PrivateLoadState>("loading");
  const [notificationLoadState, setNotificationLoadState] = useState<PrivateLoadState>("loading");
  const [requestLoadError, setRequestLoadError] = useState("");
  const [connectionLoadError, setConnectionLoadError] = useState("");
  const [conversationLoadError, setConversationLoadError] = useState("");
  const [notificationLoadError, setNotificationLoadError] = useState("");
  const readEpochRef = useRef(emptySliceEpochs());
  const initialLoadStartedRef = useRef(new Set<NetworkSlice>());
  const initialLoadInFlightRef = useRef(new Set<NetworkSlice>());
  const moreInFlightRef = useRef(new Set<NetworkSlice>());
  const deliveredCursorsRef = useRef(new Map<NetworkSlice, Set<string>>([
    ["requests", new Set<string>()],
    ["connections", new Set<string>()],
    ["conversations", new Set<string>()],
    ["notifications", new Set<string>()],
  ]));

  const beginRead = (slice: NetworkSlice) => beginPrivateNetworkRead(readEpochRef.current[slice]);
  const readIsCurrent = (slice: NetworkSlice, requestEpoch: number) => privateNetworkReadIsCurrent(readEpochRef.current[slice], requestEpoch);
  const finishRead = (slice: NetworkSlice, requestEpoch: number) => { finishPrivateNetworkRead(readEpochRef.current[slice], requestEpoch); };
  const readAllowsDependentAction = (slice: NetworkSlice) => privateNetworkReadAllowsDependentAction(readEpochRef.current[slice]);
  const readEpoch = (slice: NetworkSlice) => readEpochRef.current[slice].current;
  const resetDeliveredCursor = (slice: NetworkSlice) => { deliveredCursorsRef.current.set(slice, new Set()); };

  const loadRequests = useCallback(async (initial = false) => {
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    if (initial) {
      if (initialLoadStartedRef.current.has("requests") || initialLoadInFlightRef.current.has("requests")) return;
      initialLoadStartedRef.current.add("requests"); initialLoadInFlightRef.current.add("requests");
    }
    const requestEpoch = beginRead("requests");
    setRequestLoadState("loading"); setRequestLoadError("");
    try { const page = await listConnectionRequestInboxForSubject(getToken, () => current(requestSubject)); if (!current(requestSubject) || !readIsCurrent("requests", requestEpoch)) return; markPrivateNetworkReadReady(readEpochRef.current.requests, requestEpoch); requestsRef.current = page.items; requestCursorRef.current = page.nextCursor; setRequests(page.items); setRequestCursor(page.nextCursor); resetDeliveredCursor("requests"); setRequestLoadState("loaded"); }
    catch (error) { if (current(requestSubject) && readIsCurrent("requests", requestEpoch)) { setRequestLoadState("error"); setRequestLoadError(presentSocialError(error)); } }
    finally { finishRead("requests", requestEpoch); if (initial) initialLoadInFlightRef.current.delete("requests"); }
  }, [current, getToken, subject]);

  const loadConnections = useCallback(async (initial = false) => {
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    if (initial) {
      if (initialLoadStartedRef.current.has("connections") || initialLoadInFlightRef.current.has("connections")) return;
      initialLoadStartedRef.current.add("connections"); initialLoadInFlightRef.current.add("connections");
    }
    const requestEpoch = beginRead("connections");
    setConnectionLoadState("loading"); setConnectionLoadError("");
    try { const page = await listConnectionsForSubject(getToken, () => current(requestSubject)); if (!current(requestSubject) || !readIsCurrent("connections", requestEpoch)) return; markPrivateNetworkReadReady(readEpochRef.current.connections, requestEpoch); connectionsRef.current = page.items; connectionCursorRef.current = page.nextCursor; setConnections(page.items); setConnectionCursor(page.nextCursor); resetDeliveredCursor("connections"); setConnectionLoadState("loaded"); }
    catch (error) { if (current(requestSubject) && readIsCurrent("connections", requestEpoch)) { setConnectionLoadState("error"); setConnectionLoadError(presentSocialError(error)); } }
    finally { finishRead("connections", requestEpoch); if (initial) initialLoadInFlightRef.current.delete("connections"); }
  }, [current, getToken, subject]);

  const loadConversations = useCallback(async (initial = false) => {
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    if (initial) {
      if (initialLoadStartedRef.current.has("conversations") || initialLoadInFlightRef.current.has("conversations")) return;
      initialLoadStartedRef.current.add("conversations"); initialLoadInFlightRef.current.add("conversations");
    }
    const requestEpoch = beginRead("conversations");
    setConversationLoadState("loading"); setConversationLoadError("");
    try { const page = await listConversationsForSubject(getToken, () => current(requestSubject)); if (!current(requestSubject) || !readIsCurrent("conversations", requestEpoch)) return; markPrivateNetworkReadReady(readEpochRef.current.conversations, requestEpoch); conversationsRef.current = page.items; conversationCursorRef.current = page.nextCursor; setConversations(page.items); setConversationCursor(page.nextCursor); resetDeliveredCursor("conversations"); setConversationLoadState("loaded"); }
    catch (error) { if (current(requestSubject) && readIsCurrent("conversations", requestEpoch)) { setConversationLoadState("error"); setConversationLoadError(presentSocialError(error)); } }
    finally { finishRead("conversations", requestEpoch); if (initial) initialLoadInFlightRef.current.delete("conversations"); }
  }, [current, getToken, subject]);

  const loadNotifications = useCallback(async (initial = false) => {
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    if (initial) {
      if (initialLoadStartedRef.current.has("notifications") || initialLoadInFlightRef.current.has("notifications")) return;
      initialLoadStartedRef.current.add("notifications"); initialLoadInFlightRef.current.add("notifications");
    }
    const requestEpoch = beginRead("notifications");
    setNotificationLoadState("loading"); setNotificationLoadError("");
    try { const page = await listNotificationsForSubject(getToken, () => current(requestSubject)); if (!current(requestSubject) || !readIsCurrent("notifications", requestEpoch)) return; markPrivateNetworkReadReady(readEpochRef.current.notifications, requestEpoch); notificationsRef.current = page.items; notificationCursorRef.current = page.nextCursor; setNotifications(page.items); setNotificationCursor(page.nextCursor); resetDeliveredCursor("notifications"); setNotificationLoadState("loaded"); }
    catch (error) { if (current(requestSubject) && readIsCurrent("notifications", requestEpoch)) { setNotificationLoadState("error"); setNotificationLoadError(presentSocialError(error)); } }
    finally { finishRead("notifications", requestEpoch); if (initial) initialLoadInFlightRef.current.delete("notifications"); }
  }, [current, getToken, subject]);

  const refresh = useCallback(async (initial = false) => {
    onNotice(null);
    await Promise.all([loadRequests(initial), loadConnections(initial), loadConversations(initial), loadNotifications(initial)]);
  }, [loadConnections, loadConversations, loadNotifications, loadRequests, onNotice]);
  const loading = [requestLoadState, connectionLoadState, conversationLoadState, notificationLoadState].some((state) => state === "loading");
  useEffect(() => { void refresh(true); }, [refresh]);

  const loadOlderRequests = async () => {
    const cursor = requestCursorRef.current;
    if (!cursor || !readAllowsDependentAction("requests") || moreInFlightRef.current.has("requests") || deliveredCursorsRef.current.get("requests")?.has(cursor)) return;
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    const requestEpoch = readEpochRef.current.requests.current;
    const busySlot = "requests-more";
    if (!beginBusy(busySlot)) return;
    moreInFlightRef.current.add("requests");
    try {
      const page = await listConnectionRequestInboxForSubject(getToken, () => current(requestSubject), cursor);
      if (!current(requestSubject) || !readIsCurrent("requests", requestEpoch)) return;
      const delivered = deliveredCursorsRef.current.get("requests") ?? new Set<string>();
      delivered.add(cursor);
      deliveredCursorsRef.current.set("requests", delivered);
      const next = appendCursorPage(requestsRef.current, page, cursor, delivered);
      requestsRef.current = next.items;
      requestCursorRef.current = next.nextCursor;
      setRequests(next.items);
      setRequestCursor(next.nextCursor);
    } catch (error) {
      if (current(requestSubject) && readIsCurrent("requests", requestEpoch)) onNotice(presentSocialError(error));
    } finally {
      moreInFlightRef.current.delete("requests");
      if (current(requestSubject)) endBusy(busySlot);
    }
  };

  const loadOlderConnections = async () => {
    const cursor = connectionCursorRef.current;
    if (!cursor || !readAllowsDependentAction("connections") || moreInFlightRef.current.has("connections") || deliveredCursorsRef.current.get("connections")?.has(cursor)) return;
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    const requestEpoch = readEpochRef.current.connections.current;
    const busySlot = "connections-more";
    if (!beginBusy(busySlot)) return;
    moreInFlightRef.current.add("connections");
    try {
      const page = await listConnectionsForSubject(getToken, () => current(requestSubject), cursor);
      if (!current(requestSubject) || !readIsCurrent("connections", requestEpoch)) return;
      const delivered = deliveredCursorsRef.current.get("connections") ?? new Set<string>();
      delivered.add(cursor);
      deliveredCursorsRef.current.set("connections", delivered);
      const next = appendCursorPage(connectionsRef.current, page, cursor, delivered);
      connectionsRef.current = next.items;
      connectionCursorRef.current = next.nextCursor;
      setConnections(next.items);
      setConnectionCursor(next.nextCursor);
    } catch (error) {
      if (current(requestSubject) && readIsCurrent("connections", requestEpoch)) onNotice(presentSocialError(error));
    } finally {
      moreInFlightRef.current.delete("connections");
      if (current(requestSubject)) endBusy(busySlot);
    }
  };

  const loadOlderConversations = async () => {
    const cursor = conversationCursorRef.current;
    if (!cursor || !readAllowsDependentAction("conversations") || moreInFlightRef.current.has("conversations") || deliveredCursorsRef.current.get("conversations")?.has(cursor)) return;
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    const requestEpoch = readEpochRef.current.conversations.current;
    const busySlot = "conversations-more";
    if (!beginBusy(busySlot)) return;
    moreInFlightRef.current.add("conversations");
    try {
      const page = await listConversationsForSubject(getToken, () => current(requestSubject), cursor);
      if (!current(requestSubject) || !readIsCurrent("conversations", requestEpoch)) return;
      const delivered = deliveredCursorsRef.current.get("conversations") ?? new Set<string>();
      delivered.add(cursor);
      deliveredCursorsRef.current.set("conversations", delivered);
      const next = appendCursorPage(conversationsRef.current, page, cursor, delivered);
      conversationsRef.current = next.items;
      conversationCursorRef.current = next.nextCursor;
      setConversations(next.items);
      setConversationCursor(next.nextCursor);
    } catch (error) {
      if (current(requestSubject) && readIsCurrent("conversations", requestEpoch)) onNotice(presentSocialError(error));
    } finally {
      moreInFlightRef.current.delete("conversations");
      if (current(requestSubject)) endBusy(busySlot);
    }
  };

  const loadOlderNotifications = async () => {
    const cursor = notificationCursorRef.current;
    if (!cursor || !readAllowsDependentAction("notifications") || moreInFlightRef.current.has("notifications") || deliveredCursorsRef.current.get("notifications")?.has(cursor)) return;
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    const requestEpoch = readEpochRef.current.notifications.current;
    const busySlot = "notifications-more";
    if (!beginBusy(busySlot)) return;
    moreInFlightRef.current.add("notifications");
    try {
      const page = await listNotificationsForSubject(getToken, () => current(requestSubject), cursor);
      if (!current(requestSubject) || !readIsCurrent("notifications", requestEpoch)) return;
      const delivered = deliveredCursorsRef.current.get("notifications") ?? new Set<string>();
      delivered.add(cursor);
      deliveredCursorsRef.current.set("notifications", delivered);
      const next = appendCursorPage(notificationsRef.current, page, cursor, delivered);
      notificationsRef.current = next.items;
      notificationCursorRef.current = next.nextCursor;
      setNotifications(next.items);
      setNotificationCursor(next.nextCursor);
    } catch (error) {
      if (current(requestSubject) && readIsCurrent("notifications", requestEpoch)) onNotice(presentSocialError(error));
    } finally {
      moreInFlightRef.current.delete("notifications");
      if (current(requestSubject)) endBusy(busySlot);
    }
  };

  return { requests, connections, conversations, notifications, requestsRef, connectionsRef, conversationsRef, notificationsRef, requestCursor, connectionCursor, conversationCursor, notificationCursor, requestLoadState, connectionLoadState, conversationLoadState, notificationLoadState, requestLoadError, connectionLoadError, conversationLoadError, notificationLoadError, loading, refresh, loadRequests, loadConnections, loadConversations, loadNotifications, loadOlderRequests, loadOlderConnections, loadOlderConversations, loadOlderNotifications, setRequests, setConnections, setConversations, setNotifications, readEpoch, readIsCurrent, readAllowsDependentAction };
}
