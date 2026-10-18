// Copy to src/local.ts (git-ignored) and fill in for your setup; it is built into the app.
//
// npm run dev on the same laptop as the Vega Virtual Device: keep these values, and forward
// the port once per simulator start (vega device start-port-forwarding --port 3000
// --forward false). A hosted CrewOps: its https address, and the speaker pairing code
// printed by `uv run python -m crewops.web_env --speaker`. Never commit local.ts: the
// pairing code lets a screen read the ops board.
export const local = {
  boardUrl: 'http://localhost:3000/api/tv/board',
  pairingCode: '',
};
