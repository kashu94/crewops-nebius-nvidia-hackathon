import { act, renderHook } from '@testing-library/react-native';

import type { Card } from '../src/types';

// src/local.ts is git-ignored, so the tests bring their own settings.
jest.mock('../src/local', () => ({ local: { boardUrl: 'https://crewops.example/', pairingCode: '' } }), {
  virtual: true,
});

import { openedCard } from '../src/App';
import { boardUrl, READ_TIMEOUT_MS, REFRESH_MS } from '../src/config';
import { useBoard } from '../src/useBoard';

describe('boardUrl', () => {
  it('adds the board path to the web app address the README asks for', () => {
    expect(boardUrl('https://crewops.example/')).toBe('https://crewops.example/api/tv/board');
    expect(boardUrl('https://crewops.example')).toBe('https://crewops.example/api/tv/board');
  });

  it('keeps a full board address as it is', () => {
    expect(boardUrl('http://localhost:3000/api/tv/board')).toBe('http://localhost:3000/api/tv/board');
    expect(boardUrl('http://localhost:3000/api/tv/board/')).toBe('http://localhost:3000/api/tv/board');
  });
});

const card = (id: string, waiting: number): Card => ({
  disruption_id: id,
  status: 'OPTIONS_READY',
  handling: null,
  what: id,
  report_type: 'crew_unavailable',
  severity: 'high',
  reasons: [],
  desk: null,
  escalated: false,
  waiting_minutes: waiting,
  airlines: [],
  cover: [],
  impact: { flights: 1, passengers: 100, flight_ids: [] },
});

describe('openedCard', () => {
  it("shows the latest board's copy of the opened disruption", () => {
    expect(openedCard([card('D-1', 30)], 'D-1')?.waiting_minutes).toBe(30);
  });

  it('closes once the disruption has left the board', () => {
    expect(openedCard([card('D-2', 5)], 'D-1')).toBeNull();
    expect(openedCard(undefined, 'D-1')).toBeNull();
    expect(openedCard([card('D-1', 5)], null)).toBeNull();
  });
});

describe('useBoard', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('gives up on a read that never answers, so the next one runs', async () => {
    jest.useFakeTimers();
    const calls: AbortSignal[] = [];
    globalThis.fetch = jest.fn(
      (_url: unknown, init?: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          calls.push(init!.signal!);
          init!.signal!.addEventListener('abort', () => reject(new Error('aborted')));
        }),
    ) as unknown as typeof fetch;

    const { result, unmount } = renderHook(() => useBoard());
    expect(calls).toHaveLength(1);
    await act(async () => {
      jest.advanceTimersByTime(READ_TIMEOUT_MS);
    });
    expect(calls[0].aborted).toBe(true);
    expect(result.current.error).toBe('CrewOps did not answer in time');
    await act(async () => {
      jest.advanceTimersByTime(REFRESH_MS - READ_TIMEOUT_MS);
    });
    expect(calls).toHaveLength(2);
    unmount();
  });
});
