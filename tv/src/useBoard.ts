import { useCallback, useEffect, useRef, useState } from 'react';

import { BOARD_URL, PAIRING_CODE, READ_TIMEOUT_MS, REFRESH_MS } from './config';
import type { Board } from './types';

export type BoardState = {
  board: Board | null;
  error: string | null;
  /** When the board was last read successfully (device time, ms). */
  updatedAt: number | null;
};

/** Reads the ops board now and every REFRESH_MS; a failed read keeps the last board on
 * screen (marked stale) rather than blanking the wall. */
export function useBoard(): BoardState {
  const [state, setState] = useState<BoardState>({ board: null, error: null, updatedAt: null });
  const inFlight = useRef(false);

  const read = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    // A request that never answers would otherwise hold inFlight and stop every later read.
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), READ_TIMEOUT_MS);
    try {
      const response = await fetch(BOARD_URL, {
        headers: PAIRING_CODE ? { 'x-crewops-pairing': PAIRING_CODE } : {},
        signal: abort.signal,
      });
      const data = (await response.json()) as { result?: Board; error?: string };
      if (!response.ok || !data.result) {
        throw new Error(data.error ?? `CrewOps answered ${response.status}`);
      }
      setState({ board: data.result, error: null, updatedAt: Date.now() });
    } catch (err) {
      const message = abort.signal.aborted
        ? 'CrewOps did not answer in time'
        : err instanceof Error
          ? err.message
          : 'CrewOps is unreachable';
      setState((s) => ({ ...s, error: message }));
    } finally {
      clearTimeout(timer);
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    read();
    const timer = setInterval(read, REFRESH_MS);
    return () => clearInterval(timer);
  }, [read]);

  return state;
}
