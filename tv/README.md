# CrewOps on Fire TV

The ops-room wall display: the board a crew control room puts on its big screen. It shows
the most serious and escalated disruptions first, each desk's load, reserve cover by airline
and base, and today's operation at a glance. The remote's D-pad moves between disruptions,
OK opens one, and the desk buttons filter the board to one desk or show all of them.

It is a React Native app for Vega OS (Fire TV), package `com.crewops.tv.nebius`. It only reads.
Every 10 seconds it fetches the web app's `/api/tv/board`, which calls the MCP server's
`ops_board` tool as the speaker device, so nobody signs in on the TV. If a read fails, the
last board stays on screen marked stale rather than going blank.

## Settings

Settings are built into the app from `src/local.ts`, which is git-ignored. Create it from
the example:

```bash
cp src/local.example.ts src/local.ts
```

The defaults (`http://localhost:3000/api/tv/board`, no pairing code) suit `npm run dev`
on the same laptop as the Vega Virtual Device. For a hosted CrewOps, set its https address
and the speaker pairing code printed by `uv run python -m crewops.web_env --speaker`. Never
commit `local.ts`: the pairing code lets a screen read the ops board.

## Build and run in the Vega Virtual Device

Start the web app first (`cd web && npm run dev`), then from this folder:

```bash
source ~/vega/env
npm install
npm run build:debug
vega virtual-device start
vega device start-port-forwarding --port 3000 --forward false
vega run-app build/x86_64-debug/crewopstv_x86_64.vpkg com.crewops.tv.nebius.main -d VirtualDevice
```

Port forwarding lets the virtual device reach `localhost:3000` on the laptop. Run it again
each time the virtual device starts.

## Check it

```bash
npm test
npm run lint
./node_modules/.bin/tsc --noEmit -p .
```
