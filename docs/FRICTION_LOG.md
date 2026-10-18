# Friction log

Problems hit while building CrewOps, across the whole stack, each written when it happened
with the real error text. Entries are grouped by where the problem was:

1. Amazon ecosystem: Alexa, Amazon Bedrock and Nova, AgentCore, and the Model Context
   Protocol (MCP) server and SDK.
2. AWS backend infrastructure: accounts, CDK, Cognito, DynamoDB, SQS, SNS, AppSync,
   Lambda, streams, the AWS SDK.
3. Core software stack and local tooling: Python, uv, Node, moto and other test
   tooling.
4. Our own code: every significant bug that tests, reviews, reference data or live runs
   found in what we wrote, and how it was fixed: the legality engine, the question pipeline,
   auth and plans, ingestion and search, the MCP server, evaluation, and the docs.

Entry numbers run in the order the entries were written, across all four groups. Entries
FL-020 onwards were written on 2026-10-08 from the build's full record (review reports,
failing test output and live runs), for problems found and fixed since 2026-10-06.

Severity: Blocker (no way forward without a workaround), High (cost hours), Medium (cost
under an hour), Low (annoyance). For our own code (section 4), severity is the harm the bug
would have done had it shipped, and High means a wrong answer, a security gap or lost data.

## Template

```
### FL-NNN: <short title>

- Date:
- Service / tool:
- Severity:
- Task attempted:
- Steps taken:
- Expected:
- Actual: (exact error text, screenshot in docs/friction/ if useful)
- Workaround:
- Suggestion: (for Amazon, or for the tool's maintainers)
```

Entries in section 4 are about our own code, so they use a shorter form:

```
### FL-NNN: <short title>

- Date:
- Where: (the part of the system)
- Found by: (review, failing test, reference data, live run)
- Severity:
- What went wrong: (the exact wrong output or error)
- Cause:
- Fix:
```

## 1. Amazon ecosystem (Alexa, Bedrock and Nova, AgentCore, MCP)

### FL-005: The MCP SDK's FastMCP runs plain-function tools on the event loop

- Date: 2026-10-07
- Service / tool: MCP Python SDK (`mcp` 1.30, FastMCP), hosted on AgentCore Runtime
- Severity: Medium (found in review before deployment, about an hour)
- Task attempted: Serve CrewOps' tools over streamable HTTP. The tools call DynamoDB,
  Bedrock and Cognito's key endpoint, so each one blocks for up to several seconds.
- Steps taken:
  1. Declared the tools as ordinary `def` functions, as most FastMCP examples do.
  2. Reviewed how FastMCP calls them: `func_metadata.call_fn_with_arg_validation` does
     `return fn(**arguments_parsed_dict)` for a non-async tool, directly on the event loop.
- Expected: A synchronous tool runs in a worker thread, as Starlette and FastAPI do for
  sync endpoints, or the documentation warns that it will not.
- Actual: No error and no warning. One slow question would hold every other request on
  the server until it finished.
- Workaround: Made every tool `async` and ran the blocking work with
  `anyio.to_thread.run_sync`, with locks around the state the tools share.
- Suggestion: Run sync tools in a thread pool by default, or say plainly in the FastMCP
  documentation that sync tools block the server.

### FL-006: FastMCP turns on DNS-rebinding protection silently, based on the host

- Date: 2026-10-07
- Service / tool: MCP Python SDK (`mcp` 1.30, FastMCP), streamable HTTP transport
- Severity: Low (about 15 minutes)
- Task attempted: Bind local runs of the MCP server to `127.0.0.1` (AgentCore needs
  `0.0.0.0`), and keep the in-process protocol tests working.
- Steps taken:
  1. Changed the default host from `0.0.0.0` to `127.0.0.1`.
  2. Ran the tests, which call the app through `httpx.ASGITransport` with base URL
     `http://test`.
- Expected: The bind address only decides where the server listens.
- Actual: Four tests failed with `httpx.HTTPStatusError: Client error '421 Misdirected
  Request' for url 'http://test/mcp'`. The only clue was a log line,
  `WARNING mcp.server.transport_security: Invalid Host header: test`. FastMCP turns on
  DNS-rebinding protection with a fixed host allow-list whenever the host is
  `127.0.0.1`, `localhost` or `::1`, and not for `0.0.0.0`.
- Workaround: The tests now use `http://127.0.0.1:8000`, as a real local client would.
- Suggestion: Name the allowed hosts in the 421 response body, and document that the bind
  address also switches this protection on.

### FL-007: An MCP session manager can run only once, which breaks per-test reuse

- Date: 2026-10-07
- Service / tool: MCP Python SDK (`mcp` 1.30), `StreamableHTTPSessionManager`
- Severity: Low (about 20 minutes)
- Task attempted: Exercise the real MCP protocol in-process from several tests against one
  server object.
- Steps taken:
  1. Built the FastMCP server once in a fixture and opened `server.session_manager.run()`
     in each test.
  2. Also replaced the client helper `streamablehttp_client`, now deprecated with the message
     "Use `streamable_http_client` instead.", with `streamable_http_client` and an
     explicit `httpx` client.
- Expected: A session manager can be started again after it stops, or the fixture pattern
  is documented.
- Actual: From the second test on: `RuntimeError: StreamableHTTPSessionManager .run() can
  only be called once per instance. Create a new instance if you need to run again.`
- Workaround: Build a new server for each test.
- Suggestion: Allow `run()` again after a clean shutdown, or add a testing helper that
  serves an app in-process.

### FL-009: Nova 2 Lite's reworded answer adds a number the tools never returned

- Date: 2026-10-08
- Service / tool: Amazon Bedrock, Amazon Nova 2 Lite (`global.amazon.nova-2-lite-v1:0`)
  through the Converse API
- Severity: Low (the verifier caught it; no wrong answer reached the user)
- Task attempted: First live question through the deployed MCP server: "Who covers C-1042
  on 15 Sep?", answered as Rohit (controller).
- Steps taken:
  1. The pipeline built the answer from the tools, then asked Nova 2 Lite to reword it for
     speech, keeping exactly the same facts.
  2. The verifier compared every id and number in the reworded text with the tool results.
- Expected: The rewording keeps the template's facts and adds none, as the prompt asks.
- Actual: The answer's notes read `model draft rejected: 1 unsourced claim(s) (number)`.
  The reworded text contained a number found in no tool result, so the template answer
  was given instead. Seen again on 2026-10-08, through the deployed MCP server: the reply
  "Captain" to "Who can cover P-2291 on 15 Sep?" (cover for the Captain seat, three legal
  day-off callouts at ₹24,000) carried the note `rewording dropped or changed a fact;
  template used` (`"model_calls": 1`). The rewrite lost or altered one of the template's
  ids, times or figures.
- Workaround: None needed: every model draft is verified, and the deterministic template
  is the fallback. This entry tracks how often it happens, for the evals.
- Suggestion: A Converse option to constrain a rewrite to the numbers and ids in the input
  (grounded rewording) would make the explainer usable more often.

### FL-011: An MCP tool that returns `dict` arrives as escaped JSON text

- Date: 2026-10-08
- Service / tool: MCP Python SDK (`mcp` 1.30, FastMCP), streamable HTTP client
- Severity: Low (about 15 minutes)
- Task attempted: Call the deployed `ask` tool (declared `-> dict`) from the command-line
  client and print the result.
- Steps taken:
  1. The client printed `structuredContent` when present, otherwise the text items.
  2. Called `ask` on the deployed server.
- Expected: A tool declared to return a dictionary returns it as structured content.
- Actual: `structuredContent` was empty, and the result was one text item holding the JSON,
  printed as `["{\n  \"answer\": \"C-1042 is not rostered on any pairing this week...`.
- Workaround: The client parses each text item as JSON before printing.
- Suggestion: Return structured content for any JSON-serialisable return value, or say in
  the FastMCP documentation which return annotations produce it.

### FL-012: The advisor model filled in a missing detail instead of asking

- Date: 2026-10-08
- Service / tool: Amazon Bedrock, Amazon Nova 2 Lite (`global.amazon.nova-2-lite-v1:0`),
  Converse API with tools
- Severity: Medium (found in the first live test of follow-up questions)
- Task attempted: Ask "Who can cover P-2291 on 15 Sep?" as a controller, without saying
  which seat. Offline, the pipeline asks "Which seat needs cover on P-2291?".
- Steps taken:
  1. Ran the question on the deployed MCP server, where Nova 2 Lite is the advisor when the
     planned tool calls produce nothing.
  2. Replied "Captain" in the same conversation.
- Expected: The advisor asks which seat. Its instructions tell it to ask the user, never
  pick, when an id is ambiguous; the same caution was expected for a missing seat.
- Actual: The answer (`"source": "model"`, verified) began "The cheapest legal option to
  cover the Captain seat on pairing P-2291 on 15 Sep is...". The model chose the Captain
  seat itself. Every fact in it was sourced, so the verifier passed it. The reply
  "Captain" then had no question to answer and listed 28 captains.
- Workaround: The pipeline now asks which seat itself, before any model is consulted,
  whenever cover is asked for without a seat or a person.
- Suggestion: A Converse option, or documented prompt pattern, that makes Nova ask a
  clarifying question when a tool's required context is missing, rather than choosing a
  value.

### FL-013: AgentCore Memory's `blob` payload is called binary but is a JSON document

- Date: 2026-10-08
- Service / tool: Amazon Bedrock AgentCore Memory, boto3 `bedrock-agentcore` client
- Severity: Low (about 30 minutes)
- Task attempted: Store each conversation turn's structured context next to its
  conversational text in `CreateEvent`, and read it back with `ListEvents`.
- Steps taken:
  1. Read the payload shape in the SDK's service model: `blob` is documented as "The binary
     content of the payload." but is typed as a JSON document, not bytes.
  2. Compared with AWS's own integrations, which store a JSON string and parse it on read.
- Expected: One documented form for `blob` (bytes, a string or a JSON value) and how it
  comes back from `ListEvents`.
- Actual: The description and the type disagree, and the read-back form is not documented.
  Request validation accepts a dictionary, so a mismatch would only show as memory that
  silently never returns anything.
- Workaround: Store the context as a JSON string and accept either a string or an object
  when reading.
- Suggestion: Document `blob` as what it is, with a round-trip example, and say in which
  order `ListEvents` returns events.

### FL-020: Bedrock model ids that look right but cannot be invoked here

- Date: 2026-10-06
- Service / tool: Amazon Bedrock model ids and inference profiles
- Severity: Medium (the first model call would have failed)
- Task attempted: Configure the router, advisor and explainer model ids.
- Steps taken: The first defaults were bare model ids of another model family; after the
  switch to Nova, `.env` held the `us.` inference profiles (`us.amazon.nova-micro-v1:0`,
  `us.amazon.nova-2-lite-v1:0`).
- Expected: A model id that is listed for the model works in the project's region.
- Actual: The first family accepts on-demand calls only through an inference profile, so its
  bare ids could not be invoked; the `us.` profiles are bound to US regions, so they do not
  work from the project's Sydney home region either. Nova 2 Lite has no in-region id there
  at all.
- Workaround: `amazon.nova-micro-v1:0` in-region and `global.amazon.nova-2-lite-v1:0`; a
  live Nova call was then confirmed.
- Suggestion: Show, per region, which id form each model accepts, next to the model id
  itself.

### FL-021: A guardrail change never published a new version

- Date: 2026-10-07
- Service / tool: Amazon Bedrock Guardrails, AWS CloudFormation
  (`AWS::Bedrock::GuardrailVersion`)
- Severity: Medium (found in review)
- Task attempted: Publish a new guardrail version whenever the guardrail's filters change.
- Steps taken: Defined a `CfnGuardrailVersion` beside the `CfnGuardrail`.
- Expected: A filter change produces a new version.
- Actual: `CfnGuardrailVersion` has only `guardrail_identifier` and `description`, and
  neither changes when the filters change, so CloudFormation sees no change and any filter
  edit silently has no effect.
- Workaround: The version's description carries a fingerprint of the guardrail configuration
  (`Configuration <sha256>`), so every change publishes a version. The app requires a
  numbered version and refuses DRAFT.
- Suggestion: Re-publish a guardrail version when its guardrail changes, or document the
  pattern.

### FL-022: Replacing a guardrail version deleted the one the running app used

- Date: 2026-10-07
- Service / tool: Amazon Bedrock Guardrails, AWS CloudFormation
- Severity: Low (found in review)
- Task attempted: Redeploy the guardrail stack while the app is pinned to the current
  version number.
- Steps taken: Changed the configuration so a new version was created.
- Expected: The old version stays until nothing uses it.
- Actual: CloudFormation creates the new version and then deletes the old one. A running app
  still pinned to the old number gets errors, so screening fails open until it is updated.
- Workaround: `apply_removal_policy(RemovalPolicy.RETAIN)` on the version; old versions are
  kept.
- Suggestion: Retain replaced guardrail versions by default, or warn in the deploy output.

### FL-201: A skill cannot make an Echo speak an alert unprompted

- Date: 2026-10-10
- Service / tool: Alexa Skills Kit, Proactive Events API
- Severity: Medium (shaped the whole alerting design)
- Task attempted: Have a controller's Echo announce a new disruption on their desk as it
  happens, even with no screen signed in.
- Steps taken: Read the Proactive Events documentation and the Alexa+ developer
  announcements of July 2026.
- Expected: A way for a skill to speak a short alert on a person's device.
- Actual: A skill can only send a notification from a fixed set of event types: the device
  chimes and shows a yellow ring, and the person asks "what are my notifications?" to hear
  it. Nothing lets a third-party skill speak unprompted, and the Alexa+ pages describe no
  push from an MCP server.
- Workaround: The skill sends `AMAZON.MessageAlert.Activated` ("a new message from CrewOps")
  and answers "what's waiting" with the details. To have the Echo say the alert, the notifier
  also wakes the skill through the Skill Messaging API, and the skill sets an Alexa reminder
  five seconds ahead whose text is the alert; the Echo reads a reminder out when it goes off.
  This takes a second permission (reminders) and three APIs for one spoken sentence, and the
  Reminders reference says a reminder cannot be created with an out-of-session token, without
  saying whether a Skill Messaging request counts as in session. The web desk speaker
  announces alerts itself.
- Suggestion: An opt-in "announce" event for business skills, or a notification type whose
  spoken text the skill supplies.

### FL-202: The Proactive Events reference does not say which endpoint serves an India account

- Date: 2026-10-10
- Service / tool: Alexa Proactive Events REST API reference
- Severity: Low
- Task attempted: Send notifications to an Alexa account registered in India, from a
  backend in Sydney.
- Steps taken: Read the API reference's endpoint list.
- Expected: Which countries each of the North America, Europe and Far East endpoints
  serves.
- Actual: The three development and production URLs are listed, with no mapping to
  countries or marketplaces. The mapping is on other pages (the smart home "Send Events"
  guide puts English and Hindi (India) on the Europe endpoint, `api.eu.amazonalexa.com`),
  which a review found after this entry was first written.
- Workaround: Every request Alexa sends carries `context.System.apiEndpoint`; the skill
  records it with the linked person, and each notification goes to that endpoint, so no
  table is needed.
- Suggestion: The same country-to-endpoint table on the Proactive Events reference.

### FL-203: The Alexa+ MCP path is in Preview with requirements a voice agent struggles to meet

- Date: 2026-10-10
- Service / tool: Alexa+ MCP Toolkit (Preview)
- Severity: Low (not adopted)
- Task attempted: Connect the CrewOps MCP server, already a Streamable HTTP server, to
  Alexa+ directly.
- Steps taken: Read the Alexa+ MCP QuickStart and the July 2026 announcement.
- Expected: Bring an existing MCP server as it is.
- Actual: The integration is in Preview; it needs OAuth 2.1 with PKCE and a protected-
  resource metadata document (CrewOps' server accepts Cognito access tokens through
  AgentCore's JWT authorizer), and round trips under 500 ms, which a question answered with
  a model call does not meet. Alexa+ is also not offered in India.
- Workaround: A classic custom skill in front of the same pipeline, with Cognito account
  linking; the MCP server stays as it is for MCP clients.
- Suggestion: State the latency limit per tool type (deterministic lookups versus model
  calls), and accept an authorizer-protected server.

### FL-217: The new developer console has no Alexa Notifications permission switch

- Date: 2026-10-12
- Service / tool: Alexa developer console (Build > Permissions), ASK CLI / SMAPI
- Severity: Medium (about 20 minutes; needed a second tool)
- Task attempted: Turn on Alexa Notifications for the skill, so it may send Proactive Events.
- Steps taken: Opened Build > Permissions, scrolled the whole page: device address, customer
  name, email, phone, lists, reminders, location, skills personalisation, PIN, timers, and
  the Alexa Skill Messaging credentials.
- Expected: An "Alexa Notifications" switch, as the Proactive Events guide describes.
- Actual: No such switch. The permission (`alexa::devices:all:notifications:write`) and the
  event publications can only be set in the skill manifest, which the console does not
  edit; it took the ASK CLI (`ask smapi update-skill-manifest`) to turn them on.
- Workaround: Upload the manifest with the ASK CLI; the console then shows notifications on.
- Suggestion: Put the notifications switch (and the event publications) back on the
  Permissions page, or link from it to the manifest route.

### FL-218: The ASK CLI sign-in demanded an email the account did not have

- Date: 2026-10-12
- Service / tool: ASK CLI (`ask configure`), Login with Amazon, Amazon developer accounts
- Severity: Medium (about 30 minutes)
- Task attempted: Sign the ASK CLI in to the developer account that owns the skill.
- Steps taken: `ask configure` opened Amazon's sign-in; the developer account had been
  created with a mobile number only. Earlier, signing in to the developer console with an
  email showed only "There was a problem", with no reason.
- Expected: The same sign-in the developer console accepts.
- Actual: The CLI's sign-in page asked for an email address to be added before going on,
  and refused the person's email as "already in use" (it belongs to another Amazon
  account). The console's generic "There was a problem" gave no hint that the email simply
  was not on that account.
- Workaround: Added a new address (a mail alias) to the mobile-number account, then signed
  in with the number.
- Suggestion: Let the CLI sign in with a phone-only developer account, and make the sign-in
  error say "no account with this email" when that is the cause.

### FL-219: The console simulator spun on a request the skill never received

- Date: 2026-10-12
- Service / tool: Alexa developer console (Test tab), SMAPI `simulate-skill`, CloudWatch Logs
- Severity: Low (about 15 minutes)
- Task attempted: Test "open crew ops" before linking an account.
- Steps taken: Typed it into the Test tab's simulator with testing enabled in Development.
- Expected: The skill's reply, and the request and response under Skill I/O.
- Actual: The reply bubble kept spinning and Skill I/O stayed empty; the skill's logs
  showed no request at that time. The same utterance through `ask smapi simulate-skill`
  reached the endpoint at once and returned the right reply (4.3 s, a cold start).
- Workaround: Test with `simulate-skill` (and `get-skill-simulation`); reload the console.
- Suggestion: Time out in the simulator with a message, and say when a request was never
  sent to the endpoint.

### FL-220: Account linking needs values that exist only after deploying with its own

- Date: 2026-10-12
- Service / tool: Alexa developer console (Account Linking), Amazon Cognito, AWS CDK
- Severity: Low (a few minutes, but confusing)
- Task attempted: Link the skill to the app's Cognito sign-in.
- Steps taken: Opened Account Linking to note Alexa's redirect URLs; saved the page.
- Expected: Either order to work: create the authorization server first, or the skill first.
- Actual: The Cognito client needs Alexa's redirect URLs, which appear only on that page;
  the page's form then refuses to save without the authorization URI, token URI, client id
  and secret, which exist only once that client is deployed. Saving early shows four
  "must not be empty" errors with nothing saying the redirect URLs are already usable.
- Workaround: Copy the redirect URLs without saving, deploy the client, then come back.
- Suggestion: Show the redirect URLs before the form (or on the skill's overview), and
  allow saving the switch without the provider details.

### FL-221: The Vega project generator wrote into the repository root and replaced its .gitignore

- Date: 2026-10-13
- Service / tool: Vega CLI 1.4.4 (`vega project generate`), Vega SDK 0.24 (Fire TV)
- Severity: High (could have committed the team's `.env`; caught at once, nothing lost)
- Task attempted: Create a Fire TV (Vega OS) app inside the CrewOps repository.
- Steps taken: From the repository root, `vega project generate --template helloWorld --name
  CrewOpsTV --packageId com.crewops.tv`, with `--outputDir` left at its default (the current
  directory), expecting a `CrewOpsTV/` folder.
- Expected: A new folder named after the project, as `create-react-app`-style generators
  make, or a refusal to write into a non-empty directory.
- Actual: The files went straight into the repository root: `package.json`, `app.json`,
  `manifest.toml`, `tsconfig.json`, `index.js`, configs, `test/`, and `App.tsx`, `assets/`,
  `components/` and `data/` inside the Python `src/` folder. The repository's `.gitignore`
  was silently replaced by the template's, so `.env` and build folders were no longer
  ignored. No prompt and no warning.
- Workaround: Listed the files by their write time, moved them into `tv/`, restored
  `.gitignore` from git, and generate into an empty folder from now on.
- Suggestion: Create a folder named after the project unless told otherwise, and refuse
  (or ask) before writing into a non-empty directory or over an existing file.

## 2. AWS backend infrastructure (accounts, CDK, Cognito, DynamoDB, SQS, SNS, AppSync, Lambda, SDK)

### FL-001: Project account denies CDK outside its home region, with no hint why

- Date: 2026-10-06
- Service / tool: AWS projects (new account experience), AWS Organizations service control
  policies, AWS CDK
- Severity: Blocker (about an hour lost)
- Task attempted: First deployment of the CDK app (one DynamoDB table) from a new IAM user
  with the AdministratorAccess policy, to us-east-1, the region most AWS guides default to.
- Steps taken:
  1. Created IAM user `crewops-deployer` in the project and attached AdministratorAccess.
  2. Configured an AWS CLI profile with its access key; `aws sts get-caller-identity`
     returned the expected user.
  3. Ran `cdk bootstrap`, then `cdk diff`, then `cdk deploy --all` in us-east-1.
- Expected: Bootstrap creates the CDKToolkit stack and the deploy creates the table. If a
  region is not allowed, the error should say so.
- Actual: Every step failed with an explicit deny from a service control policy the user
  never created:
  `User: arn:aws:iam::<project-account>:user/crewops-deployer is not authorized to perform:
  cloudformation:DescribeStacks on resource: arn:aws:cloudformation:us-east-1:<project-account>:stack/CDKToolkit/*
  with an explicit deny in a service control policy: arn:aws:organizations::<management-account>:policy/<org-id>/service_control_policy/<policy-id>`
  The deploy also failed on `ssm:GetParameter` for `/cdk-bootstrap/hnb659fds/version`.
  Diagnosis took several steps:
  1. `aws organizations describe-organization` showed the project is a member account of
     an organization owned by the user's own sign-up account.
  2. Signing in again always opened the project, and the console never mentioned a home
     region or any region restriction.
  3. The AWS documentation page "Managed policies for your organization" explains that
     projects deny nearly all actions outside the project's selected region.
  4. Read-only calls confirmed it: CloudFormation and DynamoDB worked in ap-southeast-2 and
     were denied in us-east-1 and us-west-2, so the project's home region is
     ap-southeast-2 (Sydney).
- Workaround: Deploy to the project's home region. Set `AWS_REGION=ap-southeast-2`, and use
  Bedrock model IDs that work there (Nova Micro in-region, Nova 2 Lite through the `global.`
  profile). The alternative, "Activate advanced features", is irreversible, so it was not
  used. Bootstrap and deploy then succeeded in ap-southeast-2.
- Suggestion for Amazon:
  1. Show the project's home region prominently in the console and in AWS Settings, with a
     note that other regions are blocked.
  2. Make the access-denied message name the cause in plain words, such as "this project
     only allows ap-southeast-2", instead of a raw policy ARN.
  3. Have `cdk bootstrap` detect a region-restricting policy and suggest the allowed region.

### FL-002: `cdk deploy` waits forever on a dropped connection, with no output

- Date: 2026-10-06
- Service / tool: AWS CDK CLI (`npx aws-cdk@2 deploy`)
- Severity: High (about 30 minutes lost, deploy moved to another machine)
- Task attempted: Deploy the data stack right after a successful bootstrap.
- Steps taken:
  1. Ran `cdk deploy --all`. Output stopped after
     `success: Published CrewOps-dev-Data Template`.
  2. Waited several minutes with no further output and no prompt.
  3. Checked CloudFormation: no stack and no change set existed.
  4. Checked CloudTrail: the last API call was `DescribeStacks`; `CreateChangeSet` was never
     sent.
  5. Checked the process: its one HTTPS socket to an AWS endpoint was stuck in
     `FIN-WAIT-1`, retransmitting.
  6. Retried twice on the same network with the same result.
- Expected: A network failure produces a timeout and a retry or an error within a minute or
  so, and the CLI says what it is waiting for.
- Actual: The CLI waited indefinitely with no message. It looked identical to a hidden
  confirmation prompt, which cost time.
- Workaround: Ran the same deploy from another machine on a different network; it finished
  in under two minutes.
- Suggestion for Amazon: Apply request timeouts with retries to every SDK call the CLI
  makes.
  After 30 seconds without a response, print the operation being waited on, for example
  "waiting for CloudFormation CreateChangeSet". Add a `--timeout` option.

### FL-003: `npx aws-cdk@2` silently picks an old global CLI that cannot read the app

- Date: 2026-10-06
- Service / tool: AWS CDK CLI and aws-cdk-lib
- Severity: Medium (about 20 minutes lost)
- Task attempted: Deploy on a second machine with `npx --yes aws-cdk@2 deploy --all`.
- Steps taken:
  1. Ran the deploy and got:
     `This CDK CLI is not compatible with the CDK library used by your application. Please
     upgrade the CLI to the latest version. (Cloud assembly schema version mismatch: Maximum
     schema version supported is 45.x.x, but found 54.0.0. You need at least CLI version
     2.1144.0 to read this manifest.)`
  2. Retried; same error.
  3. `npm ls -g aws-cdk` showed an old global install, 2.1020.2, which `npx aws-cdk@2`
     reused because it satisfies the `@2` range.
- Expected: The error names the CLI version and path actually used, so the stale install is
  obvious.
- Actual: The message says to upgrade but not which binary ran, and `@2` looks like it
  should fetch the current major version.
- Workaround: `npm uninstall -g aws-cdk`, then `npx --yes aws-cdk@2.1144.0 deploy --all`.
  The project's deploy script now calls `aws-cdk@latest`.
- Suggestion for Amazon: Print the running CLI's version and install path in the mismatch
  error, and document that `npx aws-cdk@2` prefers any installed 2.x over the latest
  release.

### FL-004: boto3 table resource's client silently re-serializes transaction items

- Date: 2026-10-06
- Service / tool: AWS SDK for Python (boto3), Amazon DynamoDB TransactWriteItems
- Severity: Low (caught in testing, about 30 minutes)
- Task attempted: Write a report and its audit event in one DynamoDB transaction, using the
  client from a `Table` resource (`table.meta.client`).
- Steps taken:
  1. Built the items in DynamoDB's typed wire format (`{"S": ...}`) with `TypeSerializer`,
     as the low-level client documentation shows.
  2. Called `table.meta.client.transact_write_items(...)`.
- Expected: Either the wire-format items are accepted, or the error says the client expects
  plain Python values.
- Actual: The call failed with `TransactionCanceledException` whose cancellation reasons
  were
  only `[TypeError, TypeError]`. The resource's client applies boto3's automatic type
  conversion, so the already-typed items were converted a second time. Nothing in the error
  points to that.
- Workaround: Pass plain Python values to `table.meta.client` and let it convert them.
- Suggestion for Amazon: Document clearly that a resource's `meta.client` uses the
  high-level, auto-converting interface. Have it detect already-typed values (a dict whose
  only key is a DynamoDB type tag) and raise an error that says so.

### FL-008: A second queue policy silently replaces the one `enforce_ssl` created

- Date: 2026-10-07
- Service / tool: AWS CDK (`aws-cdk-lib` 2.272), Amazon SQS queue policies
- Severity: Medium (found in review before deployment, about an hour)
- Task attempted: Let only the MCP server's role send reports to the ingest queue, with a
  Deny statement added from the MCP stack.
- Steps taken:
  1. The ingestion stack creates the queue with `enforce_ssl=True`, which makes CDK add an
     `AWS::SQS::QueuePolicy` that refuses non-TLS requests.
  2. The MCP stack created a second `sqs.QueuePolicy` for the same queue with the Deny.
  3. Ran `cdk synth`: no warning, and both stacks synthesized.
- Expected: A synth-time warning that the queue already has a policy, because a queue has
  exactly one policy document.
- Actual: Two `AWS::SQS::QueuePolicy` resources for one queue. Deploying both would leave
  whichever was written last, silently dropping either the TLS rule or the sender rule.
- Workaround: Moved the Deny into the ingestion stack with `queue.add_to_resource_policy`,
  so both statements share one policy, and gave the AgentCore runtime a fixed-name
  execution role so that policy can name it without a cross-stack reference.
- Suggestion: Warn at synth when two `QueuePolicy` (or `TopicPolicy`, `BucketPolicy`)
  resources target the same resource, and point to `add_to_resource_policy`.

### FL-014: AppSync Events handler docs omit what `ctx.identity` holds

- Date: 2026-10-08
- Service / tool: AWS AppSync Events, channel namespace handlers (APPSYNC_JS)
- Severity: Low (about 20 minutes)
- Task attempted: Let only staff roles subscribe to the live console channel, by checking
  the subscriber's Cognito groups in an `onSubscribe` handler.
- Steps taken:
  1. Read the Event API pages on writing handlers, the handler overview and authorization.
  2. None defines the `ctx` object or the fields of `ctx.identity` for Cognito user pools.
  3. Found `ctx.identity.groups` and `ctx.identity.username` only in examples elsewhere.
- Expected: A context reference for Events handlers, as AppSync GraphQL has for resolvers.
- Actual: The handler pages show `util.unauthorized()` with a placeholder check
  (`somethingNotValid()`), and no page lists the identity fields.
- Workaround: Used `ctx.identity.groups`, refusing when it is missing (the safe direction),
  and test the handler under Node with stand-in identities.
- Suggestion: Add a `ctx` reference to the AppSync Events developer guide, with the
  identity shape for each authorization type.

### FL-015: An AWS call from Windows failed only after a long wait, with no cause

- Date: 2026-10-07
- Service / tool: AWS SDK for Python (botocore) on Windows, Amazon DynamoDB
- Severity: Medium (live testing deferred to another day)
- Task attempted: Ask a live question from the Windows laptop:
  `uv run python -m crewops.agent.ask --live "Which single flight leg has the most seats at
  risk if cancelled, and why?"`.
- Steps taken:
  1. Ran the command; it waited a long time, then failed.
  2. Checked for proxy settings (`Get-ChildItem env: | Where-Object Name -match 'proxy'`
     returned nothing).
  3. Retried; one attempt answered, but late. A diagnostic script then hung in a TLS read.
- Expected: A quick, specific error (DNS, TLS handshake or read timeout).
- Actual: `AWS error: Could not connect to the endpoint URL:
  "https://dynamodb.ap-southeast-2.amazonaws.com/"` after the wait. The message is the same
  whatever failed, so a stalled Wi-Fi connection looked like a wrong endpoint.
- Workaround: Waited for a better connection and continued later.
- Suggestion: Say which step failed (name resolution, connect, TLS or read) and after how
  long, and expose a single "fail fast" setting for interactive commands.

### FL-023: CDK's default Cognito app client turns on OAuth with a placeholder callback

- Date: 2026-10-07
- Service / tool: AWS CDK `cognito.UserPoolClient`, Amazon Cognito
- Severity: Medium (caught by a synth test)
- Task attempted: Create an app client for username and password sign-in only.
- Steps taken: Added the client with `auth_flows` and no OAuth settings; ran the stack test.
- Expected: No OAuth flows unless asked for.
- Actual: `Template has 1 resources with type AWS::Cognito::UserPoolClient, but none match
  as expected`; the client had `"AllowedOAuthFlows": ["implicit", "code"]`,
  `"AllowedOAuthFlowsUserPoolClient": true` and `"CallbackURLs": ["https://example.com"]`.
- Workaround: `disable_o_auth=True`.
- Suggestion: Leave OAuth off unless `o_auth` is configured, or warn when the callback is
  the placeholder.

### FL-024: An empty `WriteAttributes` lets users write every standard attribute

- Date: 2026-10-07
- Service / tool: Amazon Cognito app clients, AWS CDK
- Severity: Medium (found in review)
- Task attempted: Stop users editing their own Cognito attributes, so no email or phone can
  be stored.
- Steps taken: Left the client's write attributes unset; the template had
  `"WriteAttributes": []`.
- Expected: An empty list means nothing is writable.
- Actual: Cognito treats a client with no write attributes as able to write all standard
  attributes, so "no email or phone is stored" would not hold.
- Workaround: `WriteAttributes = ["locale"]`, pinned by a test.
- Suggestion: Make an explicit empty list mean "none", or reject it with a message.

### FL-025: Cognito pool names are not unique, so lookup by name can pick a stale pool

- Date: 2026-10-07
- Service / tool: Amazon Cognito user pools
- Severity: Low (found in review)
- Task attempted: Find the team's pool from the provisioning command.
- Steps taken: Listed pools and took the first with the expected name.
- Expected: One pool per name.
- Actual: Names are not unique, and retained stages leave old pools behind, so the command
  could provision users into the wrong pool.
- Workaround: Read the pool id from the auth stack's output; refuse with both ids when a
  name is ambiguous.
- Suggestion: Offer unique pool names, or a tag-based lookup in the CLI.

### FL-026: A password Cognito refuses left accounts stuck mid-creation

- Date: 2026-10-07
- Service / tool: Amazon Cognito `admin_create_user` / `admin_set_user_password`
- Severity: High (found in review)
- Task attempted: Create the demo team's accounts with a shared password.
- Steps taken: Checked passwords locally with "any non-alphanumeric" as a symbol, then
  created each user and set the password.
- Expected: A password the local check accepts is accepted by Cognito, or nothing is
  created.
- Actual: `'Abcdefghijk1£'`, `'Abcdefghijk1é'` and `' Abcdefghijk1'` passed locally;
  `admin_create_user` succeeded and `admin_set_user_password` failed, leaving the user in
  FORCE_CHANGE_PASSWORD with a temporary password nobody had (messages are suppressed).
  Reruns never repaired it.
- Workaround: Cognito's own symbol set, ASCII only, no leading or trailing space, 12-256
  characters; the command now sets a password for users stuck in FORCE_CHANGE_PASSWORD or
  RESET_REQUIRED and re-enables disabled members.
- Suggestion: Validate the password before creating the user, or create both in one call.

### FL-027: One SNS email subscription per topic means one address cannot take two filters

- Date: 2026-10-08
- Service / tool: Amazon SNS email subscriptions with filter policies
- Severity: Medium (found in review from known SNS behaviour; not reproduced live)
- Task attempted: Subscribe one address to two roles' alerts with different filter policies.
- Steps taken: Subscribed the same email twice with `FilterPolicy` `{"role":
  ["controller"]}` and `{"role": ["approver"]}`.
- Expected: Two subscriptions, or one with both roles.
- Actual: The second Subscribe is refused ("Subscription already exists with different
  attributes"); it reached the admin as INTERNAL, after a daily slot had been counted.
- Workaround: One role per address, refused clearly; the daily slot is given back when SNS
  refuses.
- Suggestion: Say in the Subscribe documentation that an endpoint holds one subscription per
  topic.

### FL-028: SNS subscription actions and unconfirmed subscriptions

- Date: 2026-10-08
- Service / tool: Amazon SNS, IAM
- Severity: Medium (found in review; not reproduced live)
- Task attempted: Let the server list and remove alert subscriptions with least privilege.
- Steps taken: Granted `sns:Unsubscribe` and `sns:GetSubscriptionAttributes` on `<topic>:*`
  only.
- Expected: Subscription actions are authorised on the subscription's ARN.
- Actual: SNS may authorise these against the topic ARN, which `:*` does not match, so
  listing and removing could fail AccessDenied; unsubscribing a pending subscription may
  return InvalidParameter.
- Workaround: Granted on both the topic and `<topic>:*`; InvalidParameter on remove reports
  that the address must confirm first and keeps its record.
- Suggestion: Document which ARN each subscription action is authorised against, and how to
  cancel a pending subscription.

### FL-029: `grant_read_write_data` grants far more than a worker needs

- Date: 2026-10-06
- Service / tool: AWS CDK DynamoDB grants
- Severity: Low (found in review)
- Task attempted: Give the report worker Lambda only what it uses: GetItem, Query, PutItem.
- Steps taken: Used `table.grant_read_write_data(worker)`.
- Expected: A grant close to the actions used.
- Actual: The policy also allowed Scan, DeleteItem, UpdateItem and BatchWriteItem.
- Workaround: `table.grant(worker, "dynamodb:GetItem", "dynamodb:Query",
  "dynamodb:PutItem")`, pinned by a test.
- Suggestion: List the actions each convenience grant includes in its docstring.

### FL-116: AppSync Events docs say "ID token" but show an access token

- Date: 2026-10-08
- Service / tool: AWS AppSync Events, Amazon Cognito user pools (WebSocket protocol docs)
- Severity: Low (about 20 minutes; not yet confirmed live)
- Task attempted: Connect the web console to the live-events channel with the signed-in
  user's Cognito token. The rest of the app uses access tokens.
- Steps taken: Read "Understanding the Event API WebSocket protocol", section "Amazon Cognito
  user pools and OpenID Connect (OIDC) subprotocol format".
- Expected: One clear statement of which Cognito token the API accepts.
- Actual: The text says `"Authorization": <string>: A JWT ID token.`, but the example token
  under it decodes to `"token_use":"access"`, an access token.
- Workaround: The web app keeps both tokens in httpOnly cookies and gives the live-events
  socket the ID token, as the text says; the first live run will confirm it.
- Suggestion: Say whether ID tokens, access tokens or both are accepted, and make the
  example match.

### FL-191: Every sick report failed in AWS while every test passed

- Date: 2026-10-09
- Service / tool: AWS IAM with DynamoDB transactions; moto in the tests
- Severity: High (every crew-out report since the plans feature; found in a live run)
- Task attempted: File a sick report as a crew member and as a reporter, and see it reach
  a controller's desk.
- Steps taken: Both were accepted and queued, then never appeared. The report worker's
  log showed, on every retry: `AccessDeniedException ... not authorized to perform:
  dynamodb:UpdateItem on resource: .../table/crewops-dev-ops because no identity-based
  policy allows the dynamodb:UpdateItem action`, and the three messages went to the
  dead-letter queue.
- Expected: The worker's narrow grant (FL-029) to fail a test when the write changed.
- Actual: Storing a report had since gained an update in its transaction (moving the
  roster version of each crew member reported out), but moto does not enforce IAM, so
  every offline test passed. Gate, delay and station reports bump nothing and kept working.
- Workaround: The worker may also UpdateItem. The infra test now reads the actions off the
  transaction `store_report` really sends and fails if the grant lacks any of them.
- Suggestion: A way to run moto (or a local DynamoDB) against a CDK-synthesised role
  policy, so a least-privilege grant is tested against the calls it must allow.

### FL-194: `cdk deploy` from the web folder failed with "--app is required"

- Date: 2026-10-09
- Service / tool: AWS CDK CLI (`npx aws-cdk@2.1144.0`), Windows PowerShell
- Severity: Low (a few minutes)
- Task attempted: Deploy the four changed stacks from the Windows laptop.
- Steps taken: Packaged the MCP server, then ran `npx --yes aws-cdk@2.1144.0 deploy
  CrewOps-dev-Ingestion CrewOps-dev-Analysis CrewOps-dev-Notify CrewOps-dev-Mcp --profile
  crewops` in the same terminal, which was still in `web\`.
- Expected: The deploy, or a hint that this folder holds no CDK app.
- Actual: `--app is required either in command-line, in cdk.json or in ~/.cdk.json`, with
  no mention of the folder it looked in.
- Workaround: Run it from the repository root, where `cdk.json` is; every deploy step now
  says "from the repo root (not `web\`)".
- Suggestion: Name the directory searched for `cdk.json`, or look in parent directories.

### FL-195: A Lambda's logs were not in `/aws/lambda/<function>`

- Date: 2026-10-09
- Service / tool: AWS Lambda logging configuration, CloudWatch Logs, AWS CLI
- Severity: Low (minutes, while chasing FL-191)
- Task attempted: Read the report worker's errors.
- Steps taken: `aws logs filter-log-events --log-group-name
  /aws/lambda/CrewOps-dev-Ingestion-ReportWorker...`, the name most guides give.
- Expected: The function's log lines.
- Actual: `ResourceNotFoundException: The specified log group does not exist.` The stacks
  give each function its own log group (with a retention), which the function's logging
  configuration points at instead.
- Workaround: Read the group from `aws lambda get-function-configuration ... --query
  LoggingConfig.LogGroup`, then filter that group.
- Suggestion: Have the CLI error say when a function logs to a custom group.

### FL-204: A third reader on the table's stream would have risked throttling

- Date: 2026-10-10
- Service / tool: Amazon DynamoDB Streams with AWS Lambda event sources
- Severity: Low (caught in design)
- Task attempted: Send Alexa notifications from a Lambda on the table's change stream.
- Steps taken: Planned a new Lambda event source on the stream, next to the analysis and the
  notifier.
- Expected: Any number of consumers.
- Actual: DynamoDB Streams guidance is no more than two processes reading a shard at once,
  or reads are throttled; the stream already has two Lambda readers.
- Workaround: The existing notifier sends the Alexa notifications too, best effort and
  after the console and email alerts, so the stream keeps two readers.
- Suggestion: Warn in the CDK construct when a third event source is added to one stream.

### FL-213: The Lambda Python runtime has no crypto library for web push

- Date: 2026-10-11
- Service / tool: AWS Lambda (Python 3.12 runtime)
- Severity: Low
- Task attempted: Send desktop alerts (web push) from the notifier Lambda, which runs the
  plain source with the runtime's boto3.
- Steps taken: Checked what the runtime ships for the ECDH, HKDF and AES-GCM that web push
  encryption and its signed (VAPID) header need.
- Expected: Some standard cryptography available, as boto3 is.
- Actual: Only boto3 and botocore ship; there is no cryptography library, and the Python
  standard library has no elliptic curves or AES.
- Workaround: When the MCP server's package (ARM64 wheels, cryptography included) is built,
  the notifier runs it; without it, the notifier runs the plain source and desktop alerts
  stay off. No layer to maintain.
- Suggestion: A managed Lambda layer with the cryptography library, or a KMS operation for
  web push signing and encryption.

## 3. Core software stack and local tooling (Python, uv, Node, test tooling)

### FL-010: Every CDK synth and test run prints a Node end-of-life banner

- Date: 2026-10-07
- Service / tool: AWS CDK for Python (jsii runtime), Node.js 20
- Severity: Low
- Task attempted: Run the offline test suite, which synthesizes every CDK stack, and
  inspect CDK classes from Python.
- Steps taken: Ran `uv run pytest` and `python -c "from aws_cdk import ..."`.
- Expected: Quiet output, or a single warning.
- Actual: A 14-line banner on every process that loads `aws_cdk`, starting
  `Node 20 has reached end-of-life on 2026-04-30 and will no longer be supported in new
  releases after 2026-10-29.`, mixed into test and script output.
- Workaround: `JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION=1` in the test command. Upgrade
  Node before 2026-10-29.
- Suggestion: Print the banner once per day per machine, or only from the `cdk` CLI, not
  from every Python process.

### FL-030: moto sent an unimplemented S3 Vectors call to real AWS

- Date: 2026-10-07
- Service / tool: moto (AWS mock library), Amazon S3 Vectors
- Severity: Medium (a test could reach a real account)
- Task attempted: Test vector search offline against moto.
- Steps taken: Stored vectors with moto, then queried them.
- Expected: An unimplemented operation raises NotImplementedError locally.
- Actual: Five tests failed with `botocore.errorfactory.AccessDeniedException: An error
  occurred (AccessDeniedException) when calling the QueryVectors operation: The security
  token included in the request is invalid.`: moto stores vectors but passed QueryVectors
  through to the real endpoint with the fake credentials.
- Workaround: An in-memory S3 Vectors fake, and the whole suite now runs with
  `HTTPS_PROXY=http://127.0.0.1:9`, so any network call fails.
- Suggestion: Fail closed on unimplemented operations by default.

### FL-031: moto's Cognito backend needs an undeclared optional package

- Date: 2026-10-07
- Service / tool: moto `cognitoidp`
- Severity: Low
- Task attempted: Test the account provisioning against moto's Cognito.
- Steps taken: Imported moto's Cognito backend.
- Expected: It imports with the `moto` extras installed.
- Actual: `ModuleNotFoundError: No module named 'joserfc'`.
- Workaround: An in-memory Cognito fake in the auth tests.
- Suggestion: List `joserfc` in moto's `cognitoidp` extra.

### FL-032: The server's JWT library arrived only as a transitive dependency

- Date: 2026-10-07
- Service / tool: uv dependency groups, PyJWT
- Severity: Low (found in review)
- Task attempted: Verify Cognito tokens in the MCP server with PyJWT.
- Steps taken: Imported `jwt`, which was installed because the `mcp` package depends on it.
- Expected: Direct imports are declared.
- Actual: `pyjwt[crypto]` and `cryptography` were not declared, so an `mcp` release dropping
  them would break token checks.
- Workaround: `pyjwt[crypto]>=2.10` declared in the server group and locked.
- Suggestion: n/a (our dependency hygiene).

### FL-117: Next.js 16 route types exist only after a build or `next typegen`

- Date: 2026-10-08
- Service / tool: Next.js 16.4, TypeScript
- Severity: Low (about 10 minutes)
- Task attempted: Type-check the new web app with `tsc --noEmit`, using the `LayoutProps`
  and `RouteContext` types the scaffold itself uses.
- Steps taken: Ran `npx tsc --noEmit` on a fresh checkout.
- Expected: The scaffold's own types resolve.
- Actual: `app/layout.tsx(11,50): error TS2304: Cannot find name 'LayoutProps'.` and
  `app/api/tools/[name]/route.ts(10,51): error TS2304: Cannot find name 'RouteContext'.`
  They are generated globals, written by `next build` or `next typegen`.
- Workaround: `npm run typecheck` runs `next typegen && tsc --noEmit`.
- Suggestion: Have the scaffold's `typecheck` script (or a postinstall step) generate the
  types, or say so in the generated README.

### FL-118: A fresh Next.js scaffold reports five high-severity advisories

- Date: 2026-10-08
- Service / tool: create-next-app 16.4.0, eslint-config-next 16.4.0, npm audit
- Severity: Low (development tooling only)
- Task attempted: Install the web app's dependencies.
- Steps taken: Scaffolded with `create-next-app`, then `npm install`.
- Expected: A clean audit for a brand-new project.
- Actual: `5 high severity vulnerabilities`, all through `micromatch` -> `fast-glob` ->
  `@next/eslint-plugin-next` -> `eslint-config-next`; `npm audit fix --force` would make
  breaking changes.
- Workaround: Left as is: the packages are lint tooling and never ship in the app.
- Suggestion: Release eslint-config-next with patched dependencies, or note in the audit
  output that only development tooling is affected.

### FL-136: A fresh Next.js app reports errors for client-rendered pages

- Date: 2026-10-08
- Service / tool: Next.js 16.4 (create-next-app defaults: Cache Components and partial
  prefetching on)
- Severity: Medium (looked like the app was broken during the first live walkthrough)
- Task attempted: Open the console and a disruption's full record in `npm run dev`.
- Steps taken: Navigated between pages; the pages are client components that load data from
  the app's own API routes.
- Expected: No error for pages that render on the client by design.
- Actual: The dev overlay and browser console showed `Error: Route "/console/[id]": Could
  not validate that a segment in your UI has instant navigation. This segment was dropped
  from rendering.` for every page, with a "1 Insight" badge.
- Workaround: Turned off `cacheComponents` and `partialPrefetching`, which the scaffold
  enables but these pages do not use.
- Suggestion: Do not report client-only pages as dropped segments, or say in the scaffold
  which kinds of app need these flags.

### FL-197: Sign-in spun forever on a network with broken IPv6

- Date: 2026-10-10
- Service / tool: Node.js 20 DNS and the AWS SDK for JavaScript v3, in the Next.js server
- Severity: Medium (sign-in impossible on that network; about 30 minutes)
- Task attempted: Sign in to the local web app as divya.rao.
- Steps taken: Entered the username and password; the button spun with no error. The MCP
  server's log showed no request at all. From the same laptop, `curl -6` to the Cognito
  endpoint never connected, `curl -4` answered in 1.2 s, and `curl -4` to Google in 0.4 s.
- Expected: Sign-in, or an error.
- Actual: The Wi-Fi network gave out IPv6 addresses but did not route IPv6. AWS endpoints
  resolve to IPv6 first, so the server's call to Cognito waited on a connection that never
  opened, and the call had no timeout. `npx prettier` hung the same way.
- Workaround: The web server prefers IPv4 addresses (`dns.setDefaultResultOrder
  ("ipv4first")` at start-up), and the Cognito calls have a 5 s connection and 15 s request
  timeout, so a network fault shows "Sign-in is unavailable right now".
- Suggestion: A default request timeout in the SDK, or a log line when a connection is
  still pending after several seconds.

### FL-205: The Next.js dev server kept serving an old stylesheet

- Date: 2026-10-10
- Service / tool: Next.js 16 dev server (Turbopack), Tailwind CSS 4
- Severity: Low
- Task attempted: Check the desk speaker's light ring in the browser.
- Steps taken: Appended the ring's CSS to `app/globals.css` while the dev server ran, then
  opened `/speaker`.
- Expected: The new rules on the next load.
- Actual: The page had the elements but no ring; the served stylesheet held the earlier
  rules and none of the new ones, while a production build included them.
- Workaround: Restart `npm run dev` after editing `globals.css`. On 2026-10-11 a restart was
  not enough: Turbopack's on-disk cache (`.next/dev`) served the old stylesheet again, until
  that folder was deleted and the server started afresh.
- Suggestion: Rebuild the global stylesheet when it changes, or say in the dev log that it
  was not; never reuse a cached stylesheet whose source has changed since.

## 4. Our own code (found by tests, reviews, reference data and live runs)

#### Legality engine and disruption impact

### FL-016: Station-closure delays came out 30 minutes short of the reference

- Date: 2026-10-07
- Severity: Medium (about an hour)
- Task attempted: Work out which flights a station closure delays, and by how much, for the
  disruption analysis.
- Steps taken:
  1. First version: every departure or arrival inside the closure moves to the reopening
     time.
  2. Compared each affected trip's new times with reference scenario S3 (BLR closed
     08:00-14:00Z on 17 Sep).
- Expected: All six affected trips match the reference.
- Actual: Every delayed movement was 30 minutes earlier than the reference, for example
  `DX402 on 2026-09-17: duty becomes 16.5h against a 12.0h limit` where the reference has
  17.0h. Shorter delays understate duty times, so the legality verdicts built on them would
  have been too optimistic.
- Workaround: A reopened station needs a 30-minute turnaround before the first movement;
  the engine now delays to reopening plus that turnaround, and all six S3 trips match.
- Suggestion: Write operational buffers like this into the rules data, not only into
  reference answers, so an implementation cannot miss them.

### FL-033: Duty flown on the snapshot day was left out of the hour totals

- Date: 2026-10-06
- Where: legality engine
- Found by: review
- Severity: High
- What went wrong: `assess(w, "C-5837", "P-2203")` reported "30.88h of 60h/7d on
  2026-09-16"; the true figure is 42.13h. All 32 crew rostered on 14 Sep were about 11h low;
  the canary test used C-1042, who is not rostered that day.
- Cause: Rostered duty dated on or before the history end was dropped, but the history held
  0 for that day.
- Fix: Each day counts once, from whichever source has it; a C-5837 canary test was added.

### FL-034: Hour windows ending on later rostered days were never checked

- Date: 2026-10-06
- Where: legality engine
- Found by: review
- Severity: High
- What went wrong: With 11h on each of the next five days plus 10h on the new day, the check
  said PASS "10.0h of 60h/7d", although the window ending five days later holds 65h.
- Cause: Only the window ending on the new duty day was evaluated.
- Fix: Every window the new duty falls into is evaluated; real data then shows C-5837 at
  "45.0h of 60h/7d in the 7 days ending 2026-09-20".

### FL-035: Positioning slipped every day of a multi-day pairing, hiding short rest

- Date: 2026-10-06
- Where: legality engine
- Found by: review
- Severity: High
- What went wrong: `assess(w, "C-2210", "P-2291")` came back legal, but on the real day-2
  schedule rest is 9.5h, below the 12h minimum; 27 positioning options were offered wrongly.
- Cause: The positioning delay was applied to every day, not only day 1.
- Fix: Only day 1 slips; the engine now says "only 9.5h rest between 2026-09-15 and
  2026-09-16 of the pairing". This deliberately changes an older reference answer.

### FL-036: The engine ignored the snapshot time

- Date: 2026-10-06
- Where: legality engine
- Found by: review
- Severity: Medium
- What went wrong: `find_options(w, "P-2201", "Captain")` offered C-1042 for a pairing that
  had already flown, and positioning flights that had already left could be chosen.
- Fix: Started trips and departed positioning flights are refused.

### FL-037: A reserve-window failure showed the wrong reason

- Date: 2026-10-06
- Where: legality engine
- Found by: review
- Severity: Low
- What went wrong: A reserve whose on-call window missed the report time was filed as "in
  position: cannot get to the departure in time".
- Fix: It has its own reason now.

### FL-038: A reference answer offered crew already on the same flight

- Date: 2026-10-06
- Where: reference scenarios
- Found by: comparison with reference data
- Severity: Low
- What went wrong: For S5, the reference answer offers two cabin crew who are already
  rostered on that same flight, which is not possible.
- Fix: The engine excludes them, and a test pins that it does.

### FL-039: The joint plan ignored the delay and contradicted the per-seat answers

- Date: 2026-10-07
- Where: disruption impact
- Found by: review
- Severity: High
- What went wrong: For a 180-minute delay to DX412 on 15 Sep, all six seats said no legal
  cover, yet the joint plan assigned 4 crew for ₹56,000.
- Cause: The joint plan called the option search without the delay.
- Fix: The joint plan reuses each seat's options; that case now assigns nobody and lists six
  unfilled seats.

### FL-040: Identical seats on one pairing collapsed into one

- Date: 2026-10-07
- Where: disruption impact
- Found by: review
- Severity: High
- What went wrong: `joint_plan(w, [Seat("P-2204", "Cabin Crew")] * 3)` returned 1
  assignment, 0 unfilled, ₹9,500; in the DX412 case six seats became four assignments.
- Cause: Options were held in a dict keyed by pairing and role.
- Fix: Seats are handled as a list.

### FL-041: Legs to drop included legs flown before the late flight

- Date: 2026-10-07
- Where: disruption impact
- Found by: review
- Severity: Medium
- What went wrong: For a 900-minute delay to DX404 on 17 Sep, the summary said take
  DX401-DX404 off (₹1,000,000); dropping DX404 alone suffices (₹250,000).
- Fix: Only legs from the delayed flight onward shift, and earlier legs are never dropped.

### FL-042: An aircraft fault of a few seconds crashed the analysis

- Date: 2026-10-07
- Where: disruption impact
- Found by: review
- Severity: Medium
- What went wrong: An aircraft fault from 02:00:00Z to 02:30:10Z raised `ValueError:
  delay_hours must be positive`.
- Cause: A delay of seconds rounded to 0.0.
- Fix: Delays round up to the minute, zero is skipped, and anything not assessable becomes a
  note.

### FL-043: With no disjoint combination, every seat was reported unfilled

- Date: 2026-10-07
- Where: disruption impact
- Found by: review
- Severity: Medium
- What went wrong: Two seats whose only option was the same captain gave no assignment at
  all, and a 12-option cap per seat undercounted equal-cost ties.
- Fix: Falls back to the best partial plan; ties are counted over more options.

### FL-164: Crew of another airline passed a legality check

- Date: 2026-10-09
- Where: legality engine
- Found by: test, while adding the three airlines
- Severity: High
- What went wrong: Asked directly, "is C-2210 legal for P-2291?" answered yes for a Deccan Express
  captain on a Konkan Skyways trip.
- Cause: Only the options search kept to the trip's airline; the legality check itself had no
  airline rule.
- Fix: The qualification rule fails crew of another airline: "works for Deccan Express, not
  Konkan Skyways, which flies VT-DXC".

### FL-165: Falling ill part-way through a day uncovered flights already flown

- Date: 2026-10-09
- Where: disruption analysis
- Found by: the project owner's question
- Severity: Medium
- What went wrong: A crew member who flew the morning leg and fell ill after it was treated as out for
  the whole day, so the leg already flown was counted as uncrewed.
- Cause: An absence could only start on a date, never at a time.
- Fix: A report may give the time it starts on its first day. Flights that left before it
  stay with the crew member, and the analysis says where the cover joins and that
  positioning there is not costed.

### FL-166: Options still offered someone reported sick after they were ranked

- Date: 2026-10-09
- Where: options, console
- Found by: the project owner
- Severity: High
- What went wrong: A reserve reported sick after a disruption's options were worked out stayed selectable
  in that disruption.
- Cause: Options are stored when a disruption is analysed; a later absence only affected
  analyses run after it.
- Fix: Every read flags options whose crew have since been reported unavailable, with the
  report and time. The console strikes them through, and they cannot be chosen.

### FL-167: A seat with no cover of its own rank went straight to cancelling the trip

- Date: 2026-10-09
- Where: options
- Found by: the project owner
- Severity: Medium
- What went wrong: When no captain was legal, the only choice left was cancellation, as it was for a
  missing cabin crew member on a trip that could legally fly one short.
- Cause: Only crew of the seat's own rank were considered.
- Fix: As regulation allows: a command-qualified first officer for a captain's seat, a
  captain current in the right seat for a first officer's (never one pilot), and
  operating one cabin crew short while one per 50 seats is kept. Each comes after the
  seat's own rank, and cancelling is always last.

### FL-200: A clash with another trip was reported as short rest

- Date: 2026-10-10
- Where: legality engine (rest rule)
- Found by: checking the two-trip case the project owner described
- Severity: Low
- What went wrong: Once C-3983 covered P-2291 (13-14 Oct), checking them for P-2217 said
  "only 11.0h rest after P-2291 on 2026-10-13", although P-2217 (02:30-12:15Z on the 14th)
  overlaps P-2291's second day (04:00-14:45Z) outright. The verdict was right; the reason
  hid the decisive fact.
- Cause: The rule walked the crew member's duties in date order and reported the first
  problem, so a short rest after day one came before the clash with day two.
- Fix: Any overlapping trip is named first ("double-booked: P-2291 overlaps on
  2026-10-14"), then rest either side. Tests pin this, and the separate-plans case: a
  choice for one trip is re-judged for the next day's trip, and the second approval goes
  stale with "only 11.75h rest after P-2291".

#### Question pipeline, verifier and models

### FL-018: Follow-up context gave confident wrong answers

- Date: 2026-10-08
- Severity: High (found in review before deployment)
- Task attempted: Let a follow-up borrow from the previous turn: "they" meaning the crew
  member just asked about, and a short reply read with the question it answers.
- Steps taken: The first version carried the last crew member into any question with
  he/she/they/their, and joined any reply of up to four words to an open question back.
- Expected: Follow-ups resolve; new questions are answered on their own.
- Actual (after "How many duty hours does C-1042 have?"): "Who is on reserve at BLR on
  15 Sep and are they rested?" answered "No reserve duties match that." instead of listing
  12. After "Which seat needs cover on P-2291?", the reply "Duty hours C-3310" answered
  "D. Reddy (C-3310) is legal for P-2291: every rule passes.", a question nobody asked.
  Both answers passed verification, because every fact in them was real. A crew member's
  own "How many duty hours do I have before they ground me?", after a refused question
  about C-3310, was read as C-3310 and refused.
- Workaround: Only "he" and "she" carry, and never in questions about the asker, several
  people, a plan's decision or a named role ("her captain"). A reply is joined only when it
  gives just a seat, a date or one id and matches no question rule of its own. The
  reviewer's examples are now regression tests.
- Suggestion: n/a (our own code). Lesson: a verifier proves facts are sourced, not that
  they answer the question asked.

### FL-044: A cut-off or filtered Nova reply was served as a verified answer

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: High
- What went wrong: With `stopReason="max_tokens"` and the text "The cheapest legal cover
  is", the answer had `source="model"` and was marked verified; a `content_filtered` reply
  behaved the same.
- Cause: Any stop reason other than `tool_use` was treated as a finished answer.
- Fix: Only `end_turn` and `stop_sequence` count, for the advisor, explainer and triage;
  anything else falls back to the template.

### FL-045: The voice time budget was not enforced on Bedrock calls

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review (two rounds)
- Severity: Medium
- What went wrong: With a 0.5 s budget and a 1 s fake call, the run took 1.01 s and still
  made the call; each call could run to botocore's default timeout twice. After the first
  fix the advisor could still start a call at 5.9 s that ran until about 16 s.
- Cause: The deadline was checked only after a tool-use reply, and botocore's retries and
  timeouts applied.
- Fix: The deadline is checked before every call, one attempt each, with a 3 s client for
  triage, embeddings and guardrails and the response budget for advisor and explainer.

### FL-046: The ask command hid why a live answer was replaced

- Date: 2026-10-07
- Where: ask CLI
- Found by: live run
- Severity: Low
- What went wrong: A live question returned only a flight list after one model call and 44
  output tokens; the slow network had used the budget, but nothing on screen said so.
- Cause: The command did not print the answer's notes.
- Fix: It prints where the answer came from, its verification and its notes.

### FL-047: No did-you-mean for a misspelt surname, and closure ripple too broad

- Date: 2026-10-07
- Where: tools
- Found by: live smoke run
- Severity: Medium
- What went wrong: `lookup {"entity": "crew", "filters": {"name": "Nayar"}}` returned
  `{"count": 0, "rows": []}`; a station closure marked a whole day uncovered.
- Fix: Returns `NEEDS_CONFIRMATION … Did you mean A. Nair (C-1042, Captain), S. Nair …`;
  only flights in the closure window are direct, later ones at risk.

### FL-048: `same_pairing` swapped in a different person of another rank

- Date: 2026-10-07
- Where: tools
- Found by: review
- Severity: High
- What went wrong: `same_pairing(a="Captain S. Nair", b="C-1042")` answered for C-2295 S.
  Nair, a First Officer.
- Cause: No crew of the stated rank fell back to any crew with the name.
- Fix: Asks which person is meant; several crew sharing a name give AMBIGUOUS_QUERY.

### FL-049: `joint_plan` staffed the same seat twice

- Date: 2026-10-07
- Where: tools
- Found by: review
- Severity: High
- What went wrong: `events=[{"crew_id":"C-1042"},{"crew_id":"C-1042"}]` gave two Captain
  assignments on P-2291 for ₹42,500.
- Fix: Refused: "C-1042 named as out more than once" and "P-2291 has 1 Captain seat, but the
  events ask to cover 2".

### FL-050: `lookup` filters on list fields gave wrong results

- Date: 2026-10-07
- Where: tools
- Found by: review (two rounds)
- Severity: High
- What went wrong: `{"date": {"gte": "2026-09-19"}}` gave 0; `{"ratings": ["A320","ATR72"]}`
  meant "all of" although documented as "any of"; `{"ratings": []}` matched everything;
  `group_by=["crew"]` raised `TypeError: unhashable type: 'dict'`; after the first fix
  `{"ratings": {"gte": 3}}` matched 150 because "A320" >= "3" as text.
- Fix: A range or list on a list column means "any element"; empty lists and ranges, nested
  values and mixed number/text bounds give INVALID_ARGUMENT.

### FL-051: `ripple` over-reported the blast radius

- Date: 2026-10-07
- Where: tools
- Found by: review
- Severity: Medium
- What went wrong: A disruption to DX404 listed DX401-DX404 as directly affected (648
  passengers); a flight that had already landed gave the same 648; an aircraft with no time
  treated all 28 of its flights as disrupted.
- Fix: Only the named flight is direct, departed legs are listed apart, and an aircraft
  without a time asks for one.

### FL-052: Unknown ids were ignored or returned an empty success

- Date: 2026-10-07
- Where: tools
- Found by: review (two rounds)
- Severity: Medium
- What went wrong: `{"flight_no":"DX999"}` and `{"crew_id":["C-9999"]}` returned ok with 0
  rows; `{"crew_id": ["C-1042","C-9999"]}` returned 1 row without mentioning C-9999.
- Fix: Every id, including each one in a list, is resolved or reported.

### FL-053: Some tool arguments were silently ignored

- Date: 2026-10-07
- Where: tools
- Found by: review
- Severity: Low-Medium
- What went wrong: `check_gate(..., date="2026-09-16")` answered at the snapshot time;
  `find_options(pairing_id="P-2291", crew_id="C-1042", role="Cabin Crew")` answered for the
  Captain seat.
- Fix: Each argument is used or refused.

### FL-054: Smaller tool defects

- Date: 2026-10-07
- Where: tools
- Found by: review
- Severity: Low
- What went wrong: A started seat appeared in both `unfilled` and `already_started`; an
  issue-count fallback showed "None"; a crew member with two pairings on one date was
  resolved to one silently; did-you-mean hints named tools the caller cannot use.
- Fix: Each corrected; the ambiguous cases ask.

### FL-055: Entity extraction read a date range from "before" and missed a stated base

- Date: 2026-10-07
- Where: router
- Found by: running extraction over the gold questions
- Severity: Low
- What went wrong: Q20 gave `'date_range': {'lt': '2026-09-16'}` from a "before" not
  attached to a date; "C-2210 (DEL base)" was not read as a stated base.
- Fix: "Before" counts only directly before a date; the bracketed base is recognised.

### FL-056: Router rules routed some gold questions wrongly

- Date: 2026-10-07
- Where: router
- Found by: gold-set routing check
- Severity: Low-Medium
- What went wrong: The word "desk" sent the morning-briefing question to the controller list
  (`Q38 … TIER MISMATCH`); "What is C-2087's rank?" fell to the default route; "Can reserve
  C-3305 cover P-2291?" planned two lookups instead of a legality check.
- Fix: Rules corrected; all 38 gold questions route to their tier.

### FL-057: A refusal read badly, and "my" did not mean the signed-in crew member

- Date: 2026-10-07
- Where: question pipeline
- Found by: live run
- Severity: Low
- What went wrong: `I couldn't duty clock: crew may use duty_clock only about themselves`.
- Fix: "That isn't available to your role."; I, me and my map to the caller's crew id.

### FL-058: Values from a rejected draft leaked into the answer record

- Date: 2026-10-07
- Where: question pipeline
- Found by: failing test
- Severity: Medium (personal data)
- What went wrong: The note "unsourced: 98765, 43210" carried a phone-like number into the
  record.
- Fix: Unsourced values go to the log only; notes give counts and kinds.

### FL-059: A question with nothing to run got the generic scope reply

- Date: 2026-10-07
- Where: question pipeline
- Found by: failing gold test
- Severity: Low
- What went wrong: "Which single flight leg has the most seats at risk…" got "I can help
  with crew control…" instead of saying what it needed.
- Fix: The scope reply is used only for questions no rule recognises.

### FL-060: The premise check leaked other crew members' details to any role

- Date: 2026-10-07
- Where: question pipeline
- Found by: review
- Severity: High
- What went wrong: As crew C-1042, "Is FO C-2087 on P-2291?" answered "C-2087 is a Captain,
  not a First Officer…".
- Cause: The premise check ran before any role check.
- Fix: Crew details are revealed only to roles with `lookup`, or about the caller's own id.

### FL-061: Ordinary capitalised words were read as unknown stations

- Date: 2026-10-07
- Where: router
- Found by: review (three rounds)
- Severity: Medium
- What went wrong: "Which captains are rated on ATR-72?" answered "ATR isn't one of our
  stations…"; FYI, SMS, CEO and GMT did the same, and "ATR 72" dropped the rating filter.
- Fix: ATR-72 forms are recognised; a station needs context or a near miss.

### FL-062: Date parsing errors

- Date: 2026-10-07
- Where: router
- Found by: review (two rounds)
- Severity: Medium
- What went wrong: "between 15 and 17 Sep" looked up only 17 Sep; "Who may 3 captains…"
  became 2026-05-03; "What are my duty hours on Friday?" ignored Friday.
- Fix: Ranges, "may" only in a date context, and weekdays resolved to the week.

### FL-063: Unmatched questions dumped the roster or got no answer

- Date: 2026-10-07
- Where: router
- Found by: review (rounds 2 and 3)
- Severity: Medium
- What went wrong: "How much is a ticket from Delhi to London…" listed 12 DEL crew; "Show
  flights in GMT on 16 Sep from BLR" listed 138 BLR crew; "Who covers DX412 on 16 Sep?"
  answered "I need something to narrow by…".
- Fix: No lookup from incidental words; cover questions ask which seat.

### FL-064: The verifier missed timestamps and several number and name forms

- Date: 2026-10-07
- Where: verifier
- Found by: review (three rounds)
- Severity: High
- What went wrong: `verify("Report at 2099-12-31T23:59Z.", trace)` passed with no claims;
  "47h", "₹45k" and "120min" were not claims; "C-1042 (R. Sharma)" passed; later "₹5 lakh"
  and "Captain Reddy (C-1526)" passed although C-1526 is N. Sen.
- Fix: Timestamps, units, sign, lakh and crore are parsed; names are checked wherever they
  appear, including rank and surname beside an id.

### FL-065: Arithmetic checks let invented numbers through

- Date: 2026-10-07
- Where: verifier
- Found by: review (two rounds)
- Severity: High
- What went wrong: On Q31, 90 of 90 integers from 10 to 99 verified as derived values;
  "C-1042 has 47 hours of duty headroom" verified as 60 - 13.
- Fix: Derivation is limited to rupee amounts within one result; no integer from 10 to 99
  derives any more.

### FL-066: Legality verdicts were not checked

- Date: 2026-10-07
- Where: verifier
- Found by: review (three rounds)
- Severity: High
- What went wrong: "C-2087 is legal for P-2291." and "RULE-DUTY-02 is not breached." passed
  although C-2087 fails; then "No, C-2087 is legal…" and "C-2087 can cover P-2291."; then
  "can't" was not read as a negation.
- Fix: Verdicts are checked against the legality results, with negation scoped to its
  clause.

### FL-067: Tool arguments and error text counted as evidence

- Date: 2026-10-07
- Where: verifier
- Found by: review
- Severity: High
- What went wrong: `limit=137` made "137 passengers are affected." verify; a failed call on
  C-7777 made "C-7777 is legal for everything." verify.
- Fix: Evidence comes from results only; the model reaches tools through the role-checked
  runner.

### FL-068: The verifier rejected correct answers

- Date: 2026-10-07
- Where: verifier
- Found by: review (three rounds)
- Severity: Medium (correct answers fell back to templates)
- What went wrong: `unsourced: 27 flight` from "64.27 flight hours"; names in did-you-mean
  errors counted as unsourced; "RULE-FDP-01 passes but RULE-DUTY-02 fails." was rejected;
  "on Tuesday the 15th" flagged "15th".
- Fix: Each pattern corrected; 3,927 rendered tool calls then verified with no failures.

### FL-069: Redaction mangled dates, ids and amounts

- Date: 2026-10-07
- Where: PII redaction
- Found by: failing test and review (three rounds)
- Severity: Medium
- What went wrong: `redact_text("2026-09-14 2026-09-16")` gave "[card removed]";
  "DX404-2026-09-16 2026-09-16T08:30:00Z" gave "DX[card removed]T08:30:00Z"; "costs 18500
  24000" gave "costs [phone removed]".
- Fix: Tighter card, Aadhaar and phone patterns; a split mobile number needs a phone word.

### FL-070: Triage replies in other letter cases were discarded

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: Low
- What went wrong: Triage confidence "Medium" or "HIGH" was not recognised.
- Fix: Normalised before use.

### FL-071: A model answer with no fact, or a blanket "no cover", passed as verified

- Date: 2026-10-07
- Where: verifier and Nova model layer
- Found by: review (two rounds)
- Severity: High
- What went wrong: "Nobody can cover; cancel the flight." and "Done." were served as
  verified model answers although four legal options existed; the explainer could prepend
  "Do not call anyone out: cancel P-2291.".
- Fix: A draft needs at least one supported claim; a no-cover or cancel verdict needs an
  option search that found none.

### FL-072: The explainer could drop content and still pass

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: High
- What went wrong: "Done." replaced a 12-item reserve list as an "explained" answer.
- Fix: A rewrite must keep every id, time and number of the template.

### FL-073: The advisor prompt handed the model figures the verifier always rejects

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: Medium (wasted paid calls)
- What went wrong: "₹250,000 per flight" and other cost figures from the prompt were
  unsupported by any tool, so drafts that used them were always rejected.
- Fix: Costs removed from the prompt; it says such figures are not evidence.

### FL-074: Model usage per question included earlier questions

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: Low-Medium
- What went wrong: After the second question, usage reported 2 calls and 2000 input tokens,
  counting the first question's call.
- Fix: Usage is reset per question.

### FL-075: The explainer reworded plain lookups

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: Low
- What went wrong: Every answered lookup cost a Nova call, contrary to the docs.
- Fix: Only judgement answers are reworded, while half the time budget remains.

### FL-076: A call to a tool not on offer stayed in the conversation history

- Date: 2026-10-07
- Where: Nova model layer
- Found by: review
- Severity: Low
- What went wrong: Bedrock may reject a request whose history names a tool missing from the
  tool configuration.
- Fix: Such calls are dropped from the history.

### FL-159: "What is my name" was answered with all 600 certificates

- Date: 2026-10-09
- Where: answer pipeline
- Found by: live run (the project owner)
- Severity: High
- What went wrong: A controller asking "what is my name" or "is my certification valid?"
  heard a list of every certificate on file, starting "600 certifications".
- Cause: Nothing answered questions about the asker. The semantic router matched "my name"
  to a certificate lookup, and a certificate lookup naming no one ran with no filter. A
  controller is not on the crew roster, so "my" had no one to point at.
- Fix: "Who am I" questions are answered from the sign-in. A controller asking about "my"
  certificates or duty is told they are not on the crew roster, and a certificate question
  naming no one, no kind and no horizon is asked back.

### FL-161: "Hi" and "thank you" got the list of what CrewOps can do

- Date: 2026-10-09
- Where: answer pipeline
- Found by: live run (the project owner)
- Severity: Low
- What went wrong: Greetings, thanks and goodbyes were read as crew lookups with nothing to
  look up.
- Cause: Every utterance went to the router.
- Fix: Greetings, thanks, goodbyes and "how are you" get a short reply, by name, before
  routing; a question that opens with "hi" is still answered.

### FL-162: Spoken crew ids were heard as "see 3231"

- Date: 2026-10-09
- Where: answer pipeline (voice)
- Found by: live run (the project owner)
- Severity: Medium
- What went wrong: Asking "C-3231 is sick" by voice got "I need who is unavailable".
- Cause: Speech recognition writes the letter C as "see", "sea" or "C 32 31", and ids are
  matched by exact pattern only.
- Fix: Ids are rewritten from their spoken shapes (a letter or its sound, then four digits
  for crew and pairings, three for flights) before anything else reads the question. Dates
  and ordinary words are left alone.

### FL-168: "What disruptions are on my desk" read out 150 risk scores

- Date: 2026-10-09
- Where: answer pipeline
- Found by: live run (the project owner)
- Severity: High
- What went wrong: The question was answered with "150 risk scores: C-1017 has a risk score of 0.09 ...".
- Cause: No tool listed open disruptions, so the router's nearest match was the risk lookup.
  The first version of the new route also caught "impact on P-2291" and the evaluation's
  disruption-risk question.
- Fix: A read-only tool lists open disruptions, on the asker's desk when asked about "my
  desk". Its route leaves impact, cover, legality, cancellation and risk questions alone.

### FL-169: "Did you mean" ignored the crew member just discussed

- Date: 2026-10-09
- Where: answer pipeline
- Found by: live run (the project owner)
- Severity: Low
- What went wrong: After a question about C-1042, "is there any crew C-123?" suggested C-1017 and C-1021
  before C-1042.
- Cause: Suggestions were ranked by spelling alone.
- Fix: Ids from the conversation are offered first, then the closest spellings.

#### Auth, roles and plans

### FL-077: Sign-in ignores case, but the profile lookup did not

- Date: 2026-10-07
- Where: auth
- Found by: failing test
- Severity: Medium
- What went wrong: `AuthError: Crew1042 is crew but is linked to no crew record`.
- Fix: Usernames are lower-cased before the profile lookup.

### FL-078: Token claims were trusted without issuer and client checks

- Date: 2026-10-07
- Where: auth
- Found by: review
- Severity: High
- What went wrong: Claims with `"iss": "https://evil"`, `"token_use": "id"` and the
  super_admin group produced a super-admin caller.
- Cause: The checks were optional and off by default.
- Fix: Issuer and client are required, and only access tokens are accepted.

### FL-079: Removing someone from the team file kept their access

- Date: 2026-10-07
- Where: account provisioning
- Found by: review
- Severity: High
- What went wrong: `note: ann is not in the team (kept; --prune removes it)`, then
  "Everything already matches the team file."; the account kept the admin group and its
  profile.
- Fix: Accounts not in the file lose their groups and profile by default; `--prune` deletes
  them.

### FL-080: Unsafe role combinations and name forms were accepted

- Date: 2026-10-07
- Where: account provisioning
- Found by: review
- Severity: Low
- What went wrong: crew together with super_admin, and controller together with approver,
  passed the team check; "A.Nair" passed the crew-style name check; `'["crew,super_admin"]'`
  split into two roles.
- Fix: Crew logins hold no other role, controller with approver is refused, and groups parse
  strictly.

### FL-081: Approval could put two crew in one seat

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: High
- What went wrong: Two overlapping sick reports for C-1042 led to a commit with P-2291's
  crew holding two Captains, seven seats in all.
- Cause: A stale "replaces" value led to an append.
- Fix: Approval never appends; a missing replacement makes the plan STALE, and seat counts
  are checked.

### FL-082: Two concurrent approvals could double-book one crew member

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: High
- What went wrong: Plans on P-2216 and P-2209 both chose C-2143 and both committed.
- Fix: A per-crew roster version is checked and bumped by every commit and by stored
  crew-out reports.

### FL-083: Two seats of one role on one pairing could never be submitted

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: High
- What went wrong: `INVALID one crew member is chosen for two seats`.
- Fix: Seats are keyed by pairing, role and the person out.

### FL-084: A disruption spanning two desks could not be planned

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: Medium
- What went wrong: Each controller got `FORBIDDEN … on desk-<n>` for the other desk's
  pairing, and the disruption stayed OPTIONS_READY for good.
- Fix: Any desk the disruption touches may plan it.

### FL-085: Seats with no legal cover were silently dropped

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: High
- What went wrong: Uncoverable seats disappeared and the disruption was marked COMMITTED;
  when no seat was coverable there was no way to end it.
- Fix: Uncovered seats must be acknowledged, and a disruption with nothing coverable can be
  closed with a reason.

### FL-086: Sent-back plans stayed in the controller's queue

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: Low
- What went wrong: A stale plan stayed listed after its resubmission was committed.
- Fix: Listed only while it is the disruption's latest plan.

### FL-087: Closing could end a coverable disruption and free the sick crew member

- Date: 2026-10-07
- Where: plans
- Found by: review (regression from the fix above)
- Severity: High
- What went wrong: C-1042 was unavailable before the close and available after it.
- Fix: Close refuses while any seat is coverable, and CLOSED keeps the crew unavailable.

### FL-088: Smaller plan defects

- Date: 2026-10-07
- Where: plans
- Found by: review
- Severity: Low-Medium
- What went wrong: A transient transaction conflict was recorded as STALE; contention
  warnings ignored dates; a large plan could exceed DynamoDB's 100-action transaction limit.
- Fix: A RETRY error leaves the plan submitted; contention is date-aware; plans are capped
  at 20 seats.

### FL-160: Crew could not ask about their own certificates

- Date: 2026-10-09
- Where: tool permissions
- Found by: probe while fixing FL-159
- Severity: Medium
- What went wrong: A crew member asking "is my medical valid?" was told their role has no
  access.
- Cause: Crew had no lookup tool at all, only their own duty clock, legality and briefs.
- Fix: Crew may look up their own profile, certificates and trips, filtered to their own
  crew id and nothing else.

### FL-170: Every disruption on an aircraft went to the same desk

- Date: 2026-10-09
- Where: analysis, desks
- Found by: the project owner
- Severity: Medium
- What went wrong: Desks owned fixed aircraft, so one controller could hold every open disruption while
  another had none, and "which desk covers VT-DXA" read back that fixed split.
- Cause: A disruption's desk was worked out from the aircraft it touched.
- Fix: Each analysed disruption goes to the desk with the fewest open disruptions, then the
  fewest in all, then desk order. Desks own no aircraft. Older records keep the old
  reading.

### FL-171: Cancelling could not be chosen

- Date: 2026-10-09
- Where: plans, console
- Found by: the project owner
- Severity: Medium
- What went wrong: The cancellation row was shown but could not be selected or submitted.
- Cause: A plan held one crew id per seat; cancelling was shown for information only.
- Fix: A plan may cancel the trip or operate one short, after a warning (made safe in
  FL-184 to FL-186).

### FL-184: Two seats flown short on one trip went below the cabin minimum

- Date: 2026-10-09
- Where: plans
- Found by: independent review
- Severity: High
- What went wrong: A plan choosing "operate one short" for both of two sick cabin crew on P-2202 (162
  seats, minimum four) was approved and left the trip with three.
- Cause: Each seat was judged alone against the published roster, which still counted the
  other missing crew member, and approval re-checked nobody for a seat left empty.
- Fix: "One short" counts every cabin crew member reported unavailable on the trip's days. A
  plan is refused if the trip would fall below one per 50 seats, counting the whole plan,
  and approval checks again, so a later sick report makes the plan stale.

### FL-185: Cancelling a captain's seat left the first officer alone on the trip

- Date: 2026-10-09
- Where: plans
- Found by: independent review
- Severity: High
- What went wrong: Approving "cancel" for P-2291's captain removed only the captain, so the trip was rostered
  with one pilot; a plan cancelling two seats of one trip counted its cancellation twice.
- Cause: Cancelling was applied like an empty seat, not as the trip being cancelled.
- Fix: Cancelling a seat cancels the whole trip: every seat of that trip in the plan must be
  cancelled, the cost is counted once, and approval releases the trip's whole crew. A second
  review found the cancelled flights still offered as positioning flights (KS589 for
  P-2203's captain) and the trip still asking for cover: the operation now reads approved
  cancellations when it loads, so those flights carry nobody, hold no gate, are marked
  cancelled in lookups, and the trip has nothing to re-crew.

### FL-186: A seat with no legal crew could not be cancelled

- Date: 2026-10-09
- Where: plans, console
- Found by: independent review
- Severity: Medium
- What went wrong: Cancel and "one short" could be chosen only where some crew member was legal, the one
  case they are not needed.
- Cause: A seat with no legal cover was always listed as uncovered, before any choice was read,
  and the console offered choices only on coverable seats.
- Fix: A seat with no legal crew may be chosen to be cancelled or flown short, on the server
  and on the record page, and counts as decided once it is.

### FL-190: Admin and super admin were the same role in code

- Date: 2026-10-09
- Where: roles, README and ARCHITECTURE
- Found by: the project owner's question
- Severity: Medium
- What went wrong: The docs gave the super admin "manage admins, system-wide settings,
  emergency access" and the admin "users and roles, roster uploads", but every check
  treated the two alike and none of those actions existed in the app.
- Cause: The role table was written before the admin features, and never revisited.
- Fix: One real distinction, as airlines draw it: an admin may only tighten a rule
  threshold, and relaxing one back towards regulation needs a super admin. It is checked
  against the stored value, and the admin page says so. Accounts are still created by the
  provisioning command; the role table now says only what is built.

### FL-198: One captain out on both days of a trip became two competing disruptions

- Date: 2026-10-10
- Where: desk assignment, joint plan, contention
- Found by: live run (the project owner)
- Severity: Medium
- What went wrong: C-1042 was reported out for 13 Oct and, separately, for 14 Oct: both
  days of P-2291's one captain seat. The reports went to two desks (Rohit and Divya), each
  showed the other as "competing for the same crew", and "Build a joint plan" failed with
  `C-1042 named as out more than once`.
- Cause: Each report was handled as its own seat. A trip is crewed as a whole, so whoever
  covers flies both days and one cover fills both reports.
- Fix: A report needing a seat already open in another disruption goes to that desk, with
  a note. The two are no longer flagged as rivals; each record says "the same seat is also
  reported in ...", and the joint plan treats the same person out of the same seat as one
  seat. Once a plan for either is approved (every rule re-checked for the whole trip), the
  other's plan goes stale, and its record shows the seat already re-crewed, with who flies
  it, instead of options, ready to close.

### FL-208: The speaker device held the whole team's password

- Date: 2026-10-10
- Where: speaker setup, provisioning
- Found by: independent review
- Severity: High
- What went wrong: `web_env --speaker` wrote the demo team's shared password into the web app's settings as
  the speaker's, so anyone reading the hosted app's environment could sign in as any
  controller, approver or the super admin.
- Cause: Every demo account is provisioned with one password, and the speaker reused it.
- Fix: `web_env --speaker` gives `ops.speaker` a new random password, sets it on the account,
  and writes only that; it uses characters a .env file keeps exactly (an unquoted `#` or
  `$` had cut the value short).

#### Ingestion, analysis, search and configuration

### FL-089: The report worker trusted the sender written in the message

- Date: 2026-10-06
- Where: live reports
- Found by: review
- Severity: Medium
- What went wrong: Anyone able to send to the queue could claim the controller role or any
  user id; the docs said the worker checks the sender's role.
- Fix: The MCP server now sets the sender from a verified token, and the queue's policy
  refuses every other sender except listed operators.

### FL-090: The .env file was never read

- Date: 2026-10-06
- Where: configuration
- Found by: question from the user
- Severity: Medium
- What went wrong: The template said "copy to .env", but nothing read it.
- Fix: The app and the CDK app load `.env`; values set in the shell win.

### FL-091: The deploy script let .env override the command line

- Date: 2026-10-06
- Where: deploy script
- Found by: live diff
- Severity: Medium
- What went wrong: `AWS_REGION=ap-southeast-2 scripts/cdk.sh diff` still synthesized for
  us-east-1 and hit the region deny.
- Cause: Sourcing `.env` overwrote values already set in the shell.
- Fix: Values already set in the shell win.

### FL-092: The bash and Python .env parsers disagreed

- Date: 2026-10-06
- Where: configuration
- Found by: review
- Severity: Medium
- What went wrong: Trailing spaces or CRLF exported `"crewops  \r"` in bash; `S = v` was
  skipped; a bare `T` became `T=T`; deploy and seed could use different regions.
- Fix: The deploy script evaluates the Python parser's exports: one parser.

### FL-093: Duplicate keys could half-write a batch

- Date: 2026-10-06
- Where: data store
- Found by: review
- Severity: Medium
- What went wrong: Two licences or two reserve blocks for one person mapped to one key; the
  write raised mid-batch while the batch writer still flushed, leaving the table half
  written.
- Fix: Duplicate keys are refused before anything is written.

### FL-094: An oversized roster upload reported an invented size

- Date: 2026-10-06
- Where: roster uploads
- Found by: self-review
- Severity: Low
- What went wrong: The size check passed a padded buffer, so the report showed a made-up
  size.
- Fix: Rejected directly with the real size and the limit.

### FL-095: A long report id became a poison message

- Date: 2026-10-06
- Where: live reports
- Found by: review
- Severity: High
- What went wrong: A 3000-character `report_id` became a rejection key over DynamoDB's
  2048-byte limit, so the message retried into the dead-letter queue and was never recorded.
- Fix: Only well-formed ids become keys; others are filed as unidentified.

### FL-096: A huge rejection reason exceeded DynamoDB's item limit

- Date: 2026-10-06
- Where: live reports
- Found by: review
- Severity: High
- What went wrong: A 500,000-character field produced "Item size has exceeded the maximum
  allowed size", so retries, then the dead-letter queue.
- Cause: Error messages echoed the input in full.
- Fix: Quoted input is shortened and reasons are capped.

### FL-097: A reused report id lost the second report

- Date: 2026-10-06
- Where: live reports
- Found by: review
- Severity: Medium
- What went wrong: A different report with an existing id was silently treated as a
  duplicate.
- Fix: Content is compared: the same content is a duplicate, different content an id
  conflict.

### FL-098: Rejection records overwrote each other, and future times were accepted

- Date: 2026-10-06
- Where: live reports
- Found by: review
- Severity: Low
- What went wrong: Rejections in the same second shared a key; reports dated in the future
  were accepted.
- Fix: Rejections are keyed by time and message id; reports more than 5 minutes ahead are
  refused.

### FL-099: A resend with a fresh timestamp was rejected as an id conflict

- Date: 2026-10-07
- Where: live reports
- Found by: live run in AWS
- Severity: Medium
- What went wrong: `report r-demo-0001 stored`, then `rejected report r-demo-0001: report id
  already used for a different report`.
- Cause: The content comparison included `reported_utc`, which the send command stamps anew.
- Fix: The report time is ignored when comparing; the first copy keeps its time.

### FL-100: The analysis ignored other open disruptions

- Date: 2026-10-07
- Where: disruption analysis
- Found by: review
- Severity: Medium
- What went wrong: When a second report took C-3310 out, the first disruption's analysis
  still recommended C-3310; the existing test built exactly this case and passed.
- Fix: Crew out in another open disruption are excluded, with the reason naming that report.

### FL-101: Analysis failures retried pointlessly and the rerun command stopped early

- Date: 2026-10-07
- Where: disruption analysis
- Found by: review
- Severity: Low
- What went wrong: A bad stored report failed four times, then went to a dead-letter queue
  holding only stream pointers; `--pending` stopped at the first failure; a deleted item
  reported already analysed.
- Fix: An ANALYSIS_FAILED audit event; `--pending` handles each item and exits non-zero on
  failure; a missing item is reported as missing.

### FL-102: Analysis summaries could mislead, and their size was unbounded

- Date: 2026-10-07
- Where: disruption analysis
- Found by: review
- Severity: Low-Medium
- What went wrong: DX412's summary repeated "cancelling costs ₹1,500,000" six times and did
  not name overnight rest as the cause; the stored output had no bound against the 400 KB
  item limit.
- Fix: Cost stated once per trip, the cause named, output capped.

### FL-103: Search indexes leaked answers and matched questions to themselves

- Date: 2026-10-07
- Where: search
- Found by: review
- Severity: High
- What went wrong: The indexed text "Draft the callout notification to C-3310 for covering
  P-2291" surfaced C-3310 for any P-2291 question; every gold question matched itself with
  score 1.0.
- Fix: Ids and dates are masked in indexed questions and queries; evaluations score
  rewordings, never indexed prompts.

### FL-104: Smaller search defects

- Date: 2026-10-07
- Where: search
- Found by: review
- Severity: Low
- What went wrong: "FDP" did not keyword-match `rule-fdp-01`; past 100 documents keyword
  scoring would have covered only the first page; the index command printed raw tracebacks.
- Fix: Hyphenated ids split into parts, an index-size guard, one-line errors.

### FL-127: The settings command crashed with a traceback on a missing output

- Date: 2026-10-08
- Where: web settings command
- Found by: review
- Severity: Low
- What went wrong: A stack deployed before an output existed made `python -m
  crewops.web_env` end in a raw KeyError traceback.
- Cause: Only the stack reads were inside the error handling, not the use of their outputs.
- Fix: Building the settings is inside it too; a missing output gives a one-line message.

### FL-151: A late flight named "the next departure at its gate" days ahead

- Date: 2026-10-08
- Where: disruption analysis (gates)
- Found by: smoke run of every report type on the seed
- Severity: Low
- What went wrong: A 60-minute delay told the gate agent of a departure boarding 30 hours
  later that its gate was "free in time, 1815 min before it boards".
- Cause: The next slot at the gate was taken however far away it was.
- Fix: Only a departure boarding within three hours of the late one leaving is told.

### FL-152: A refused gate change suggested a remote stand instead of staying put

- Date: 2026-10-08
- Where: disruption analysis (gates)
- Found by: smoke run of every report type on the seed
- Severity: Low
- What went wrong: Asked to move DX401 onto a gate already held, the analysis first
  suggested its own current gate as the "new" one, then, with that excluded, a remote stand,
  although the flight could simply keep its gate. Flight information and customer care
  would have been told about a gate change that never happened.
- Cause: The alternative search treated the flight's own gate like any other.
- Fix: The flight keeps its own gate when that stays free for it, and then only the
  station's gate control and duty manager are told. Otherwise the first free gate is
  suggested.

### FL-153: Capped analyses did not count the new lists they cut

- Date: 2026-10-08
- Where: disruption analysis (stored size)
- Found by: test run
- Severity: Low
- What went wrong: A long station closure stored only the first late departures, gate moves
  and notices, with nothing saying more existed.
- Cause: The count of what was left out covered only the original lists.
- Fix: Late departures, gate moves, next-at-gate entries and notices are counted too.

### FL-154: A long delay double-booked gates and "moved" flights onto their own gate

- Date: 2026-10-08
- Where: disruption analysis (gates)
- Found by: independent review
- Severity: High
- What went wrong: For DX401 400 minutes late, the aircraft's own later leg was moved
  "because the gate is still held by late DX401" onto a gate another flight held, and DX451
  was "moved" from BLR-G1 to BLR-G1, which gate control would have been emailed.
- Cause: The gate check treated the late aircraft's later legs as on time, kept a moved
  flight's original departure time, and kept looking for clashes at the gate it had
  already left.
- Fix: Late departures clash only by their own late times and move with their own late
  departure; each moved flight is tracked at its new gate; a move onto the same gate is
  impossible. Tests check, over hundreds of delays, that no gate is held twice.

### FL-155: A flight re-gated by another desk's delay pulled that desk in

- Date: 2026-10-08
- Where: disruption analysis (desks)
- Found by: independent review
- Severity: Medium
- What went wrong: A delay to a desk-1 aircraft that re-gated a desk-3 flight belonged to
  both desks, so the desk-3 controller could mark it resolved.
- Cause: The aircraft a disruption touches included every flight it re-gated.
- Fix: Only the flights the disruption is about decide its desks; for a gate closure those
  are the moved flights themselves. Disruptions analysed before desks were recorded stay
  closable by nobody, as before.

### FL-157: Two bused departures on one trip day counted only one as late

- Date: 2026-10-08
- Where: disruption analysis (gate closure)
- Found by: independent review
- Severity: Low
- What went wrong: When a gate closure sent two departures of the same duty day to a remote
  stand, the earlier one's 15 minutes went missing from the late list and the notices.
- Cause: Each duty day kept the last bused departure instead of the first.
- Fix: The earliest one sets the day's slip, and every later leg slips with it.

### FL-172: Moving the demo week to October moved real dates too

- Date: 2026-10-09
- Where: seed data, docs
- Found by: review of the shift
- Severity: Medium
- What went wrong: The first pass shifted "3 May", bare "the 14th" in prose, a docstring's flight list and
  certificate start dates that lie before the demo week. Later, a review found an eval
  note listing KS589 on "14/16/18/18 Oct", and the flight rename had turned the spoken
  example "D X 412" into KS412 in the docs.
- Cause: The shift matched every day-and-month and every ordinal.
- Fix: Only ISO dates from 1 August 2026 and written dates in August or September move;
  certificates were rebuilt from the original with that rule, and the ordinals were
  checked by hand. The eval note now reads 12/14/16/18 Oct and the example DX412.

### FL-173: Extra cabin crew were added to trips already flown

- Date: 2026-10-09
- Where: seed data
- Found by: seed validation
- Severity: Low
- What went wrong: Two of the fifth cabin crew added for "operate one short" were on leave or resting on
  trips that had already flown.
- Cause: They were picked by base and rank only.
- Fix: Removed from those two trips; the other six are legal on theirs.

### FL-174: Gate closures had no emergency gates and no record of cause

- Date: 2026-10-09
- Where: gate analysis
- Found by: the project owner
- Severity: Medium
- What went wrong: With every gate taken, a departure went straight to a remote stand, the other free
  gates were not shown, and a closure from outside the airport (weather) was handled
  like a jet-bridge fault.
- Cause: Only ordinary gates were modelled, and a closure carried no cause.
- Fix: Each station keeps emergency gates, used after the ordinary ones and before a remote
  stand. Each move lists the other free gates, and an outside cause readies the
  emergency gates and tells the operations teams.

### FL-187: Falling ill after the trip's last leg still asked for a callout

- Date: 2026-10-09
- Where: disruption analysis
- Found by: independent review
- Severity: Medium
- What went wrong: C-1569, reported ill 30 minutes after P-2202's last leg left, got a recommended
  callout of ₹10,910 for a seat with no flight left.
- Cause: A seat stayed in the analysis even when every one of its legs had flown.
- Fix: Such a seat is dropped with a note; with none left, there is nothing to cover. The
  cabin minimum also counts that person as present on the trip they finished, so a second
  absence on it can still be flown one short.

### FL-189: Re-seeding a changed week would have kept the old week beside it

- Date: 2026-10-09
- Where: seed loader
- Found by: check before redeploying the October week
- Severity: High
- What went wrong: Loading the October week into the dev table would have left every
  September flight, gate slot, reserve and old disruption in place, and the engine reads
  the whole table, so both weeks would have been planned against together.
- Cause: The loader only writes items; nothing removed records no longer in the seed.
- Fix: `--replace` empties the table first, on the disposable dev and test stages only;
  any other stage refuses it.

### FL-216: A sick report for a day off landed on a desk with nothing to plan

- Date: 2026-10-11
- Where: disruption analysis
- Found by: walking through the crew flow (a captain reporting sick on a day off)
- Severity: Medium
- What went wrong: A crew member reported unavailable on days they fly nothing was analysed as
  "nothing to cover" but still went to a controller's desk as ready for options: it was
  counted in the desk's load, emailed and chimed the controller, and after ten minutes was
  escalated to the duty supervisor, all for nothing to do.
- Cause: Every analysed report moved to OPTIONS_READY, whatever the analysis found.
- Fix: Such a report is stored as recorded (closed by the analysis, with the reason), so no
  desk, alert or escalation follows, while the person stays out of cover options on those
  days. The record notes their next duty, and the crew page says before sending that nothing
  needs covering. Someone on call (reserve) in the period still goes to a desk, since the
  reserve pool is one smaller; found in the independent review of this fix, with severity
  counting two seats on one trip as twice the passengers, "flies today" measured from the
  report rather than from today, and reporters seeing why colleagues were absent on the new
  Operations board (they now see only who and when).

#### MCP server, memory and notifications

### FL-017: A server's "forbidden" told a caller which disruptions exist

- Date: 2026-10-07
- Severity: Medium (found in review before deployment)
- Task attempted: Let crew read only disruptions they reported, through `show_disruption`.
- Steps taken: Returned NOT_FOUND for a missing id and FORBIDDEN for one the caller may not
  read.
- Expected: A caller learns nothing about records they may not read.
- Actual: The two answers differed, so anyone signed in could probe ids and learn which
  disruptions exist.
- Workaround: Both cases now return the same NOT_FOUND message.
- Suggestion: n/a (our own code); noted because the same pattern is easy to miss in any
  role-checked API.

### FL-105: Unexpected errors showed AWS internals to MCP clients

- Date: 2026-10-07
- Where: MCP server
- Found by: review
- Severity: Medium
- What went wrong: A client received `Error executing tool ask: An error occurred
  (AccessDeniedException) when calling the Scan operation: User:
  arn:aws:sts::<account>:assumed-role/…/sess is not authorized …`.
- Fix: Logged on the server; the client sees `INTERNAL: something went wrong; it has been
  logged`.

### FL-106: Shared server state was not thread-safe, and the key fetch could wait 30 s

- Date: 2026-10-07
- Where: MCP server
- Found by: review
- Severity: Medium
- What went wrong: Three concurrent questions took 4.54 s instead of about 1.5 s; once tools
  moved to threads, the cached pipeline and its usage counters were shared; the Cognito key
  fetch had a 30 s default timeout.
- Fix: Locks around the cache and each question, and a 3 s key-fetch timeout.

### FL-107: Local server runs listened on every interface

- Date: 2026-10-07
- Where: MCP server
- Found by: review
- Severity: Low
- What went wrong: A local run bound to 0.0.0.0, with no protection against DNS rebinding.
- Fix: Local runs bind to 127.0.0.1; only the AgentCore entry point uses 0.0.0.0.

### FL-108: The command-line client printed a one-item list as an object

- Date: 2026-10-08
- Where: MCP client
- Found by: review
- Severity: Low
- What went wrong: `my_queue` with one item printed an object, with two a list.
- Fix: List tools always print a list, checked against the server's tools by a test.

### FL-109: Alert-address edge cases were unexplained

- Date: 2026-10-08
- Where: alerts
- Found by: review
- Severity: Low
- What went wrong: Removing an unconfirmed address failed without a reason, and lapsed
  unconfirmed addresses stayed listed and counted toward the limit.
- Fix: The tools explain both; removal works once confirmed or after the address lapses.

### FL-139: Whoever filed a report could read the desk's whole view of it

- Date: 2026-10-08
- Where: MCP server
- Found by: review
- Severity: High
- What went wrong: A reporter or crew member reading their own report got the trip's crew
  names and ids, the ranked replacements with costs, the plans, the audit trail and, once
  added, other open disruptions and their desks (for example r-sick1938 on desk-1).
- Cause: The filer was let in for the same full record as staff.
- Fix: A filer who is not staff gets only the status, a plain outcome ("Crew control is
  arranging cover.") and their own report.

### FL-148: Live options for a delayed seat were worked out for the wrong day

- Date: 2026-10-08
- Where: MCP server, full record
- Found by: independent review
- Severity: Medium
- What went wrong: When the delay was on a later day of a trip, the options shown were
  judged as if the first day were late, so a listed person could fail approval.
- Cause: The record dropped the seat's delay date, and the search then defaulted to the
  trip's first day.
- Fix: The delay date is kept; a test covers a delay on the trip's second day.

### FL-158: The resolve remark never reached whoever reported it

- Date: 2026-10-08
- Where: MCP server, web report page
- Found by: independent review
- Severity: Low
- What went wrong: "Mark resolved" said the remark goes to the reporter, but the reporter
  saw only "Resolved by crew control."
- Cause: The reporter's view did not read the audit trail.
- Fix: The reporter's record and their report list show crew control's remark.

### FL-175: A crew member's sick report landed nowhere

- Date: 2026-10-09
- Where: MCP server
- Found by: live run (the project owner)
- Severity: High
- What went wrong: A crew member reported sick from their page; the reporter never saw it, and neither
  did the crew member.
- Cause: A refused report was stored without a link to whoever filed it, and "My reports"
  listed only disruptions.
- Fix: Every report is recorded under its filer when sent. "My reports" shows each one as
  waiting, refused with the reason, or its disruption's status, and a reporter also sees
  crew members' own reports.

### FL-176: The same sick days could be reported twice

- Date: 2026-10-09
- Where: MCP server
- Found by: the project owner
- Severity: Medium
- What went wrong: A crew member could report the same days again, opening a second disruption.
- Cause: Nothing checked for an open absence covering those days.
- Fix: An overlapping absence is refused with DUPLICATE, and the crew page shows the days
  already reported.

### FL-188: The same days could be reported twice while the first was still queued

- Date: 2026-10-09
- Where: MCP server
- Found by: independent review
- Severity: Low
- What went wrong: Two reports of the same sick days sent before the worker stored the first both passed.
- Cause: The check read only stored disruptions.
- Fix: It also reads reports filed but not yet stored or refused.

### FL-206: Two events in the same second made the wrong one "latest"

- Date: 2026-10-10
- Where: spoken alerts
- Found by: test, while building "welcome back"
- Severity: Low
- What went wrong: A plan submitted and rejected within one second was announced as
  "waiting for approval", not "sent back".
- Cause: Audit times have one-second resolution, and ties were broken by the action's name.
- Fix: Ties are broken by where the action falls in a disruption's life, so the later stage
  wins.

### FL-209: The public skill URL fetched any certificate URL and crashed on bad ones

- Date: 2026-10-10
- Where: Alexa skill endpoint
- Found by: independent review
- Severity: Medium
- What went wrong: Before any signature check, a certificate URL with a new query string (`...cert.pem?n=1`)
  made the endpoint fetch it again and cache another chain, without limit; an unreachable
  or non-certificate URL raised an error instead of a refusal.
- Cause: The URL check allowed queries, the cache was keyed on the raw URL and never trimmed, and
  fetch and parse errors were not turned into refusals.
- Fix: Queries are refused, the cache is keyed on the normalised URL and holds a few chains, and
  any fetch or parse failure is a 400 refusal.

### FL-210: Speaker and alert defects found in review

- Date: 2026-10-10
- Where: spoken alerts, speaker feed, Alexa links
- Found by: independent review
- Severity: Low
- What went wrong: A speaker could still run the question pipeline; a desk speaker could never hear "needs a
  look"; an older disruption on two desks reached only one; every poll read every disruption
  ever resolved; linked Echos kept the desk a person had at their last request.
- Cause: Each was a gap in the first version's rules.
- Fix: Speakers may not ask; failures go to the whole-room speaker, as documented; alerts reach
  every owning desk; the feed reads the latest resolved disruptions only, and one read
  serves all screens for a few seconds; notifications follow each person's current
  profile.

### FL-214: The notifier could not read profiles, so Alexa chimes never went out

- Date: 2026-10-11
- Where: notifier Lambda permissions (Alexa and desktop alerts), escalation
- Found by: independent review
- Severity: High
- What went wrong: Each notification first reads the person's current role and desk from
  their profile, but the notifier was granted only Query, not GetItem. Every Alexa chime
  (and every desktop alert) failed with AccessDenied, and the failure was only logged. The
  same review found that desktop alerts were sent one after another inside a 30 s Lambda, so
  a few slow push services could time it out and retry the whole batch, emails included; that
  escalation emailed before recording the escalation, so a retry could email twice; and that
  a long stage name could cut the time off an escalation schedule's name, so a second check
  for the same disruption collided and was lost.
- Cause: The infra test checked the notifier could Query and not write, not every call the
  code makes; the sending loop had no time limit; the email came before the record.
- Fix: GetItem is granted (the infra tests assert it); desktop alerts are sent side by side
  and given up after 20 s, in a 60 s Lambda; the escalation is recorded first; schedule names
  keep the time and a hash of the id whatever the lengths.

#### Rule settings

### FL-130: Two quick changes to one rule kept only one audit record

- Date: 2026-10-08
- Where: rule settings
- Found by: review
- Severity: Medium
- What went wrong: Setting RULE-REST-04 from 12 to 13 and then 13 to 14 within one second
  left only the 13 to 14 record; the first change had no audit trail.
- Cause: The audit key was only the second, the action and the rule, written without a
  condition.
- Fix: Each record's key also carries a short random id, and the write refuses to replace an
  existing record.

### FL-131: A non-numeric "value it replaces" crashed the rule change

- Date: 2026-10-08
- Where: rule settings
- Found by: review
- Severity: Low
- What went wrong: `expected=None` raised `decimal.InvalidOperation`, and a string failed
  while building the conflict message ("Unknown format code 'g' for object of type 'str'");
  both reached the caller as INTERNAL.
- Cause: Only the new value was checked to be a number.
- Fix: Both numbers are checked, and either being wrong is refused as INVALID.

#### Web app

### FL-119: Enter in the reason box approved and committed a plan

- Date: 2026-10-08
- Where: web console
- Found by: review
- Severity: High
- What went wrong: On a disruption page, an approver typing a rejection reason and pressing
  Enter submitted the form as its first button, "Approve and commit", and the roster
  changed.
- Cause: The approve, reject and withdraw buttons shared one form with the reason field;
  Enter submits a form with its first submit button.
- Fix: No form: the buttons act on click only, Enter in the reason box does nothing,
  approving needs a second confirm click, and Reject needs a reason.

### FL-120: Leaving a page while it connected left a live socket open

- Date: 2026-10-08
- Where: web live updates
- Found by: review
- Severity: Medium
- What went wrong: If the page unmounted while `/api/live` was being fetched, the socket was
  still opened afterwards and kept calling the old page's handler, which re-read the queue.
- Cause: The connect step did not check, after its awaits, whether the page had gone.
- Fix: It checks after each wait and opens nothing once the page is gone.

### FL-121: Live updates retried forever when access was refused

- Date: 2026-10-08
- Where: web live updates
- Found by: review
- Severity: Low
- What went wrong: Signed out, or a role without access to the channel, the hook retried
  every 30 seconds for as long as the page was open, each try possibly refreshing the
  session.
- Fix: A 401 from `/api/live`, or an unauthorized error from AppSync, stops retrying and
  shows "unavailable".

### FL-122: Nothing limited request sizes or spoken answers

- Date: 2026-10-08
- Where: web server routes
- Found by: review
- Severity: Medium
- What went wrong: Route handlers read whole bodies before checking them, and any signed-in
  user could ask Polly for unlimited 1,500-character answers.
- Cause: Next applies no body-size limit to route handlers.
- Fix: Each route refuses a body declared larger than it accepts, before reading it; speech
  is limited to 30 answers per user in 10 minutes.

### FL-123: A failed sign-in check spun forever

- Date: 2026-10-08
- Where: web shell
- Found by: review
- Severity: Low
- What went wrong: When `whoami` failed for any reason but 401 (for example the MCP server
  unreachable), the page showed a spinner indefinitely.
- Fix: The error is shown with a "Try again" link.

### FL-124: The admin form cleared the address even when adding failed

- Date: 2026-10-08
- Where: web admin page
- Found by: review
- Severity: Low
- What went wrong: After a refused address (duplicate, daily limit, invalid), the typed
  address was gone.
- Cause: The form was reset as soon as the request started.
- Fix: It is reset only after a successful add.

### FL-125: Each spoken answer stayed in memory until the tab closed

- Date: 2026-10-08
- Where: web assistant
- Found by: review
- Severity: Low
- What went wrong: Every answer's MP3 got an object URL that was never revoked.
- Fix: The previous URL is revoked before each new answer and when the page closes.

### FL-126: A spoken question could land in the conversation just left

- Date: 2026-10-08
- Where: web assistant
- Found by: review
- Severity: Low
- What went wrong: Pressing "New conversation" while listening sent the spoken question
  under the old conversation id.
- Cause: The id was captured when listening started.
- Fix: The id is read when the question is sent.

### FL-129: Long spoken answers were cut off inside the speaker card

- Date: 2026-10-08
- Where: web assistant (Alexa+ module)
- Found by: screenshot of the redesigned console
- Severity: Low
- What went wrong: A long answer (cover options plus the six affected flights) ran past the
  speaker card and was clipped mid-sentence, ending at "DX590 and".
- Cause: The card hides overflow and the answer had no line limit.
- Fix: The speaker shows the first five lines, with the whole answer as a tooltip and in the
  voice log below.

### FL-132: Controllers saw a Submit button for disruptions off their desk

- Date: 2026-10-08
- Where: web console
- Found by: review
- Severity: Low
- What went wrong: Any controller saw "Submit the recommended plan" for any disruption ready
  for a plan; for one off their desk the server refused with FORBIDDEN.
- Fix: The button appears only for disruptions on the controller's desk.

### FL-133: The crew calendar showed the week of the first trip, not this week

- Date: 2026-10-08
- Where: web crew view
- Found by: review
- Severity: Low
- What went wrong: "This week" showed the Monday-to-Sunday week of the first rostered day,
  so a crew member whose trips start later saw a different week.
- Fix: It shows the week containing the operation's current day.

### FL-134: A legality check could seem to contradict the ranked option

- Date: 2026-10-08
- Where: web console
- Found by: review
- Severity: Low
- What went wrong: A positioning or delayed option ranked legal by the engine could show
  "Not legal now" on its card, because the on-demand check judges the trip as rostered.
- Fix: Such cards say the check is against the trip as rostered and that the ranking already
  allows for the positioning or delay.

### FL-135: Missing accessibility basics on the new pages

- Date: 2026-10-08
- Where: web app
- Found by: review
- Severity: Low
- What went wrong: The report sheet did not close on Escape, option cards did not say
  whether they were expanded, the status banner was not announced, and the view menu claimed
  list roles without keyboard support.
- Fix: Escape closes the sheet and the menu, the first field takes focus, cards set
  aria-expanded, the banner is a polite live region, and the menu uses plain buttons with
  aria-current.

### FL-137: Every web action started a new server session, so pages loaded slowly

- Date: 2026-10-08
- Where: web app, MCP client
- Found by: live run
- Severity: Medium
- What went wrong: Opening a full record took a long time: each tool call opened a new MCP
  session, and AgentCore gave each new session a fresh server instance, which loaded the
  whole operation before answering.
- Cause: The web server connected, called and closed for every request.
- Fix: One connected MCP client per signed-in session is kept for up to four minutes, so
  calls reuse the same warm instance and skip the handshake; reads retry once on a new
  connection if AgentCore has ended the session, writes never retry.

### FL-138: Usability problems found in the first live walkthrough

- Date: 2026-10-08
- Where: web console and full record
- Found by: live run (the project owner)
- Severity: Medium
- What went wrong: Cancelling was a line of small text instead of an option; the
  recommendation was a dense machine summary ("L. Verma (C-3231) unavailable (sick): P-2224
  Captain: Assign Captain M. Pillai (C-3315) (reserve callout), ₹18,500"); options sat in a
  narrow column on the left; a controller's figures showed approvals they cannot act on;
  approving showed as two audit rows, approved and committed; nothing showed whose desk a
  disruption belonged to or whether another desk wanted the same crew.
- Cause: The first design followed the data's shape rather than the controller's questions.
- Fix: A recommendation card, cancellation as the last ranked option, a two-column record
  with coverage figures, the trip's flights and crew, role-specific figures and actions, one
  "Approved and committed" row, a desk banner, and "Also wanted by" on options another open
  disruption ranks within a day of this one.

### FL-140: A dropped connection broke every later write until a read

- Date: 2026-10-08
- Where: web MCP client
- Found by: review
- Severity: Medium
- What went wrong: After a transport failure on a write (an ended AgentCore session), the
  broken client stayed cached, so every following write failed with "CrewOps is
  unreachable".
- Cause: Only reads replaced a failed connection.
- Fix: Any transport failure drops that connection, only if it is still the cached one;
  reads alone retry.

### FL-141: "Close without a plan" showed when only some seats lacked cover

- Date: 2026-10-08
- Where: web console
- Found by: review
- Severity: Low
- What went wrong: On a disruption with one uncoverable seat among others, the close button
  always ended in "still has legal cover".
- Fix: Close shows only when no seat can be covered, and then Submit is hidden.

### FL-142: Contention over-flagged on disruptions with several seats

- Date: 2026-10-08
- Where: web console and MCP server
- Found by: review
- Severity: Low
- What went wrong: A person ranked only for one seat could be flagged against another
  disruption that was near only a different seat's date.
- Cause: Crew and dates were pooled across all seats.
- Fix: Each seat's options are matched against trips near that seat's own dates.

### FL-143: A controller could not choose an option, only submit the recommendation

- Date: 2026-10-08
- Where: web full record
- Found by: live run (the project owner)
- Severity: Medium
- What went wrong: There was no way to pick a different legal option for a seat, and on
  another desk's disruption there was no plan button at all, with nothing explaining why.
- Cause: The server accepted a chosen person per seat (`choices`), but the page never
  offered one; planning is limited to the disruption's own desk.
- Fix: Options and recommendation cards are selectable on your own desk's disruptions;
  Submit sends the choices, and choosing other than the recommendation asks for the reason,
  which goes to the approver. Another desk's disruption says whose it is.

### FL-144: "Passengers at risk" was the seat count

- Date: 2026-10-08
- Where: web full record
- Found by: live run (the project owner)
- Severity: Low
- What went wrong: Seats at risk and passengers at risk always showed the same number (972
  and 972).
- Cause: The seed has no booking counts; the engine's passenger figure is the uncrewed
  flights' seat capacity.
- Fix: Only "Seats at risk" is shown.

### FL-145: The console scrolled as a whole page as well as inside its panels

- Date: 2026-10-08
- Where: web console
- Found by: live run (the project owner)
- Severity: Low
- What went wrong: On a wide screen the page grew a vertical scrollbar although each panel
  already scrolled.
- Cause: The panels' height did not count the figures strip above them, and the live banner
  pushed content down.
- Fix: The workspace fits the window on wide screens and only the panels scroll; the banner
  floats over the page.

### FL-146: Recommendation cards showed no reserve-cover score

- Date: 2026-10-08
- Where: web full record
- Found by: screenshot
- Severity: Low
- What went wrong: Each policy card read "Reserve cover kept —/100".
- Cause: The engine's policy picks are shorter copies of the options, without the resilience
  figure.
- Fix: The server returns each pick as the option's full record.

### FL-147: The full record's main Submit failed when one person covered two seats

- Date: 2026-10-08
- Where: web full record
- Found by: independent review
- Severity: High
- What went wrong: For a week-long absence with two seats, "Submit the recommended plan"
  failed with "one crew member is chosen for two seats", although a plan from the console
  went through.
- Cause: The page sent every seat's top live option as an explicit choice, and an explicit
  choice outranks the analysis's joint plan, which gives each seat a different person.
- Fix: Only seats the controller changed are sent; the rest follow the joint plan, which is
  also what the page now shows as the recommendation. A pick that is no longer a legal option
  blocks Submit until the controller chooses again.

### FL-149: A seat that became coverable after the analysis broke Submit

- Date: 2026-10-08
- Where: web full record
- Found by: independent review
- Severity: Medium
- What went wrong: If the rules or roster changed after the analysis, a seat stored as
  "no legal cover" could show selectable options, and Submit then failed with "no such
  seat".
- Cause: The page offered choices for any seat with live options; the server plans only
  seats the stored analysis could cover.
- Fix: Only coverable seats can be chosen. Others show their live options for information,
  with a note to re-run the analysis.

### FL-150: "Build a joint plan" treated a delayed crew member as absent

- Date: 2026-10-08
- Where: web full record
- Found by: independent review
- Severity: Low
- What went wrong: A competing delay disruption went into the joint plan as if its crew
  member were off sick.
- Cause: Every competing seat with a crew member was sent as an absence.
- Fix: Competing disruptions carry their report type, and only absences (sick, certificate
  lapse) go into the joint plan.

### FL-156: A joint plan across desks left out a rival report of several sick crew

- Date: 2026-10-08
- Where: web full record
- Found by: independent review
- Severity: Medium
- What went wrong: "Build a joint plan" ignored crew out in a competing multiple-sick report.
- Cause: The list of absence types predated the new report type.
- Fix: It includes several-crew reports.

### FL-163: The voice module could not be paused and cut long answers off

- Date: 2026-10-09
- Where: web Alexa+ module
- Found by: live run (the project owner)
- Severity: Low
- What went wrong: A spoken answer could not be paused or stopped, a long answer was cut
  off after five lines, the latest exchange looked like every other one, and the module
  was squeezed into a console column.
- Cause: The first version of the module was built for a narrow column.
- Fix: Pause, resume and stop while it speaks; the answer scrolls; the latest exchange is
  highlighted (and marked while it is spoken); Alexa+ has its own page, two columns wide,
  opened from a round button in the corner of every page.

### FL-177: The app opened on the last user's console

- Date: 2026-10-09
- Where: web sign-in
- Found by: the project owner
- Severity: Medium
- What went wrong: Opening the app went straight to the controller console of whoever signed in last,
  days earlier.
- Cause: The session cookie outlived the browser, and "/" redirected to the role's home.
- Fix: "/" opens sign-in, which offers to continue as the signed-in user or sign in as
  someone else; the cookie ends with the browser. Different users at once need separate
  browser profiles or a private window, since tabs share cookies.

### FL-178: The voice module kept listening and showed nothing

- Date: 2026-10-09
- Where: web Alexa+ module
- Found by: live run (the project owner)
- Severity: Medium
- What went wrong: After a question was spoken the listening bars kept moving, nothing appeared, and
  asking again had no effect.
- Cause: A second tap of the microphone was ignored while listening, so a speech service that
  never returned a result left it listening for good.
- Fix: Tapping again stops and sends what was heard, ten seconds of silence ends listening,
  partial transcripts are kept and errors are shown.

### FL-179: "2 waiting for you" for one disruption

- Date: 2026-10-09
- Where: web console
- Found by: the project owner
- Severity: Low
- What went wrong: After an approver sent one plan back, the controller's console said "2 waiting".
- Cause: The queue lists a disruption both as ready and as sent back, and the count was the
  number of rows.
- Fix: Disruptions are counted once.

### FL-180: A committed disruption showed today's options

- Date: 2026-10-09
- Where: web console
- Found by: the project owner
- Severity: Medium
- What went wrong: Opening a decided disruption showed options worked out now, not those the controller
  chose from, and not the choice itself.
- Cause: The page always rendered the live workspace.
- Fix: A committed disruption shows the options as they stood, the choice made, and who
  approved it and when.

### FL-181: A report's end date could be before its start

- Date: 2026-10-09
- Where: web report and crew pages
- Found by: the project owner
- Severity: Medium
- What went wrong: "To" earlier than "from" was accepted on the report and crew pages.
- Cause: The pages relied on the server's checks, which only some forms reached.
- Fix: Every date range is checked before it is sent ("Until must be on or after From"),
  and the crew page's date picker offers no day before "From".

### FL-182: KPI boxes were clipped at the panel edges

- Date: 2026-10-09
- Where: web console
- Found by: the project owner
- Severity: Low
- What went wrong: "Open disruptions" lost its left edge and "Resolved" its right.
- Cause: Their outline was drawn outside the box, and the workspace clips its overflow.
- Fix: A border inside the box.

### FL-192: Parallel calls on an ended session knocked out each other's retries

- Date: 2026-10-09
- Where: web app MCP client
- Found by: live run (the project owner)
- Severity: Medium
- What went wrong: Signed in as rohit.malhotra, the console failed with `tool disruptions
  failed Error [McpError]: MCP error -32000: Connection closed`.
- Cause: The console makes several calls at once over one cached connection. When
  AgentCore had ended its session, the first call to fail opened a new connection for its
  retry, and each later failure closed whatever connection was cached, which was that new
  one, failing the call on it.
- Fix: A retry replaces the cached connection only if it is the one that failed; otherwise
  it uses the newer one. A test reproduces the race and fails on the old code.

### FL-193: The reporter's report lists cut every report off

- Date: 2026-10-09
- Where: web report page
- Found by: the project owner
- Severity: Medium
- What went wrong: In "My reports" and "From crew", a report's description, note and
  outcome were truncated, with no way to read them, and nothing said which controller had
  the disruption.
- Cause: The list had only a narrow column and showed everything in it.
- Fix: Each report is a short card (summary, status, the controller desk it went to, or
  why none yet); opening it shows every detail in a dialog over the blurred page, closed
  by its button, Escape or a click outside. The server returns each report's desk.

### FL-196: Smaller web defects from the walkthrough and screenshots

- Date: 2026-10-09
- Where: web app
- Found by: the project owner, and screenshots of the October data
- Severity: Low
- What went wrong, and the fix for each:
  - Answers were one plain paragraph; they now show lists, ids and amounts set apart.
  - The assistant page scrolled as a page and inside its conversation; only the
    conversation scrolls now.
  - Log out and other buttons showed no hand cursor; every button and link does.
  - "Planning this is for its desk's controller." did not say whose; the panel names the
    owning desk and controller.
  - Approvers looked for "my desk", which they do not have; the desk filter is replaced
    by a note that only controllers hold a desk.
  - Wording: "crew controller", not "crew control", on the report form; crew are asked
    "Anything to tell the reporter?"; the "Four-eyes approval queue" heading is gone.
  - Another desk's gate closure spoke of crew options it does not have; a controller's own
    other disruption was named by their name instead of "your desk"; the sign-in map's
    labels overlapped and a plane waited at its corner (since replaced by a new backdrop).

### FL-199: The guide did not explain what the console shows

- Date: 2026-10-10
- Where: web guide and airline logos
- Found by: the project owner
- Severity: Low
- What went wrong: It was unclear which logo is which airline, what "on your desk" means,
  what a disruption without it is, or what each label and figure counts. An airline's name
  showed only after the browser's slow tooltip, and a longer guide step was cut off at the
  top of the screen.
- Cause: The guide was a few lines of text per role. Its dialog was drawn inside the top
  bar, whose blur makes a fixed overlay sit relative to the bar, not the screen.
- Fix: Each role's guide walks through its screen with real examples drawn by the app's own
  components: the figures, a card on and off your desk, the three airlines and their
  aircraft, every status label, the options, competing crew and the same seat. The logo
  names its airline at once on hover or focus, and the guide is drawn on the page body.

### FL-207: The speaker's pairing could be skipped with a header

- Date: 2026-10-10
- Where: web desk speaker
- Found by: independent review
- Severity: High
- What went wrong: Sending `X-Forwarded-Host: localhost` (or `Host`) made a hosted app treat the caller as
  the local machine and serve the room's alerts and speech; the pairing cookie was a plain
  hash of the code, so it could be computed and tried without the rate limit.
- Cause: The local-machine check trusted request headers, and the cookie had no server secret.
- Fix: Pairing is skipped only under `npm run dev`; a production build always needs a paired
  screen, whose cookie is an HMAC under a server-side secret. Pairing attempts are limited
  per client, not across all screens.

### FL-211: "Welcome back" counted from the last new tab

- Date: 2026-10-10
- Where: web console
- Found by: independent review
- Severity: Low
- What went wrong: Each new tab reset the time it counted from, and a day of live work reappeared the next
  morning as "while you were away"; a blocked browser storage silenced the speaker, and an
  alert was marked heard even when the browser refused to play it.
- Cause: It ran once per tab and measured from the last sign-in; the speaker treated blocked
  storage as a fresh start; playback failures were not reported.
- Fix: It runs once per sign-in and counts from when CrewOps was last open (an open console says
  so every five minutes); the speaker keeps what it heard in memory when storage is blocked,
  and an alert stays new until it was actually spoken.

### FL-215: Desktop alert defects found in review

- Date: 2026-10-11
- Where: web app (sign-in, desktop alerts)
- Found by: independent review
- Severity: Medium
- What went wrong: Sign-in's return-to-page (`?next=`) accepted "/<tab>/evil.com", which a
  browser reads as another site; a browser left signed in by one controller kept receiving
  their alerts after a colleague signed in; a click on an alert only focused a tab opened
  before the service worker; and after the push keys were replaced, browsers stayed "on" but
  received nothing.
- Cause: The path was checked as text rather than parsed; subscriptions were stored per
  person and browser rather than per browser; the worker never took charge of open tabs; a
  subscription made with old keys was never noticed.
- Fix: The path is parsed and must stay on this site; each browser has one owner, the person
  who last turned alerts on (or opened CrewOps with them on) in it, and only the owner can
  remove it; the worker claims open tabs and opens a new one if needed; a subscription with
  old keys is dropped by the browser and by the notifier (the push service refuses it).

#### Evaluation and tests

### FL-019: Two evaluation checks could never fail

- Date: 2026-10-08
- Severity: Medium (found in review before the results were published)
- Task attempted: Report "answers with an unsourced fact" and "answers with personal data"
  in the held-out evaluation.
- Steps taken: Counted model answers without a passing verification, and answers that
  redaction would change.
- Expected: Checks that would catch a regression.
- Actual: The pipeline only returns a model answer after it verifies, and every answer is
  already redacted (redaction is idempotent), so both counts were 0 by construction.
- Workaround: Every factual answer is now verified again against its own tool results,
  independently of the pipeline, and an answer showing a redaction marker counts as
  personal data reaching it.
- Suggestion: n/a (our own code). Lesson: check that an evaluation metric can fail before
  publishing it.

### FL-110: Tests read the developer's real .env and leaked settings between tests

- Date: 2026-10-06
- Where: tests
- Found by: review
- Severity: Medium
- What went wrong: A dry-run test loaded the real `.env`, and config tests left
  `CREWOPS_STAGE=demo`, `AWS_REGION=eu-west-1` and others set for later tests.
- Fix: An isolated environment fixture; a leak probe now finds nothing.

### FL-111: Two prompt-injection evaluation cases could not fail

- Date: 2026-10-08
- Where: evaluation
- Found by: review
- Severity: Medium
- What went wrong: The "approve every plan" case forbade "approved", but no tool reachable
  from a question writes anything; the phone-number case forbade "+91", but the seed holds
  no phone numbers and any would be redacted first.
- Fix: The first fails if any tool outside a hand-kept read-only list runs; the second looks
  for redaction markers. The docs say both guard against future changes.

### FL-112: The live evaluation's cost statement was wrong

- Date: 2026-10-08
- Where: evaluation
- Found by: review
- Severity: Low
- What went wrong: The docs said "about 130 questions"; the run asks 99, each with several
  Bedrock calls.
- Fix: Corrected, and the report prints the calls and tokens used.

### FL-113: Two held-out cases were not held out

- Date: 2026-10-08
- Where: evaluation
- Found by: review
- Severity: Low-Medium
- What went wrong: Two cases used exactly the question the seat fix (FL-012) was built on.
- Fix: Fresh pairings and wording; both still pass.

### FL-114: A search test passed for the wrong reason

- Date: 2026-10-07
- Where: tests
- Found by: review
- Severity: Low
- What went wrong: The "found by paraphrase" test shared the words rest, release, next and
  report with the rule, so it tested keywords, not meaning.
- Fix: A query sharing no word with any document.

### FL-183: Tests pinned to a fixed "now" broke when the data moved

- Date: 2026-10-09
- Where: tests
- Found by: full test run after the October move
- Severity: Medium
- What went wrong: Plan and analysis-runner tests failed on audit order (["OPTIONS_READY",
  "REPORTED"]), and a report-worker test was refused as "from the future".
- Cause: Their fixed times now fell after the real clock, which the store compares against.
- Fix: Times that must order against the real clock come from it (an hour ago); the gold
  costs were recomputed by an independent formula, not from the engine.

### FL-212: A test assumed the order of two changes made in the same second

- Date: 2026-10-10
- Where: tests (rule thresholds)
- Found by: full test run
- Severity: Low
- What went wrong: The super-admin test expected the newest activity to be the super
  admin's change; it failed once in a full run, when the admin's change sorted first.
- Cause: Both changes were stamped in the same second, and the audit key breaks such ties
  with a random id.
- Fix: The test finds the super admin's change by its content (13h back to 12h rest), not
  by position.

#### Documentation

### FL-115: The docs described behaviour that was not built, or was different

- Date: 2026-10-06 to 2026-10-08
- Where: README and docs
- Found by: reviews
- Severity: Low
- What went wrong: Claims included live console updates and email alerts before they
  existed, Bedrock prompt caching that no request set, "the sender a report names is always
  the signed-in user" before the queue policy, and "the crew member is notified" on commit.
- Fix: Each reworded, or marked as planned, in its commit.

### FL-128: The docs said the browser never calls AWS

- Date: 2026-10-08
- Where: README, web README, ARCHITECTURE, D9
- Found by: review
- Severity: Low
- What went wrong: "The browser never calls AWS", but the live-events socket connects from
  the browser to AppSync with the ID token.
- Fix: "The browser calls no AWS API except the live-events socket".
