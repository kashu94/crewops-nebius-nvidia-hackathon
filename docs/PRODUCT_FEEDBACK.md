# Product feedback

For the submission form's Product Feedback section: for each developer tool, API and SDK
used, what it was used for, what worked, what needs work, how onboarding went, and whether
we would build with it again. Every "needs work" point has a full entry (task, steps,
expected, actual, severity, workaround, suggestion) in [FRICTION_LOG.md](FRICTION_LOG.md),
cited as FL-nnn.

## Summary

| Tool | Used for | Build again? |
|---|---|---|
| Nebius Token Factory | Serving the chat models through its OpenAI-compatible API | _To fill in after live runs_ |
| NVIDIA Nemotron 3.5 Lightning | Triage, the tool-calling advisor and the explainer | _To fill in after live runs_ |
| Model Context Protocol (spec 2025-11-25) and the MCP Python SDK | The CrewOps server: 35 tools over Streamable HTTP | Yes |
| Agent Skills | The CrewOps crew-control skill | Yes |
| Alexa Skills Kit, Proactive Events, Skill Messaging, Reminders, ASK CLI / SMAPI | The CrewOps Alexa skill: questions by voice, and a chime plus spoken alert when a disruption reaches a desk | Yes, with reservations |
| Amazon Bedrock (Titan Text Embeddings V2, Guardrails; Nova 2 Lite in earlier builds) | Semantic search; screening questions and answers | Yes |
| Amazon Bedrock AgentCore (Runtime, Memory, JWT authorizer) | Hosting the MCP server signed in with Cognito; remembering conversations | Yes |
| Amazon DynamoDB and Streams | The whole operation in one table; streams drive analysis and alerts | Yes |
| Amazon S3 Vectors | Hybrid search over rules, example questions and past scenarios | Yes |
| AWS Lambda, Amazon SQS, Amazon SNS, Amazon EventBridge Scheduler, AWS Systems Manager Parameter Store | Report ingestion, analysis, notifications, escalation, the Alexa endpoint, secrets | Yes |
| Amazon Cognito | Sign-in for every role and the Alexa account linking | Yes, with care |
| AWS AppSync Events | Live updates to the console | Yes |
| Amazon Polly | Spoken answers and the desk speaker's voice | Yes |
| AWS CDK (Python) | All infrastructure, nine stacks | Yes |
| Vega SDK and the Vega Virtual Device (Fire TV) | The ops-room wall display, driven by the remote | Yes, with reservations |
| AWS Amplify Hosting | The web app (Next.js, server-rendered), built from GitHub on every push | Yes |
| Kiro | Showing the MCP server and the Agent Skill working in a general agent | Yes |

## Nebius Token Factory

**Used for.** Serving NVIDIA Nemotron to the three model roles (triage, advisor, explainer)
from the MCP server on AgentCore Runtime and the Alexa skill's Lambda, through the
OpenAI-compatible chat completions API at `https://api.tokenfactory.nebius.com/v1/`, with
function calling for the advisor's read-only tools. The key lives in AWS Parameter Store.

**What worked well.**
- The OpenAI-compatible API meant the roles could keep their message shapes behind a
  translation layer, not a rewrite: one small client (`crewops.agent.nebius`) with plain `urllib`, so the MCP
  package and the Lambda ship no new dependency.
- Standard Bearer-token auth and a `/v1/models` endpoint, which let us add a one-command
  check that a key can use the configured models. With `?verbose=true` it also gives each
  model's price, context length and supported features (tools, reasoning), which made the
  model choice a measurement rather than a guess.
- Turning thinking off with `chat_template_kwargs: {"enable_thinking": false}` works and
  cut a short reply from about 150 billed tokens to 2.

**What needs work.**
- The public API reference names only Llama example models. The model we first planned on
  (Llama-3.1-Nemotron-70B-Instruct) turned out not to be served to our key (FL-222).
- Thinking is on by default for the Nemotron 3 models, and the switch that turns it off is
  not listed in the model's supported parameters (FL-223).

**Onboarding.** _To fill in: credits activation, getting the key, first call._

**Build again?** _To fill in after the live evaluation._

## NVIDIA Nemotron 3.5 Lightning

**Used for.** All three model roles. Triage classifies a question the router's rules and
semantic match could not place (JSON intent, no tools). The advisor runs the tool loop
over read-only crew-control tools and drafts an answer. The explainer rewrites verified
answers for speech. Every draft and rewrite is checked by the deterministic verifier
against the tool results before anyone hears it.

**What worked well.** One to two seconds per call, cheap enough that a $25 credit covers
thousands of questions, and dependable tool calls. Triage and the explainer worked first
time. _To add after more live runs: answers the verifier accepted, latency on the deployed
service._

**What needs work.** Left alone, the advisor gathered rather than concluded: it paged
through rows instead of using the lookup's sort and limit, and kept calling tools until the
round limit. When told not to call tools, it once wrote its own tool-call syntax as text
(FL-224). A prompt rule and a final no-tools round fixed both. _To add after more live runs._

**Build again?** _To fill in._

## Model Context Protocol and the MCP Python SDK

**Used for.** CrewOps is an MCP server (spec 2025-11-25, Streamable HTTP, `mcp` 1.30 with
FastMCP) with 35 tools: asking questions in plain words, the controller's queue,
disruptions, reporting, the four-eyes plan actions, the operations overview, admin tools,
and the deterministic crew-operations tools behind `crew_tool`. Each call runs as the
person signed in, with their role's rights.

**What worked well.** The protocol itself is clean and was quick to adopt: tool
annotations (read-only, destructive) map directly onto "ask before you commit", and
stateless Streamable HTTP fits a serverless runtime. The Python SDK's FastMCP gets a
server running in minutes; the client side (`ClientSession`, `streamable_http_client`)
made an end-to-end test harness and a command-line client straightforward.

**What needs work.**
- Plain-function tools run on the event loop, so one slow call blocks every other
  request, with no warning (FL-005).
- DNS-rebinding protection switches on silently from the bind address and answers 421 with
  no hint which hosts are allowed (FL-006).
- A session manager can run only once, which makes per-test servers awkward (FL-007).
- A tool returning `dict` arrives as escaped JSON text, not structured content, depending
  on the return annotation (FL-011).

**Onboarding.** Fast: from the getting-started guide to a working local server in well
under an hour; the hours went into the behaviours above, which the docs do not mention.

**Build again?** Yes. One server now serves our web app, our Alexa skill and any MCP host,
through one set of role checks.

## Agent Skills

**Used for.** `skills/crewops-crew-control`: teaches any agent with the server connected
to work crew control like a controller (ground rules, playbooks per role, voice phrasing,
error recovery), with a tool reference generated from the server.

**What worked well.** The format is simple (a `SKILL.md` with two required fields, and
optional references, scripts and assets), and progressive disclosure keeps the agent's
context small: the details load only when needed.

**What needs work.** It is not yet clear from the Alexa+ pages how an Agent Skill reaches
Alexa+ itself, as opposed to a coding agent; an Alexa+-specific example would help.

**Onboarding.** Under an hour from the specification to a validated skill.

**Build again?** Yes: it is the cheapest way to make an MCP server usable well by agents
we do not control.

## Alexa Skills Kit (custom skill, account linking, Proactive Events, Skill Messaging, Reminders, ASK CLI and SMAPI)

**Used for.** A CrewOps skill: "what's waiting", any crew-control question by voice, and,
when a disruption reaches a controller's desk, a notification (chime and yellow ring)
followed by the alert spoken through a reminder. Linked to the app's Cognito sign-in;
hosted on a Lambda function URL that verifies every request's signature and certificate.

**What worked well.** Account linking with an OAuth authorization-code server worked first
time once configured. Request signing is well specified, so verifying it ourselves (needed
for a function URL) was straightforward. SMAPI (`simulate-skill`, `update-skill-manifest`,
`set-interaction-model`, `get-skill-status`) is reliable and scriptable, and was faster
than the console for every step.

**What needs work.**
- A skill cannot make an Echo speak an alert unprompted: notifications only chime, and
  speaking one takes Skill Messaging plus a reminder, three APIs for one sentence; the
  Reminders reference does not say whether a Skill Messaging request counts as in session
  (FL-201).
- The Proactive Events reference lists endpoints with no mapping to countries; India is on
  the Europe endpoint, found on another page (FL-202).
- Alexa cannot trigger Lambda in Sydney, so the skill needed a function URL and its own
  request verification.
- The new console has no Alexa Notifications permission switch; it took the ASK CLI and the
  manifest (FL-217).
- The ASK CLI sign-in demanded an email the phone-only developer account did not have, and
  the console's sign-in error only said "There was a problem" (FL-218).
- The console's simulator spun on a request the skill never received, while
  `simulate-skill` worked at once (FL-219).
- Account linking needs Alexa's redirect URLs before the authorization server exists, but
  the page will not save without that server's details (FL-220).

**Onboarding.** Slower than the AWS side: about half a day from a new skill to a linked,
answering skill, mostly in the console and sign-in issues above.

**Build again?** Yes, with reservations: voice is the right interface for a controller
with their hands full, but proactive voice alerts need a simpler, supported path.

## Alexa+ (MCP add-ons)

**Used for.** Evaluated as the way to bring the CrewOps MCP server into Alexa+ directly.

**What worked well.** The direction (bring your own MCP server, discovered as tools) is
what a business agent needs.

**What needs work.** The integration is in Preview, needs OAuth 2.1 with PKCE and a
protected-resource metadata document rather than an authorizer-protected server, asks for
round trips under 500 ms, which a question answered with a model call does not meet, and
Alexa+ is not offered in India, where we build and test (FL-203).

**Build again?** Yes, once it is generally available: the server is ready for it.

## Amazon Bedrock (Nova 2 Lite, Titan Text Embeddings V2, Guardrails, Converse)

The model roles now run on NVIDIA Nemotron on Nebius Token Factory; Titan embeddings and
Guardrails are the Bedrock parts in use. Earlier builds ran the model roles on Nova 2 Lite,
and the notes below record that period as it was.

**Used for.** Nova 2 Lite triaged questions the rules could not route, drafted answers
through a tool loop, and reworded template answers for speech; every draft is verified against the
tool results before it is used. Titan V2 embeddings (512 dimensions) feed hybrid search.
Guardrails screen every question and answer, including prompt-attack filtering.

**What worked well.** The Converse API made the tool loop simple and model-agnostic; Nova
2 Lite accepted our strict JSON-schema tool definitions; the guardrail's prompt-attack
filter needed no tuning; latency through the global inference profile was good.

**What needs work.**
- Model ids that look right cannot be invoked from some regions without the right
  inference profile, and the per-region rules are not next to the ids (FL-020).
- Reworded answers sometimes add a number no tool returned; a grounded-rewrite option
  would help (FL-009).
- The model filled in a missing detail instead of asking (FL-012).
- A guardrail change never published a new version through CloudFormation, and replacing a
  version deleted the one the running app used (FL-021, FL-022).

**Onboarding.** Quick for the first call; about an hour on model ids and regions.

**Build again?** Yes. The verifier pattern (model drafts, engine facts) makes Nova safe to
use for a domain where a wrong number is a safety issue.

## Amazon Bedrock AgentCore (Runtime, Memory, JWT authorizer)

**Used for.** Hosting the MCP server (ARM64 code package, Streamable HTTP) behind a JWT
authorizer that accepts the app's Cognito access tokens, so every tool call arrives as the
signed-in person; Memory keeps each conversation's last turns for follow-up questions.

**What worked well.** The JWT authorizer removed a whole layer of auth code: tokens are
checked before the server sees a request. Deploying a code package (no container) from CDK
kept the build simple.

**What needs work.** Memory's `blob` payload is described as binary but is a JSON
document, and the read-back order is undocumented (FL-013). A Windows-built zip can lack
the Linux file permissions the runtime expects (documented in our README).

**Onboarding.** Half a day, most of it in the code-package format and authorizer settings.

**Build again?** Yes: it is the easiest way we found to host an authenticated MCP server.

## Amazon DynamoDB and DynamoDB Streams

**Used for.** One table for the whole operation (about 5,000 items through 31 December:
crew, flights, trips, certificates, duty clocks, reserves) plus disruptions, plans, audit
events, links and security events. Transactions make every plan action atomic and
conditioned on the state it expects; streams trigger analysis and notifications.

**What worked well.** Conditional transactions gave us four-eyes and conflict safety
without locks; streams with filter criteria made the event-driven design natural.

**What needs work.** The boto3 table resource silently re-serialises transaction items
(FL-004); a third stream reader risks throttling with no warning from CDK (FL-204); IAM is
not enforced by local mocks, so a missing grant passed every test and failed in AWS
(FL-191).

**Build again?** Yes.

## Amazon S3 Vectors

**Used for.** Hybrid (keyword plus semantic) search over the legality rules, example
questions and past scenarios.

**What worked well.** No cluster to run and pay for: an index is cheap and immediately
queryable, which suits a project that is idle most of the time.

**What needs work.** The local mock library sends unimplemented S3 Vectors calls to real
AWS instead of failing (FL-030).

**Build again?** Yes, for any small-to-medium semantic index.

## AWS Lambda, Amazon SQS, Amazon SNS, Amazon EventBridge Scheduler, AWS Systems Manager Parameter Store

**Used for.** SQS plus a worker Lambda ingests reports exactly once; Lambdas analyse
disruptions, notify (console, email, Alexa, desktop alerts) and escalate; SNS carries
admin-managed email alerts filtered by role; one-time EventBridge schedules check whether
a disruption was left untouched; Parameter Store holds the Alexa credentials and push keys.

**What worked well.** One-time schedules that delete themselves are a good fit for
"check again in 10 minutes", with no timer running while nothing happens. SNS filter
policies made per-role alerts simple.

**What needs work.** The Lambda Python runtime has no cryptography library (FL-213); a
function's logs can live outside `/aws/lambda/<name>` with no hint from the CLI (FL-195);
one SNS email endpoint holds only one subscription per topic, and pending subscriptions
are awkward to cancel (FL-027, FL-028); a second SQS queue policy silently replaces the
first (FL-008).

**Build again?** Yes.

## Amazon Cognito

**Used for.** Sign-in for every role (one group per role), the Alexa account-linking
client, and the desk speaker's device account.

**What worked well.** Groups in the access token made role checks simple and verifiable
everywhere (the MCP server, the Alexa skill, the web app).

**What needs work.** CDK's default app client turns OAuth on with a placeholder callback
(FL-023); an empty write-attributes list means "all attributes" (FL-024); pool names are
not unique (FL-025); a password the policy refuses left users stuck mid-creation (FL-026).

**Build again?** Yes, with care: the defaults are more permissive than they look.

## AWS AppSync Events

**Used for.** Pushing every disruption status change to open consoles over WebSocket,
publish by IAM only, subscribe by Cognito sign-in with a role check in the handler.

**What worked well.** A managed pub/sub with no connection management on our side.

**What needs work.** The handler docs do not show what `ctx.identity` holds (FL-014), and
the WebSocket docs say "ID token" while the example is an access token (FL-116).

**Build again?** Yes.

## Amazon Polly

**Used for.** Speaking answers in the web assistant and the desk speaker's announcements
(neural Indian-English voice).

**What worked well.** Fast, natural, and simple to call from the web server.

**What needs work.** Nothing significant.

**Build again?** Yes.

## AWS CDK (Python)

**Used for.** Every resource, in nine stacks, with synth tests asserting least-privilege
IAM, encryption and filters.

**What worked well.** Synth tests caught several configuration mistakes before deployment;
grants and constructs keep IAM tight.

**What needs work.** `cdk deploy` waits forever on a dropped connection with no output
(FL-002); `npx aws-cdk@2` can silently run an old global CLI (FL-003); `--app is required`
does not say which folder was searched (FL-194); every Python process loading CDK prints a
Node end-of-life banner (FL-010); convenience grants include more than their names suggest
(FL-029). On a new AWS project account, a service-control policy denied CDK outside the
home region with no hint why (FL-001).

**Build again?** Yes.

## Vega SDK and the Fire TV app (Vega OS)

**Used for.** `tv/`: the ops-room wall display. A React Native app on Vega OS that reads the
ops board every 10 seconds and is driven by the remote: the D-pad moves between desks and
disruptions, OK filters by desk or opens a disruption, Back closes it. Run in the Vega
Virtual Device.

**What worked well.** The SDK installer and the virtual device on a KVM Linux laptop worked
first time, and the virtual device starts quickly. It's React Native, so components and
state were familiar, and `TVFocusGuideView` with `Pressable` focus handling made D-pad
navigation straightforward, including trapping focus in an open panel. One `vpkg` build
runs on the simulator without changes.

**What needs work.** The project generator wrote into the repository root and replaced its
`.gitignore` (FL-221). Screenshots from the virtual device came out empty (FL-222). Reaching
a web server on the same laptop needs a reverse port forward whose flag doesn't say its
direction, set again after every restart (FL-223). The template had stray diff markers and a
placeholder app title (FL-224). The SDK runs on Linux and macOS only, so our Windows laptop
could not build it.

**Onboarding.** Install to "Hello" on the virtual device took one sitting; the generator
incident was the only real setback.

**Build again?** Yes, with reservations about the CLI. The runtime and the remote model
suited a wall display well; the tooling around it cost more time than the app itself.

## AWS Amplify Hosting

**Used for.** Hosting the web app: Next.js 16, server-rendered, `web/` inside a monorepo,
built from the `dev` branch by `amplify.yml` on every push, at an `amplifyapp.com` address
with HTTPS. The Fire TV app and the judges use the same address.

**What worked well.** Connecting GitHub and choosing a branch; `appRoot` for a monorepo;
builds of about five minutes with no servers to manage; HTTPS at once; every push redeploys.
It cost cents.

**What needs work.** When the wizard couldn't read `amplify.yml` it showed only a GitHub
API docs link (FL-228). The Next.js support page stops at version 15, though 16.4 worked
(FL-229). Server code has no AWS credentials until a compute role is attached, and console
environment variables reach the build, not the running server (FL-230).

**Onboarding.** About half an hour from "Create new app" to the live site, including the
build spec.

**Build again?** Yes: the shortest path from a Next.js repository to a hosted HTTPS app on
AWS.

## Kiro

**Used for.** Showing the MCP server and the Agent Skill working in a general-purpose
agent: the skill in `.kiro/skills/crewops-crew-control`, the server in
`.kiro/settings/mcp.json`, read-only tools approved in advance, and Kiro asking before it
submits or approves anything.

**What worked well.** Agent Skills load from a folder with no extra setup, and the skill is
picked up from its description or by name. Remote MCP servers over Streamable HTTP with a
bearer header just work once connected, and per-tool auto-approval fits a four-eyes
workflow: reading is free, changing anything is confirmed.

**What needs work.** A folder in Restricted Mode silently loads no skill or MCP server, and
trusting it needs a window reload (FL-225). `${VAR}` in `mcp.json` expands only when Kiro is
started from a shell holding the variables, and the log doesn't say which were empty
(FL-226). A short-lived bearer token can't be refreshed (FL-227).

**Onboarding.** Under an hour, most of it on the two problems above.

**Build again?** Yes: it's the agent we would point developers at to try an MCP server and a
skill together.

## Feature requests

| Request | Why it matters to CrewOps | Priority |
|---|---|---|
| Token Factory: list every served model id, with whether it supports function calling, in the public docs | We could not confirm the Nemotron model id or its tool-calling support before calling the API (FL-222) | Important |
| An opt-in "announce" event, or a notification whose spoken text the skill supplies | A controller must hear what happened the moment it lands, hands-free; today it takes three APIs | Critical |
| Alexa+ MCP add-ons: a latency limit per tool type, and acceptance of an authorizer-protected server | Deterministic lookups answer in milliseconds; a question that needs a model cannot meet 500 ms | Important |
| Alexa+ availability (or developer testing) in India | We cannot test an Alexa+ integration where we build | Important |
| The Alexa Notifications permission and event publications back in the console | Today they need the ASK CLI and a manifest upload | Important |
| A country-to-endpoint table on the Proactive Events reference | Sending to the wrong region fails silently for that account | Nice-to-have |
| A grounded-rewrite option in the Converse API | Lets a model reword verified answers without adding numbers | Important |
| Run sync tools off the event loop by default in FastMCP | One slow tool blocks every other request today | Important |
| A managed Lambda layer (or KMS operation) for web push cryptography | Desktop alerts need the cryptography library, absent from the runtime | Nice-to-have |
| Warn in CDK when a third Lambda reads one DynamoDB stream | Throttling would appear only under load | Nice-to-have |
| A Vega CLI screenshot command, and named directions for port forwarding | Checking a TV layout and reaching a local server shouldn't need a person or guesswork | Important |
| Runtime environment variables and a compute-role hint for Amplify SSR | Server routes failed with a 502 until a role was found and attached | Important |
| Kiro: refreshable tokens for remote MCP servers, and a note when Restricted Mode hides skills | A demo agent shouldn't need a token script and a window reload | Nice-to-have |
