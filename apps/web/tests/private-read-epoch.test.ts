import { describe, expect, it } from "vitest";

import {
  beginPrivateRead,
  beginReadyPrivateRead,
  createPrivateReadEpoch,
  createReadyPrivateReadEpoch,
  finishPrivateRead,
  markPrivateReadReady,
  privateReadAllowsDependentAction,
  privateReadAllowsDependentWrite,
  privateReadIsCurrent,
} from "../lib/private-read-epoch";

describe("private read epochs", () => {
  it("preserves the existing base and ready state shapes", () => {
    expect(createPrivateReadEpoch()).toEqual({ current: 0, inFlight: false });
    expect(createReadyPrivateReadEpoch()).toEqual({ current: 0, inFlight: false, ready: false });
  });

  it("keeps a newer read authoritative when an older read settles", () => {
    const state = createPrivateReadEpoch();
    const first = beginPrivateRead(state);
    const second = beginPrivateRead(state);

    expect(privateReadIsCurrent(state, first)).toBe(false);
    expect(privateReadIsCurrent(state, second)).toBe(true);
    finishPrivateRead(state, first);
    expect(state).toEqual({ current: 2, inFlight: true });
    expect(privateReadAllowsDependentWrite(state)).toBe(false);

    finishPrivateRead(state, second);
    expect(privateReadAllowsDependentWrite(state)).toBe(true);
  });

  it("requires current readiness after a refresh", () => {
    const state = createReadyPrivateReadEpoch();
    const first = beginReadyPrivateRead(state);
    markPrivateReadReady(state, first);
    finishPrivateRead(state, first);
    expect(privateReadAllowsDependentAction(state)).toBe(true);

    const refresh = beginReadyPrivateRead(state);
    expect(privateReadAllowsDependentAction(state)).toBe(false);
    finishPrivateRead(state, refresh);
    expect(privateReadAllowsDependentAction(state)).toBe(false);

    markPrivateReadReady(state, first);
    expect(privateReadAllowsDependentAction(state)).toBe(false);
  });

  it("keeps the in-flight-only gate distinct from the ready gate", () => {
    const state = createPrivateReadEpoch();
    expect(privateReadAllowsDependentWrite(state)).toBe(true);
    const epoch = beginPrivateRead(state);
    expect(privateReadAllowsDependentWrite(state)).toBe(false);
    finishPrivateRead(state, epoch);
    expect(privateReadAllowsDependentWrite(state)).toBe(true);
  });
});
