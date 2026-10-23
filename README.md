# CrewOps

A voice-first agent for airline crew control, built for Alexa+ on the Model Context
Protocol. Its model layer is **NVIDIA Llama-3.1-Nemotron-70B-Instruct served by Nebius
Token Factory**; its backend runs on AWS.

Built for the [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/).
See [Nebius x NVIDIA: what runs where](#nebius-x-nvidia-what-runs-where).

A captain calls in sick at 01:30. Today a crew controller digs through rosters, duty-hour
sheets and certification records to find a legal replacement. With CrewOps they ask:

> "Alexa, Captain C-1042 is sick for P-2291. Who do I use?"

CrewOps answers with a recommendation that is legally checked, ranked by cost and cited.
Every crew id, duty hour and rupee in the answer comes from a deterministic rules engine,
never from the model's own arithmetic, and a verifier checks the final wording against the
tool results before anyone hears it.

> The model selects, sequences and narrates. It never calculates.

## What it does

It answers crew-control questions by voice: lookups ("who is on reserve at BLR?"),
replacements ("who covers P-2291?"), legality ("is C-2087 legal for P-2291?") and
consequences ("both A320 captains are sick, what's the joint plan?").

It reacts to disruptions without being asked. A sick call or an incident goes onto a queue,
and the disruption analysis works out the blast radius and the legal options, ranked by
cost, before the controller asks. Each change is pushed live to the console and, where the
admin has set it up, emailed to the role that must act.

Seats are covered the way crew control covers them. Cover only ever comes from the airline
that flies the trip, ranked by what it costs (callout, seniority and travel allowances,
positioning and any delay). When fewer than three of the seat's own rank are legal, the
regulated substitutes follow: a command-qualified first officer for a captain, a captain in
the right seat for a first officer, or one cabin crew short while one per 50 seats remains.
Cancelling is always the last choice, and no two-pilot aircraft is offered with one pilot.

"Alexa, anything waiting for me?" brings back pending work hours later: a plan to review for
a controller, a plan to sign off for an approver. Within a conversation, a reply such as
"Captain" or "is she legal?" is read with the question before it.

The desk hears about a disruption even when nobody is signed in. A desk speaker, running
under its own device account, chimes and glows amber when a disruption reaches its desk and
reads the brief on request. A controller signing in hears what came while they were away,
and a linked Echo (or the Alexa app) gets an Alexa notification from the CrewOps skill. A
Fire TV app puts the same operation on the ops-room wall.

Every crew change has four eyes on it. One person reports, a controller picks a plan, a
different person approves it, and only then is it committed in one transaction. Role checks
live in code, never in a prompt.

## Nebius x NVIDIA: what runs where

Every model call that reads or writes language goes to NVIDIA Nemotron on Nebius Token
Factory. Everything that stores, searches, secures or hosts stays on AWS.

| Role | Model | Served by | What it does |
|---|---|---|---|
| Triage | `nvidia/Llama-3.1-Nemotron-70B-Instruct-HF` | Nebius Token Factory | Classifies a question that neither the router's rules nor its semantic match could place. No tools; one JSON intent back. |
| Advisor | `nvidia/Llama-3.1-Nemotron-70B-Instruct-HF` | Nebius Token Factory | The tool-calling loop: sees the planned tool results as its own earlier calls, calls more read-only tools through the caller's role checks (OpenAI-style function calling), and drafts the answer. |
| Explainer | `nvidia/Llama-3.1-Nemotron-70B-Instruct-HF` | Nebius Token Factory | Rewrites a verified answer for speech. The rewrite is verified again. |
| Embeddings | Titan Text Embeddings V2 | Amazon Bedrock | Hybrid search over rules, example questions and past scenarios (S3 Vectors). |
| Screening | Bedrock Guardrails | Amazon Bedrock | Screens every question and answer, including prompt attacks. |

Nemotron never has the last word. Every crew id, duty hour and rupee comes from the
deterministic legality engine; a verifier checks each Nemotron draft and rewrite against
the tool results, and anything it cannot source is replaced by the template answer.

How the swap is built (`src/crewops/agent/nebius.py`): the three roles were written against
Bedrock's Converse shapes (content blocks, `toolUse` / `toolResult`, stop reasons, usage).
`NebiusClient.converse` takes and returns exactly those shapes and translates them to Token
Factory's OpenAI-compatible chat completions API, with plain `urllib` and no new dependency.
So the tool loop, the stop-reason checks (a cut-off or filtered reply is no answer), the
circuit breaker and every deterministic fallback work unchanged, and any Token Factory model
with tool calling is a setting, not code. The API key is a SecureString in Parameter Store,
read by the MCP server on AgentCore and by the Alexa skill's Lambda; their roles no longer
have any Bedrock permission for chat models.

The model layer is made of:

- A Token Factory client that speaks the roles' message shapes, Nemotron defaults for all
  three roles, and `python -m crewops.agent.nebius` to check a key against the live
  catalogue.
- Two circuit breakers, so a Token Factory outage and a Bedrock outage each pause only
  their own calls, with Token Factory 429 and 5xx counted as outages and 4xx as our fault.
- Infrastructure that passes the Token Factory URL and model ids to the MCP server and the
  Alexa skill, and grants each read access to the one Parameter Store key. Bedrock access is
  limited to the embedding model.
- Tests for the translation, errors, the key, and the advisor's tool loop through it.

Feedback on Nebius Token Factory and the NVIDIA model is in
[docs/PRODUCT_FEEDBACK.md](docs/PRODUCT_FEEDBACK.md#nebius-token-factory).

## Roles

| Role | Can do |
|---|---|
| Crew | See own schedule, report own unavailability |
| Reporter | Record any disruption: sick crew (one or several), certification expiry, delays, aircraft faults, station and gate closures, gate changes; follow their own reports |
| Controller | Review disruptions and options, submit one plan for approval |
| Approver | Approve or reject submitted plans; approval commits them |
| Auditor | Read-only access to every disruption, decision and audit record |
| Admin | Revoke a report filed in error (a reporter's, a crew member's own absence, planned leave) until cover is approved; reset the passwords of crew, reporters, controllers, approvers and auditors; book planned leave; tighten rule thresholds; read the system activity log; manage alert addresses. Never submits or approves a plan |
| Super admin | Everything an admin does, and also resets admins' passwords and relaxes a rule threshold back towards regulation (an admin may only tighten one) |
| Speaker | A device, not a person: hears the alert briefs for the desk or area its screen is set to; cannot read records, ask questions or change anything |

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roles-and-approvals).

## Architecture

```
 Alexa+ simulator (web)   Alexa Developer Console skill   Any MCP host
            \                        |                       /
             \                       |                      /
              ▼                      ▼                     ▼
        ┌──────────────────────────────────────────────────────┐
        │ Assistant agent · Strands on AgentCore Runtime        │
        │ router → advisor (Nemotron on Nebius) → verifier →    │
        │ explainer · Bedrock Guardrails · AgentCore Memory     │
        └──────────────────────────┬───────────────────────────┘
                                   │ MCP (Streamable HTTP)
        ┌──────────────────────────▼───────────────────────────┐
        │ CrewOps MCP server · tools + interactive cards        │
        └───────────┬───────────────────────────┬──────────────┘
                    ▼                           ▼
        Legality engine (pure Python)    S3 Vectors + Titan V2
                    │
                    ▼
        DynamoDB ──Streams──┬──► disruption analysis (Lambda)
                    ▲       └──► notifier (Lambda) ──► live console (AppSync Events)
                    │                              └─► email alerts (SNS, by role)
        SQS ingestion (live reports) · S3 roster uploads (batch)
```

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Why each piece was chosen:
[docs/DECISIONS.md](docs/DECISIONS.md).

## Services

| Nebius and NVIDIA | Role |
|---|---|
| Nebius Token Factory | Serves the chat models through its OpenAI-compatible API |
| NVIDIA Llama-3.1-Nemotron-70B-Instruct | Triage, the tool-calling advisor and the explainer |

| AWS | Role |
|---|---|
| Amazon Bedrock | Titan V2 embeddings, Guardrails |
| Amazon Bedrock AgentCore | Runtime for the agent and MCP server, Memory, Observability |
| Amazon DynamoDB | Operational data (single table) with Streams |
| Amazon S3 Vectors | Hybrid search over rules, example questions and past scenarios |
| Amazon SQS + AWS Lambda | Real-time disruption ingestion and background agents |
| Amazon SNS | Optional email alerts, assigned to roles by the admin |
| Amazon EventBridge Scheduler | One-time escalation checks for untouched disruptions |
| AWS Systems Manager Parameter Store | The Nebius Token Factory API key, the Alexa skill's credentials and the web push keys |
| AWS AppSync Events | Live updates to the console |
| Amazon Cognito | Sign-in, with one group per role |
| Amazon Polly | Spoken answers in the simulator |
| AWS CDK | Infrastructure as code |

## Repository layout

```
data/seed/              The seed operating week (JSON), carried forward to 31 December on load
data/eval/              Gold questions, paraphrases, reference scenarios and the held-out set
data/team/              The demo team: usernames and roles, nothing private
src/crewops/
  config.py             Runtime settings
  domain/               Typed records shared by every layer
  ingestion/            Seed loading, roster uploads (S3 + Lambda), live reports (SQS + Lambda)
  engine/               Legality engine: duty math, the seven rules, assessment, cost ranking,
                        blast radius, delay effects, joint plans
  analysis/             Disruption analysis, its stream Lambda and the run command
  search/               Hybrid keyword + semantic search over S3 Vectors
  store/                DynamoDB key layout, item conversion, reads and writes
  tools/                The tools agents call: schemas, role checks, dispatch
  agent/                Question pipeline: router, planner, verifier, templates, the model
                        roles and the Nebius Token Factory client
  auth/                 Sign-in: the team, token checks, claims to caller, account sync
  plans/                Plans and approvals, with the four-eyes rules
  mcp/                  The MCP server for AgentCore, its package builder and a client
  notify/               Live console events, admin-managed email alerts, escalation and
                        desktop alerts (web push)
  alerts.py             Spoken alerts for the desk speaker, "welcome back" and Alexa
  alexa/                The Alexa skill's endpoint, request checks and notifications
skills/                 The CrewOps Agent Skill (SKILL.md, references, a client script)
alexa/                  The Alexa skill package (manifest, interaction models) and the
                        account-linking template
web/                    The web app (Next.js): Alexa+ simulator, console, desk speaker,
                        crew view, admin
infra/                  AWS CDK app: one stack per part of the system
tests/                  Unit tests
docs/                   Architecture, decisions, friction log
```

The Fire TV wall display is a separate app in `tv/`; see [tv/README.md](tv/README.md).

## Getting started

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check .
```

## Deploying

Infrastructure is defined with the AWS CDK in Python and deployed to `ap-southeast-2`
(Sydney), the home region of the AWS project it runs in. You need
Node.js (22 or later recommended), an AWS CLI profile for the target account, and a Nebius
Token Factory API key (from the [Token Factory console](https://tokenfactory.nebius.com/)).

```bash
aws configure --profile crewops   # once: access keys stay in ~/.aws

scripts/cdk.sh bootstrap          # once per account and region
scripts/cdk.sh diff               # what would change
scripts/cdk.sh deploy --all       # create or update the stacks
scripts/cdk.sh destroy --all      # remove a dev stage completely
```

`scripts/cdk.sh` reads `.env`, refuses to run without a named profile, and prints the
target account before calling the latest CDK CLI. The CLI must be at least as new as the
`aws-cdk-lib` the app uses; an older global install fails with a "cloud assembly schema
version mismatch". On Windows, run the same settings directly:

```powershell
$env:AWS_PROFILE="crewops"; $env:AWS_REGION="ap-southeast-2"; $env:CREWOPS_STAGE="dev"
npx --yes aws-cdk@latest deploy --all --profile crewops
```

### Settings

Settings are read from a `.env` file in the repository root (not committed) and from the
environment; a value set in the shell wins over the file. Only `AWS_PROFILE` is required;
without a Nebius key the assistant still answers, from rules and templates alone.

| Variable | Default | Purpose |
|---|---|---|
| `AWS_PROFILE` | none | AWS CLI profile to deploy and run with; `default` is refused |
| `AWS_REGION` | `ap-southeast-2` | Region for every resource |
| `CREWOPS_STAGE` | `dev` | Prefix for resource names (`crewops-<stage>-...`) |
| `NEBIUS_API_KEY` | none | Token Factory API key for local runs; deployed services read Parameter Store instead |
| `CREWOPS_NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1/` | Token Factory's OpenAI-compatible API |
| `CREWOPS_ROUTER_MODEL_ID` | `nvidia/Llama-3.1-Nemotron-70B-Instruct-HF` | Triage model (Token Factory) |
| `CREWOPS_ADVISOR_MODEL_ID` | `nvidia/Llama-3.1-Nemotron-70B-Instruct-HF` | Tool-calling advisor model (Token Factory) |
| `CREWOPS_EXPLAINER_MODEL_ID` | `nvidia/Llama-3.1-Nemotron-70B-Instruct-HF` | Answer rewriting model (Token Factory) |
| `CREWOPS_EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2:0` | Embeddings for search (Bedrock) |
| `CREWOPS_EMBEDDING_DIMENSIONS` | `512` | 256, 512 or 1024 |
| `CREWOPS_GUARDRAIL_ID` | none | Bedrock guardrail, once deployed |
| `CREWOPS_GUARDRAIL_VERSION` | none | Published guardrail version; required with the id |
| `CREWOPS_MAX_TOOL_ITERATIONS` | `8` | Cap on the advisor's tool loop |
| `CREWOPS_RESPONSE_BUDGET_SECONDS` | `6.0` | Time budget for a spoken answer |
| `CREWOPS_ROUTER_SEMANTIC_FLOOR` | `0.45` | Least similarity for the router to borrow an example question's intent; set from `crewops.agent.calibrate` |
| `CREWOPS_QUEUE_SENDERS` | none | IAM ARNs, besides the MCP server, allowed to send reports to the queue (comma-separated) |
| `CREWOPS_ESCALATE_AFTER_MINUTES` | `10` | Minutes a disruption may wait untouched on a desk before the duty supervisor is alerted |
| `CREWOPS_ALEXA_SKILL_ID` | none | The Alexa skill's id; the Alexa stack is deployed only when it is set |
| `CREWOPS_ALEXA_REDIRECT_URLS` | none | The skill's three Account Linking redirect URLs (comma-separated) |
| `CREWOPS_ALEXA_EVENTS_STAGE` | `development` | `development` until the skill is certified, then `live` |

`CREWOPS_NEBIUS_API_KEY` is accepted as another name for `NEBIUS_API_KEY`. A template with
every setting is in [.env.example](.env.example): `cp .env.example .env`, then fill it in.

A minimal `.env`:

```bash
AWS_PROFILE=crewops
```

Access keys never go in `.env`; they stay in `~/.aws`.

### The Nebius Token Factory key

The deployed MCP server and Alexa skill read the key from Parameter Store, never from an
environment variable or a file. Store it once per stage, before or after deploying:

```bash
read -rs NEBIUS_API_KEY   # paste the key; it is not echoed or kept in shell history
aws ssm put-parameter --name /crewops-dev/nebius/api-key --type SecureString \
  --value "$NEBIUS_API_KEY" --profile crewops
```

Keep the default encryption key (no `--key-id`): the services' roles may read the parameter
but are not granted a customer-managed KMS key. To replace a key later, add `--overwrite`.

For local runs, `NEBIUS_API_KEY` in the shell is used first. Check that the key works and
that the configured models are served to it:

```bash
uv run python -m crewops.agent.nebius
```

It lists each role's model as served or not, and, if one is missing, the NVIDIA models the
key can use; set another through `CREWOPS_*_MODEL_ID` (any Token Factory model with tool
calling works for the advisor).

After the first deploy, load the operation into the table (about 5,000 items):

```bash
uv run python -m crewops.ingestion.load_seed --dry-run   # validate and count, no AWS calls
uv run python -m crewops.ingestion.load_seed             # validate, then write
```

The seed is one hand-built week (12 to 18 October 2026). The loader carries it forward to
31 December (`crewops.ingestion.calendar`), past the judging period: the same flights each
week with passengers booked varying a little, and the crew rotating within their airline,
rank and base, so nobody flies the same days for ever; the reserve pool stays on call. Every
generated week is checked with the legality engine (rest across the weekend, the 7- and
28-day limits, certificates) and anyone who would break a rule is swapped with a colleague,
so the roster is legal before any disruption. `--week-only` loads the seed week alone.

The deployed services run on the real clock (`CREWOPS_REAL_TIME=1`, set by their stacks):
flights before now count as flown, a disruption is reported for today up to two months
ahead, crew see their flights from five days back to two months ahead, and the Operations
board moves a week at a time across the same span. Tests, the evaluation and local runs keep
the seed's own moment (12 October, 18:00 UTC), so they give the same result every time.

Planned leave (annual leave, a planned operation, a course) is not a disruption. An admin
books it on the Admin page, up to the end of the rostered period, as rostering would. The
person is never offered as cover then, and any trips they already have in that period go to
a controller to re-crew in advance, with four-eyes approval as usual.
Nothing about it is urgent, so it gets the controller's email but no escalation, no Echo
chime, no desktop alert, no speaker announcement and no team emails.

Loading only writes, so a table that holds an older week keeps it beside the new one. On the
disposable `dev` and `test` stages, `--replace` empties the table first, including every
disruption, plan and audit event; any other stage refuses it.

```bash
uv run python -m crewops.ingestion.load_seed --replace   # dev/test: start from a clean table
```

### Uploading roster changes

Admins change the operation by uploading a JSON file to the roster bucket (its name is in the
`CrewOps-dev-Ingestion` stack outputs). The file holds any of the operation's collections in
the same shapes as the seed files, for example `{"pairings": [...]}`; the seed's
`rosters.json` and `rules.json` can be uploaded as they are.

```bash
aws s3 cp week-38.json s3://<roster-bucket>/uploads/week-38.json --profile crewops
aws s3 cp s3://<roster-bucket>/results/week-38.json.result.json - --profile crewops
```

A Lambda merges the upload over the current operation, validates the whole result, and
writes the uploaded records only if there are no errors. Records with the same id are
replaced and new ids are added; nothing is deleted. Every upload gets a report under
`results/` saying accepted or rejected, with the errors and any new warnings. Upload one
file at a time: two uploads processed at the same moment are each checked against the
operation as it was before either of them.

### Sending a live report

Reports go to the SQS ingest queue, where a worker Lambda validates them, checks the role
the sender states and every id they name, and stores each one once as a REPORTED
disruption. Only the MCP server may send reports, and it sets the sender from the user's
verified login. The queue's policy refuses everyone else unless their IAM ARN is listed in
`CREWOPS_QUEUE_SENDERS` when the ingestion stack is deployed. This development command
states the sender directly, so to keep using it, list your own IAM user's ARN there. Do the
same before moving messages back from the dead-letter queue, since that sends them again as
you.

```bash
uv run python -m crewops.ingestion.send_report crew_unavailable \
  --user u-17 --role reporter \
  --details '{"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"}'
```

Report types: `crew_unavailable`, `multiple_crew_unavailable`, `certification_lapse`,
`flight_delay`, `aircraft_technical`, `station_disruption`, `gate_closure`, `gate_change`.
Add `--dry-run` to validate without sending. Crew may report only their own unavailability; auditors and admins may not
report.

### Search indexes

After the first deploy, embed the rules, example questions and scenarios into S3 Vectors, and
try a query:

```bash
uv run python -m crewops.search.index --dry-run   # count documents, no AWS calls
uv run python -m crewops.search.index             # embed with Titan V2 and store
uv run python -m crewops.search.index --index rules --query "how much rest does a pilot need"
```

### Disruption analysis

Each new disruption is analysed automatically from the table's change stream and stored as
OPTIONS_READY. Disruptions reported before that existed, or whose analysis was parked after
a failure, can be analysed on demand; it is safe to run repeatedly. Each analysed
disruption goes to the controller desk with the fewest open disruptions; the reporter never
picks one:

```bash
uv run python -m crewops.analysis.run --pending
uv run python -m crewops.analysis.run --id r-demo-0001
```

The `dev` and `test` stages are disposable: destroying them deletes their data. Any other
stage keeps its table, with point-in-time recovery and deletion protection.

### The demo team

Sign-in uses a Cognito user pool with one group per role (the `CrewOps-<stage>-Auth` stack).
Nobody signs themselves up, and no email or phone is stored. The team is defined in
`data/team/demo_team.json`: three controllers (one per desk), three approvers, two
reporters, eleven crew members across the three airlines, an auditor, an admin, a super
admin, and the ops-room speaker device (`ops.speaker`). Usernames are first name and surname
(`ananya.iyer`); a crew member's starts with their crew number (`c1042.nair`). To create or
update the accounts after deploying the stack:

```bash
uv run python -m crewops.auth.provision            # what would change
uv run python -m crewops.auth.provision --apply    # asks for the accounts' password once
```

The password is read from `CREWOPS_TEAM_PASSWORD` in the shell, or asked for, checked against
the pool's rules before anything changes, and never stored. Every demo account shares it, so
treat it as a demo password only. Running the command again changes only what differs from
the file. An account removed from the file loses its roles and profile, and `--prune`
deletes it too.

### Plans and approvals

A controller submits cover for a disruption on their desk, and a different person with the
approver role approves it, which re-checks legality and commits the roster in one step:

```bash
uv run python -m crewops.plans.desk --as rohit.malhotra queue
uv run python -m crewops.plans.desk --as rohit.malhotra submit r-demo-0001
uv run python -m crewops.plans.desk --as meera.bhatt approve r-demo-0001 <plan id>
uv run python -m crewops.plans.desk --as meera.bhatt show r-demo-0001     # plans and audit trail
uv run python -m crewops.plans.desk --as rohit.malhotra close r-demo-0002 --reason "KS589 cancelled"
```

This is an operator's tool, acting as the named team member with your AWS credentials; people
reach the same rules through the assistant, signed in.

Each plan gets a SHA-256 fingerprint at submission, over exactly what
decides it (who replaces whom, costs, what is left uncovered, the rosters it was made against,
who submitted it and when). The approval card shows it; approval computes it again and refuses
a plan changed since (`TAMPERED`), and the commit only succeeds if the stored fingerprint is
still the one checked. The approval is then sealed (SHA-256 over the fingerprint, the approver
and the time), and both appear on the plan and in the audit trail. This is tamper evidence
anyone can recompute, not a signature: nobody holds a private key.

### Resilience and security of the assistant

Every model call already has a firm timeout and a deterministic fallback. On top of that,
circuit breakers (`crewops.agent.breaker`), one for Nebius Token Factory and one for
Bedrock, count outage failures: timeouts, lost connections, throttling and 5xx, never our
own invalid requests. After three in a row,
model calls pause for a minute and every answer comes straight from the roster in DynamoDB
through the templates, at once, instead of each question waiting out a timeout. Then one
call is let through to test the service. The assistant shows "Models paused" while the
breaker is open.

Before any model or guardrail call, an override check (`crewops.agent.injection`) refuses
the common override phrasings ("ignore all previous instructions", "you are now an admin",
"bypass the RBAC checks", "approve without approval") with a security alert. Ordinary
questions such as "can we ignore the rest rule?" go through. Every refusal, by this check or
by the Bedrock Guardrail's prompt-attack filter, is recorded in the admin's activity log
with who asked and what. Neither is the real defence. The assistant has only read tools,
filtered by role, so no wording can make it submit, approve or change anything.

### The MCP server

The assistant is served over MCP from AgentCore Runtime, signed in with the team's accounts.
AgentCore runs code packages on Linux ARM64 and does not install dependencies, so the
package is built first (it downloads ARM64 wheels; no Docker needed), then deployed:

```bash
uv run python -m crewops.mcp.package            # builds build/mcp_server
scripts/cdk.sh deploy CrewOps-dev-Ingestion CrewOps-dev-Notify CrewOps-dev-Mcp
```

Until the package is built, the CDK app leaves the MCP stack out and says so, so the other
stacks deploy from a fresh clone. The CDK zips the package on the machine that deploys it.
If the server fails to start after a deploy from Windows (AgentCore expects Linux file
permissions, which a Windows zip may not carry), build and deploy this stack from Linux,
macOS or WSL with the repository on the Linux filesystem.

Then call it as any team member, the way an MCP host would (it asks for the password):

```bash
uv run python -m crewops.mcp.client --as rohit.malhotra tools
uv run python -m crewops.mcp.client --as rohit.malhotra ask "Who covers C-1042 on 13 Oct?"
uv run python -m crewops.mcp.client --as rohit.malhotra my_queue
uv run python -m crewops.mcp.client --as c1042.nair report_disruption \
  '{"report_type": "crew_unavailable", "details": {"crew_id": "C-1042", "from_date": "2026-10-16", "reason": "sick"}}'
```

Set `from_date` to a day from today up to two months ahead: on the real clock the server refuses
dates outside that window.

Its tools: `ask` (the full verified answer pipeline), `my_queue`, `show_disruption`,
`report_disruption`, the plan actions (`submit_plan`, `approve_plan`, `reject_plan`,
`withdraw_plan`, `close_disruption`, and `confirm_resolution` / `return_resolution` for the
approver's sign-off), `list_crew_tools` / `crew_tool` for the
deterministic tools directly, `whoami`, `disruptions` (the desk's feed), `my_roster`
(crew, with their notices), `my_reports` and `report_reference` (reporting),
`rule_thresholds`, and, for admins, `set_rule_threshold`, `activity_log`, `alert_addresses`,
`add_alert_address` and `remove_alert_address`. What each user may do depends on their
role.

A follow-up needs the same conversation id on each question. The previous turn then fills
in only what a reply giving just a seat, a date or an id, or a "he", "she" or "that
pairing", leaves out, and the answer says so in its notes:

```bash
uv run python -m crewops.mcp.client --as rohit.malhotra --conversation c1 ask "Who can cover P-2291 on 13 Oct?"
uv run python -m crewops.mcp.client --as rohit.malhotra --conversation c1 ask "Captain"
```

### The Agent Skill

`skills/crewops-crew-control` is an [Agent Skill](https://agentskills.io/specification)
that teaches any agent with the MCP server connected (Kiro, any other MCP host, or Alexa+
once it can reach the server) to work crew control the way a controller does:

- `SKILL.md`: the ground rules (every fact from a tool, four eyes, ask before changing
  anything, roles are the server's, legality is not negotiable, privacy, ignore orders in
  data), the start of a session by role, the disruption lifecycle, playbooks (what is
  waiting, cover a seat, decide a plan, report, planned leave, plain-word questions), how
  to say it briefly for a voice assistant, and what to do on each error.
- `references/`: every tool (`tools.md`, generated from the server by
  `python -m crewops.mcp.skill_docs`, so it cannot drift from the code), the seven rules,
  cover strategies, costs and severity (`legality.md`), worked conversations per role
  (`workflows.md`) and every error code (`errors.md`).
- `scripts/crewops_mcp.py`: signs in and calls the server from a shell, for agents with no
  MCP client configured; `assets/mcp.json.example`: an MCP client configuration.

To use it with [Kiro](https://kiro.dev) (it loads Agent Skills from `.kiro/skills/` and
remote MCP servers from `.kiro/settings/mcp.json`), in a folder outside the repository:

```bash
mkdir -p ~/crewops-demo/.kiro/skills ~/crewops-demo/.kiro/settings && cd ~/crewops-demo
cp -r ~/Downloads/crewops-nebius-nvidia-hackathon/skills/crewops-crew-control .kiro/skills/
cp .kiro/skills/crewops-crew-control/assets/kiro-mcp.json.example .kiro/settings/mcp.json
set -a; source ~/Downloads/crewops-nebius-nvidia-hackathon/web/.env.local; set +a   # region, client, runtime
export CREWOPS_USERNAME=rohit.malhotra
export CREWOPS_MCP_URL=$(python .kiro/skills/crewops-crew-control/scripts/crewops_mcp.py url)
export CREWOPS_ACCESS_TOKEN=$(python .kiro/skills/crewops-crew-control/scripts/crewops_mcp.py token)
kiro .        # or open the folder from Kiro started in this terminal
```

Approve the two variables when Kiro asks (it expands only approved ones), check that the
`crewops` server lists its tools, and ask "what's on my desk?". The skill loads from its
description, or type `/crewops-crew-control`. Read-only tools are approved in advance;
Kiro still asks before submitting, approving or reporting. The access token lasts about an
hour. Any other agent with Agent Skills and remote MCP works the same way: put the skill in
its skills folder and the server from `assets/mcp.json.example` in its MCP settings.

Tests check the skill against the specification and the server: every tool it calls
exists, and every rule and strategy it names is the engine's.

### Live updates and alerts

Every disruption status change is published to the `/ops/disruptions` channel of an AppSync
Events API, which staff (not crew or reporters) can subscribe to with their sign-in; the web
console uses it to refresh. Email alerts are optional and only an admin can set them up:
an address is subscribed to the controller or approver alerts, AWS emails it a link to
confirm, and after that it receives that role's alerts (options ready, a plan sent back, a
plan waiting for approval). An address can instead follow one operational team: gate
control, station duty managers, flight information (airport screens, website, app),
operations monitoring (who tells the captain), maintenance control, training and crew
records, or customer care. Each analysed disruption emails the teams it needs, for example
a delay goes to flight information, operations monitoring, the station and customer care.
An address holds one role or team, and at most five addresses can be added a day.

```bash
uv run python -m crewops.mcp.client --as priya.desai add_alert_address \
  '{"email": "you@example.com", "role": "approver"}'
uv run python -m crewops.mcp.client --as priya.desai alert_addresses
```

#### Escalation

When a disruption reaches a desk (analysed, or its plan sent back), the
notifier creates a one-time EventBridge Scheduler schedule for 10 minutes later
(`CREWOPS_ESCALATE_AFTER_MINUTES`), which deletes itself after it runs. If by then nobody has
submitted a plan or closed it, the addresses subscribed to the duty supervisor audience
get an email, and the desk and room speakers announce "Escalated". If anything was done,
nothing happens. Subscribe an address the same way, with `"role": "duty_supervisor"`.

#### Desktop alerts (web push)

A controller or approver can click the bell in the web app's
header to get a notification from the browser when something reaches their desk or waits for
approval, even with CrewOps closed. Clicking the notification opens the disruption. The
browser's push service (Google, Microsoft or Mozilla) delivers it free of charge; each message
is encrypted for that one browser and signed with the app's key. Set up once, after the MCP
package is built and the Notify stack deployed:

```bash
uv run python -m crewops.notify.webpush keys   # the app's key pair, kept in Parameter Store
uv run python -m crewops.web_env               # writes the public key for the web app
```

Signing out turns desktop alerts off in that browser. Without the keys the bell does not show.

#### Severity

Every disruption is rated high, medium or low from its analysis, never by a model. It is
high when a flight is at risk however it is handled (no legal crew, the best way out is
to cancel or fly short, a seat left unfilled, a delay breaking a duty limit), medium when it
needs a real decision or flies today or tomorrow (counted from today, not from when it was
reported), and low when a reserve covers it with time to spare. The console and disruption list show it with the reasons, and the speaker, Alexa and
desktop alerts say it first ("Medium severity. …").

#### Absent on a day off

Someone reported unavailable on days they fly nothing is recorded, not put on a desk. There
is no alert, no escalation and nothing to plan, but they are not offered as cover on those
days, and the record notes their next duty. If they were on call (reserve)
then, it does go to a desk: the reserve pool at that base is one smaller. A reporter sees on
the Operations board who is out and when, never why.

Alerts and events name the disruption, its status, and for a team the flights, gates and
times it acts on, never a crew member. A crew member whose certificate lapses is told on
their own CrewOps page.

### The web app

The web app in `web/` is the Alexa+ experience and the desk's console. It needs Node.js 20
or later and the deployed Auth, Mcp and Notify stacks:

```bash
uv run python -m crewops.web_env     # writes web/.env.local from the stack outputs
cd web && npm install && npm run dev # http://localhost:3000
```

| View | Who | What it does |
|---|---|---|
| Disruptions | controllers, approvers, auditors, admins | Every disruption, filtered by desk (mine, other desks, the whole network), state (open, resolved, all) and type |
| Operations | reporters (their landing page) | The operating week: a chart of passengers by day and airline, then every flight of the chosen day with airline, aircraft, captain, first officer, cabin crew and load. Crew reported unavailable are struck through in rose, and each flight shows its open disruptions with their severity. Click a crew name to report them unavailable that day, or "Delay" to report the flight late; the report form opens filled in. A side panel lists everyone out, by whom and for which days |
| Report | reporters | Record any of the eight disruption types, picking crew, flights, gates and stations from the operation (a crew member taken ill part-way through a day from a time, a gate closure as an internal or outside cause), and follow every report you filed, and crew members' own, as waiting, refused with the reason, or its disruption's status |
| Controller | controllers, auditors, admins | Live figures for the role, the ingestion feed of disruptions, and the solution engine: the recommendation, each seat's legal cover ranked by cost with cancellation last, every rule's margin checked on demand, crew another desk also wants, and the plan submitted from there. Each disruption's full record adds coverage figures, its desk, crew another desk is competing for (with a joint plan across both), recommendation cards by policy, a plain explanation of the chosen option, every legal option to choose from (the first ten, then the rest on request; anyone reported unavailable since is struck through), the regulated substitutes, operating short or cancelling, each chosen only after a warning, cover suggested by other desks, every excluded candidate and why, the trip's flights and crew, plans and the audit trail. A committed disruption shows the options as they stood and the choice made. Every record says in one sentence how it gets resolved (re-crew, a decision, or handled internally), shows gate moves and late flights with passengers booked, and lists who has been told and why; one handled internally is resolved with a remark, which an approver then signs off (or sends back) on the Approver view |
| Approver | approvers, auditors | The four-eyes queue: each waiting plan, approved with a second confirming click or sent back with a reason |
| Crew | crew | Notices for me (a certificate about to lapse), this week's duty calendar (days reported unavailable in rose), my flights, duty-limit gauges, reporting myself unavailable (never twice for the same days), and what became of my reports |
| Admin | admins | Who does what (admin against super admin), revoking reports filed in error, password resets (a new random password shown once), rule thresholds (an admin tightens, only a super admin relaxes), the system activity log, alert addresses and planned leave |
| Alexa+ | everyone | Talk or type, hear the answer (Amazon Polly) with pause, resume and stop, and follow up. The latest exchange stands out from the conversation beside it. A round Alexa+ button in the corner of every page opens it. |

The app opens on sign-in, which offers to continue as whoever is signed in; the session ends
when the browser closes. The "View" menu lists only the views the signed-in roles allow, a
banner slides in with every disruption status change, and a Guide in the top bar and (i)
icons explain each screen and term.

The browser calls no AWS API except the live-events socket. The app's server forwards every
action to the MCP server as the signed-in user, so every role rule is the server's. Voice
input uses the browser's speech recognition (Chrome or Edge); elsewhere, type.

The app is hosted on AWS Amplify at https://dev.d2ibbbc4686wno.amplifyapp.com, built from the
`dev` branch by `amplify.yml` (Node 20, root `web/`). The Amplify console holds the same
`CREWOPS_*` variables as `web/.env.local` (without `AWS_PROFILE`); the build copies them into
`.env.production`, because Amplify passes console variables to the build, not to the running
server. Spoken replies on the hosted app need a compute role that may call
`polly:SynthesizeSpeech` (App settings, IAM roles); without one the browser's own voice speaks.

### The desk speaker and "welcome back"

`/speaker` is a desk or ops-room speaker. It belongs to a place, not a person: nobody signs
in on it, and no one's sign-in or sign-out affects it. The app's server signs in as the
speaker device (the speaker role, which hears only alert briefs) and the page polls its
feed. Set it to a desk (`/speaker?for=desk-2`), `approvals` or the whole `room`, and tap "Turn
on the speaker" once (browsers allow sound only after a tap). A new alert chimes, turns the
ring amber and is announced in the Polly voice, then repeated as a reminder 15 seconds after
each announcement ends, three times at most. It stops as soon as the disruption is dealt with
(a plan submitted, resolved or closed takes it off the desk's list) or someone taps "Got it"
or asks "What are my notifications?". Several alerts take turns, oldest first. After the
third announcement the ring stays amber until someone listens. Each alert is built from the
disruption's latest event and its analysis, never from a model.

The speaker also answers questions about the room, spoken (Ask) or typed: who it is, how many desks and
controllers there are and who they are, what is open on each desk or on "my desk", which desk
is busiest, how many disruptions are open, escalated or waiting for approval, and today's
flights, crew out and reserves. The answers come from the ops board by fixed rules
(`web/lib/room-answers.ts`), so they say nothing the wall display doesn't already show.
Anything else is turned away with a pointer to Alexa or the assistant, because questions about
people and legality need a signed-in person behind them.

```bash
uv run python -m crewops.web_env --speaker   # after provisioning the team
```

It gives `ops.speaker` a new random password of its own, set on the account (never the
team's), and a random pairing code it prints; both go into `web/.env.local`. Run with
`npm run dev`, a speaker needs no pairing. A hosted app (a production build) hears alerts
only on screens paired once with that code; the pairing cookie is signed with a server-side
secret, so it cannot be guessed. Re-running the command without `--speaker` keeps both;
with it, both are replaced.

When a controller or approver signs in, they see, and can hear, what came to their desk or
to approvals since they last had CrewOps open (an open console says so every few minutes);
nothing on a first sign-in, and a second tab replays nothing.

### The Alexa skill

The CrewOps skill (`alexa/skill-package`) answers "Alexa, ask crew ops what's waiting" and
any crew-control question ("ask crew ops tell me who covers P 2291") as the linked person,
with their role's access. When a disruption reaches a linked controller's desk, or a plan
waits for approval, their Echo chimes and the Alexa app shows a notification
(`AMAZON.MessageAlert.Activated`). A few seconds later the Echo says what happened: a
notification cannot carry words, so the notifier also wakes the skill (Skill Messaging API)
and the skill sets an Alexa reminder with the alert's words, which the Echo reads out when it
goes off. Asking "what's waiting" gives the latest updates at any time.

Alexa cannot trigger Lambda in ap-southeast-2, so the skill uses an HTTPS endpoint (a Lambda
function URL) that checks every request's Alexa signature, certificate, timestamp and skill
id. Notifications go out from the existing notifier on the table's stream, to the Alexa API
endpoint each person's account belongs to.

Setting it up, once:

1. In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask), signed in
   with the Amazon account your Alexa app uses, create a custom skill named CrewOps (English
   (India), plus English (US) and (Australia) if wanted) with your own hosting. Note the
   skill id, and on Build > Account Linking the three redirect URLs.
2. In `.env`: `CREWOPS_ALEXA_SKILL_ID=amzn1.ask.skill...` and
   `CREWOPS_ALEXA_REDIRECT_URLS=` the redirect URLs, comma-separated. Build the package and
   deploy:

   ```bash
   uv run python -m crewops.mcp.package
   scripts/cdk.sh deploy CrewOps-dev-Auth CrewOps-dev-Notify CrewOps-dev-Alexa
   ```

3. Copy `alexa/account-linking.template.json` to `alexa/account-linking.json` (git-ignored)
   and fill it from the Auth stack's outputs `AlexaAuthorizeUri`, `AlexaAccessTokenUri` and
   `AlexaClientId`, and the Alexa client's secret
   (`aws cognito-idp describe-user-pool-client --user-pool-id <pool> --client-id <id>`). Enter
   the same values on the console's Account Linking page (authorization code grant, HTTP
   Basic, scopes `openid` and `profile`).
4. Put the Alexa stack's `SkillEndpointUrl` in `alexa/skill-package/skill.json` (both
   endpoint `uri` values; the committed ones are our own deployment's), and import the manifest and the interaction models in
   the console (or deploy them with the ASK CLI). The endpoint uses the "wildcard
   certificate" option.
5. On the skill's Permissions page, switch on Alexa Notifications and Reminders, note
   the Login with Amazon client id and secret (Alexa Skill Messaging), and store them where
   the notifier reads them, never in a file:

   ```bash
   aws ssm put-parameter --name /crewops-dev/alexa/client-id --type String --value <id> --profile crewops
   aws ssm put-parameter --name /crewops-dev/alexa/client-secret --type SecureString --value <secret> --profile crewops
   ```

6. In the Alexa app: Skills > Your Skills > Dev > CrewOps: enable it, link your CrewOps
   account (sign in as a controller, for example `rohit.malhotra`), and allow notifications
   and reminders (without reminders the Echo only chimes).
   Then "Alexa, open crew ops". Notifications are sent to the development stage
   (`CREWOPS_ALEXA_EVENTS_STAGE=development`), which reaches the developer's own devices.

### Asking a question

Answers come from the tools and are verified before they are printed. Offline, no model is
called. With `--live`, Nemotron on Nebius Token Factory triages, advises and rewords, the
guardrail screens the question and the answer once `CREWOPS_GUARDRAIL_ID` is set, and the
tokens used are printed.

```bash
uv run python -m crewops.agent.ask "Who is on reserve at BLR on 13 Oct?"
uv run python -m crewops.agent.ask --role crew --crew-id C-1042 "How many duty hours do I have?"
uv run python -m crewops.agent.ask --live "C-1042 is sick, who covers?"   # live, with models
uv run python -m crewops.agent.ask --live --no-model "C-1042 is sick, who covers?"
uv run python -m crewops.agent.calibrate      # measure the router's similarity floor (live)
```

The guardrail is its own stack. After deploying it, copy its two outputs into `.env`. A
change to the guardrail publishes a new version number and keeps the old one, so update
`CREWOPS_GUARDRAIL_VERSION` after such a deploy:

```bash
CREWOPS_GUARDRAIL_ID=<GuardrailId output>
CREWOPS_GUARDRAIL_VERSION=<GuardrailVersionNumber output>
```

Set the floor that `calibrate` recommends as `CREWOPS_ROUTER_SEMANTIC_FLOOR` in `.env`.

### Evaluation

`data/eval/heldout.json` was written after the router rules were final and is never used to
change them. It rewords each of the 38 gold questions (same ids and dates, so the two
answers can be compared) and adds behaviour cases: off-topic questions, what each role may
see, questions back, follow-ups and prompt injection. Scoring is deterministic; no model
judges another.

```bash
uv run python -m crewops.agent.evaluate                       # offline, free
uv run python -m crewops.agent.evaluate --live --json build/evals/live.json
```

`--live` asks 99 questions (each rewording with its gold question, and both turns of each
follow-up), each with several model calls (Nemotron on Token Factory, Titan embeddings and
the guardrail on Bedrock), billed per token, and prints the calls and tokens used. It
reads the deployed table, so live agreement compares two live answers, not fixed ones.
Offline results, with no model and no semantic stage (rules and templates only):

| Measure | Offline |
|---|---|
| Rewordings whose answer states at least 80% of the gold answer's facts | 25 of 38 |
| Rewordings routed to the gold question's intent | 25 of 38 |
| Mean recall of the gold answer's facts | 0.72 |
| Off-topic questions that ran no tool | 7 of 8 |
| Role, question-back, follow-up and injection cases | 12 of 12 |
| Answers failing an independent re-verification against their tool results | 0 |
| Answers showing a redaction marker (personal data reached them) | 0 |

The rewordings the rules miss are what the semantic stage and the triage model are for;
the live run measures them.

The web app's public `/evaluation` page shows these reports: the figures above, results
by kind, and every question with its answer and checks, searchable; with both reports it
compares them side by side. Publish them by writing the reports into the web app:

```bash
uv run python -m crewops.agent.evaluate --json web/public/evals/offline.json
uv run python -m crewops.agent.evaluate --live --json web/public/evals/live.json   # billed per token
```

## Friction log

Problems hit while building are recorded as they happened in
[docs/FRICTION_LOG.md](docs/FRICTION_LOG.md), in five groups: Nebius Token Factory and
NVIDIA Nemotron, the Amazon ecosystem (Alexa, Bedrock, AgentCore, MCP), the AWS backend
infrastructure, the core software stack and local tooling, and the bugs that tests, reviews
and live runs found in our own code, with how each was fixed.

Product feedback for Nebius Token Factory, the NVIDIA model, and each other tool, API and
SDK used (what for, what worked, what needs work, onboarding, and whether we would build
with it again), with feature requests, is in [docs/PRODUCT_FEEDBACK.md](docs/PRODUCT_FEEDBACK.md).

## License

[MIT](LICENSE)
