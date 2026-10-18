import { local } from './local';

// Where the wall display reads the ops board (the CrewOps web app's /api/tv/board) and the
// pairing code a hosted app asks for. Both come from src/local.ts, which is git-ignored:
// copy src/local.example.ts to create it.
export const BOARD_URL = local.boardUrl;
export const PAIRING_CODE = local.pairingCode;

/** How often the board is read again. A wall display needs no more than this. */
export const REFRESH_MS = 10_000;
