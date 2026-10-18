# CrewOps web app

The Alexa+ simulator, the controller and approver console, the crew phone view and the admin
alerts page. Next.js 16 (App Router), TypeScript and Tailwind.

The browser calls no AWS API except the live-events socket. Every action goes to this
app's server routes, which forward it to the CrewOps MCP server on AgentCore as the
signed-in user. Tokens stay in httpOnly cookies; only the live-events socket gets the ID
token.

## Run it

From the repository root, after the stacks are deployed:

```bash
uv run python -m crewops.web_env     # writes web/.env.local from the stack outputs
cd web
npm install
npm run dev                          # http://localhost:3000
```

Sign in as any member of the demo team. Spoken answers use Amazon Polly with the AWS
profile in `.env.local`; everything else uses only the signed-in user's token.

`npm run start` (production mode) marks the sign-in cookies Secure, so over plain http it
works only on `localhost`; use `npm run dev` on a local network address.

## Check it

```bash
npm test             # unit tests, offline
npm run typecheck
npm run lint
npm run build
```

## Layout

```
app/
  login/               Sign-in
  (app)/console/       Waiting work, live status feed, a disruption's plans and actions
  (app)/assistant/     The Alexa+ simulator: talk or type, spoken answers, follow-ups
  (app)/crew/          Crew phone view: my hours, report unavailable
  (app)/admin/         Alert addresses (admins)
  api/                 Sign-in, the MCP tool proxy, live-events token, speech
components/            Shell, signed-in user, small UI pieces
lib/                   Cognito, session cookies, MCP client, AppSync Events protocol
tests/                 Vitest unit tests
```

The tree above is the core. `app/` also holds the disruption list (`(app)/disruptions/`),
the reporters' Operations board (`(app)/operations/`) and Report page (`(app)/report/`),
the approvals queue (`(app)/approvals/`), the public `evaluation/` page and the desk
`speaker/`. Under `api/`, `push/` hands out the public key for desktop alerts, `speaker/`
pairs the desk speaker and serves its feed, and `tv/board` feeds the Fire TV wall display
(see [../tv/README.md](../tv/README.md)).