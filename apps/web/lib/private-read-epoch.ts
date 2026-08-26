export type PrivateReadEpoch = {
  current: number;
  inFlight: boolean;
};

export type ReadyPrivateReadEpoch = PrivateReadEpoch & {
  ready: boolean;
};

export function createPrivateReadEpoch(): PrivateReadEpoch {
  return { current: 0, inFlight: false };
}

export function createReadyPrivateReadEpoch(): ReadyPrivateReadEpoch {
  return { current: 0, inFlight: false, ready: false };
}

export function beginPrivateRead<T extends PrivateReadEpoch>(state: T): number {
  state.current += 1;
  state.inFlight = true;
  return state.current;
}

export function beginReadyPrivateRead(state: ReadyPrivateReadEpoch): number {
  state.ready = false;
  return beginPrivateRead(state);
}

export function finishPrivateRead<T extends PrivateReadEpoch>(state: T, requestEpoch: number): void {
  if (state.current === requestEpoch) state.inFlight = false;
}

export function privateReadIsCurrent<T extends PrivateReadEpoch>(state: T, requestEpoch: number): boolean {
  return state.current === requestEpoch;
}

export function markPrivateReadReady(state: ReadyPrivateReadEpoch, requestEpoch: number): void {
  if (state.current === requestEpoch) state.ready = true;
}

export function privateReadAllowsDependentWrite<T extends PrivateReadEpoch>(state: T): boolean {
  return !state.inFlight;
}

export function privateReadAllowsDependentAction(state: ReadyPrivateReadEpoch): boolean {
  return state.ready && !state.inFlight;
}
