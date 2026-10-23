import { local } from './local';

// Where the wall display reads the ops board (the CrewOps web app's /api/tv/board) and the
// pairing code a hosted app asks for. Both come from src/local.ts, which is git-ignored:
// copy src/local.example.ts to create it.

const BOARD_PATH = '/api/tv/board';

/** The board's address from the configured one: the web app's own address (as the README
 * says to set for a hosted CrewOps) gets the board path added; a full board URL is kept. */
export function boardUrl(configured: string): string {
  const base = configured.trim().replace(/\/+$/, '');
  return base.endsWith(BOARD_PATH) ? base : `${base}${BOARD_PATH}`;
}

export const BOARD_URL = boardUrl(local.boardUrl);
export const PAIRING_CODE = local.pairingCode;

/** How often the board is read again. A wall display needs no more than this. */
export const REFRESH_MS = 10_000;

/** A read that has not answered by then is given up, so the next one can start. */
export const READ_TIMEOUT_MS = 8_000;
