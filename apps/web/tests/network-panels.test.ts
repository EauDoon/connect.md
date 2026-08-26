import { readFileSync } from "node:fs";
import { createElement, Fragment, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ConnectionsPanel,
  ConnectionRequestsPanel,
  NotificationsPanel,
  NetworkConversationCard,
} from "../components/network-panels";
import type { Connection, ConnectionRequest, Notification } from "../lib/social-api";

const source = readFileSync(
  new URL("../components/network-panels.tsx", import.meta.url),
  "utf8",
);

function render<P>(value: (props: P) => ReactElement, props: P) {
  vi.stubGlobal("React", { createElement, Fragment });
  try {
    return renderToStaticMarkup(value(props));
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("private network presentation panels", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders typed connection props without exposing private identifiers", () => {
    const markup = render(ConnectionsPanel, {
      connections: [connection],
      conversationFor: () => undefined,
      loadState: "loaded",
      loadError: "",
      busy: null,
      loading: false,
      cursor: "cursor-1",
      canAct: () => true,
      onRefresh: () => undefined,
      onRetry: () => undefined,
      onStartConversation: () => undefined,
      onEndConnection: () => undefined,
      onLoadOlder: () => undefined,
    });

    expect(markup).toContain("Connections");
    expect(markup).toContain("@ari-chen");
    expect(markup).toContain("Start conversation");
    expect(markup).toContain("Remove");
    expect(markup).toContain("Block");
    expect(markup).toContain("Load older connections");
    expect(markup).not.toContain("owner");
    expect(markup).not.toContain("connection-1");
  });

  it("renders request and notification action boundaries from presentation props", () => {
    const requestMarkup = render(ConnectionRequestsPanel, {
      requests: [request],
      loadState: "loaded",
      loadError: "",
      busy: null,
      cursor: null,
      canAct: () => false,
      onRetry: () => undefined,
      onDecide: () => undefined,
      onLoadOlder: () => undefined,
    });

    expect(requestMarkup).toContain("Accept with messaging");
    expect(requestMarkup).toContain("Accept without messaging");
    expect(requestMarkup).toContain("Reject");
    expect(requestMarkup).toContain("Block");
    expect(requestMarkup.match(/<button[^>]*disabled/gu)).toHaveLength(4);
  });

  it("renders notification action boundaries from presentation props", () => {
    const notificationMarkup = render(NotificationsPanel, {
      notifications: [notification],
      loadState: "loaded",
      loadError: "",
      busy: null,
      cursor: null,
      canAct: () => true,
      onRetry: () => undefined,
      onRead: () => undefined,
      onLoadOlder: () => undefined,
    });

    expect(notificationMarkup).toContain("New connection request");
    expect(notificationMarkup).toContain("Mark new connection request read and open connection requests");
  });

  it("keeps callback wiring and accessible conversation links in the extracted family", () => {
    expect(source).toContain("onStartConversation(connection)");
    expect(source).toContain("onEndConnection(connection, \"remove\")");
    expect(source).toContain("onDecide(request, \"accept\", true)");
    expect(source).toContain("onRead(notification, action)");

    const markup = render(NetworkConversationCard, { conversation });
    expect(markup).toContain('href="/p/ari-chen"');
    expect(markup).toContain('href="/messages/conversation-1"');
    expect(markup).toContain('aria-label="Open conversation with @ari-chen"');
    expect(markup).toMatch(/<a\b(?=[^>]*href="\/messages\/conversation-1")(?=[^>]*class="[^"]*\bmin-h-11\b[^"]*")[^>]*>/u);
    expect(markup).not.toMatch(/<a\b[^>]*>(?:(?!<\/a>)[\s\S])*<a\b/u);
  });
});

const connection: Connection = {
  id: "connection-1",
  counterpartyProfileHandle: "ari-chen",
  messagingEnabled: true,
  createdAt: "2026-08-04T00:00:00Z",
  retentionExpiresAt: "2027-08-04T00:00:00Z",
};

const request: ConnectionRequest = {
  id: "request-1",
  counterpartyProfileHandle: "grace-hopper",
  direction: "inbound",
  messagingRequested: true,
  messagingConsent: null,
  status: "pending",
  createdAt: "2026-08-04T00:00:00Z",
  decidedAt: null,
  retentionExpiresAt: "2027-08-04T00:00:00Z",
};

const notification: Notification = {
  id: "notification-1",
  type: "connection_request.received",
  resourceType: "connection_request",
  createdAt: "2026-08-04T00:00:00Z",
  readAt: null,
};

const conversation = {
  id: "conversation-1",
  connectionId: "connection-1",
  counterpartyProfileHandle: "ari-chen",
  createdAt: "2026-08-04T00:00:00Z",
  retentionExpiresAt: "2027-08-04T00:00:00Z",
};
