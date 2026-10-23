# Decisions

Short records of the choices that shape this project and what was traded away.

## D1. Python

The rules engine, agents, MCP server and Lambda functions share one language. boto3, the MCP
Python SDK, Strands Agents and the ASK SDK for Python cover every AWS and Alexa surface needed.
The web app is the one exception (D9).

## D2. DynamoDB as the operational store

| | DynamoDB (on-demand) | RDS Postgres | Aurora Serverless v2 |
|---|---|---|---|
| Latency from Lambda | Single-digit ms, no connections | Low ms, needs pooling or RDS Proxy | Low ms warm, ~15 s from zero |
| Fits Alexa's 8 s limit | Yes | Yes, if always on | Only if never at zero |
| Idle cost | $0 | Instance runs all the time | $0 at zero, else minimum capacity |
| Networking | IAM only, no VPC | VPC, security groups | Data API avoids most VPC work |
| Joins and ad-hoc SQL | No | Yes | Yes |
| Change events | Streams → Lambda | Build it yourself | Build it yourself |

The data is relational in shape, but small: 150 crew, 39 pairings, one operating week. The
rules engine loads what it needs with a few key lookups and evaluates in memory, so joins are
not needed at query time. Streams give the event-driven disruption analysis for free.

Traded away: ad-hoc SQL. Every access pattern is designed up front.

Not chosen: Aurora DSQL (no foreign keys, new), DocumentDB (VPC and a running instance),
Neptune (overkill), ElastiCache or MemoryDB (caches, not a system of record).

## D3. S3 Vectors with Titan Text Embeddings V2

Semantic search covers two things: finding a rule from a paraphrased question, and matching an
unusual question to a known intent. Exact lookups by crew, pairing or rule id stay in DynamoDB.

| Option | Idle cost | Verdict |
|---|---|---|
| S3 Vectors | Cents | Chosen |
| OpenSearch Serverless | High minimum | Too expensive idle |
| Aurora + pgvector | See D2 | Only if Aurora held everything |

Embeddings are 512-dimensional: enough for short rule texts and questions, at half the storage
of 1024.

Search is hybrid: S3 Vectors provides semantic similarity, and BM25 keyword scoring runs in
Python over the same documents (each index is under a hundred documents, so a query sees all
of them). Keywords catch exact terms such as rule ids and flight numbers that embeddings blur;
embeddings catch paraphrases that share no words. The scenario index returns only the kind of
disruption, never a past answer, so stale crew or costs can never leak into a live answer.

## D4. No Redis and no semantic answer cache

A cache would need a VPC and NAT for no latency gain, since DynamoDB already answers in
milliseconds and model calls take seconds. A semantic answer cache is dangerous here:
"C-1042 sick on P-2291" and "C-1024 sick on P-2219" embed almost identically, so a cached
answer could send the wrong person to the wrong aircraft. If caching is added, it will be
Bedrock prompt caching of the fixed prompt and tool definitions, never of answers.

## D5. AgentCore Runtime for the agent and MCP server

AgentCore Runtime hosts MCP servers over Streamable HTTP, validates Cognito tokens, and bills
only for active compute. Memory and Observability come with it. Lambda runs the short,
event-triggered jobs: the SQS worker, the roster loader and the disruption analysis.

The MCP server is a FastMCP server (the official MCP Python SDK) in stateless streamable
HTTP mode, deployed as an AgentCore code package rather than a container, so no Docker is
needed. AgentCore's JWT authorizer admits only the team pool's access tokens; the server
verifies the token again and acts as that user.

The advisor's tool loop is a short Converse loop in this repository rather than an agent
framework. Every tool call it makes goes through the caller's role checks, the round limit
and a time check before each model call, and its answer through the verifier.

Fallback: the MCP server is stateless between requests, so it can move to Lambda behind a
Function URL without code changes if AgentCore blocks progress.

## D6. Models: NVIDIA Nemotron on Nebius Token Factory

The rules engine does the hard reasoning; the model only chooses tools and phrases answers,
and the verifier rejects anything it did not get from a tool. NVIDIA
Llama-3.1-Nemotron-70B-Instruct, served by Nebius Token Factory, runs all three roles:
triage, the advisor's tool loop and the explainer. It is an open model tuned for helpful,
instruction-following answers, and Token Factory serves it behind an OpenAI-compatible API
with function calling, so no GPU of our own is needed.

The roles speak Bedrock Converse's message shapes (content blocks, tool use and tool
results, stop reasons, usage). A small client (`crewops.agent.nebius`) translates them to
chat completions, so the tool loop, stop-reason checks, breaker and fallbacks are
independent of the provider, and every model is a setting, not code.

Traded away: a second provider to run and pay for, and a network hop out of AWS on the voice
path. Each role keeps its firm timeout and its template fallback, and Token Factory has its
own breaker, so its outage costs fluency, never an answer. Other Token Factory models
(a larger Nemotron for the advisor, a smaller one for triage) can be tried by changing
configuration and re-running the evaluation.

## D7. Most answers need no model call to route

Ordered regex rules route the common question shapes with zero model calls, and an embedding
match catches paraphrases. The triage model is reached only when both abstain. This cuts
latency on the voice path and makes routing testable.

## D8. A deterministic verifier between the advisor and the user

Every id, number, name and rule verdict in a draft must appear in a tool result from the same
turn. A failing draft is discarded and a deterministic template is spoken instead. The
explainer's rewrite is verified again. Guardrails cannot do this job: they do not know what a
sourced number is.

## D9. Next.js web app as the main Alexa+ experience

No public path connects a self-hosted MCP server to an Alexa+ device, and the rules accept a
simulated Alexa+ experience in a web app. The web app (Next.js, TypeScript, Tailwind, a
handful of its own components rather than a component library, to be hosted on Amplify)
gives the Alexa+ simulator, the controller and approver console, an admin page and a mobile
crew view. It is a backend for frontend: apart from the live-events socket, the browser
talks only to the app's server routes, which call the MCP server as the signed-in user, so
the web app holds no rule of its own. A classic Alexa custom skill, tested in the Alexa
Developer Console, adds real Alexa voice as a second channel. A standard MCP host proves the
server follows the spec.

## D10. Real-time through events, not polling

Reports go to SQS (with a dead-letter queue), a worker stores them, and DynamoDB Streams wake
the disruption analysis. AppSync Events pushes updates to the console over WebSockets. SNS is
used only for optional email alerts that the admin assigns to roles; nothing depends on them.
Kinesis is unnecessary at this volume. One notifier on the stream feeds both, so whatever
stores a change (the worker, the analysis, a plan action) is reported the same way.

Conversation memory is deliberately narrow: AgentCore Memory keeps each conversation's
previous turn, and only a short reply or a pronoun may borrow from it, with a note on the
answer. Long-term extraction is not used: a remembered "fact" about a roster goes stale
within hours, and pending work is always read from the table instead.

## D11. Agents recommend; people decide, with four eyes

No agent can change the roster. A controller submits one plan, a different person with the
approver role approves it, and only approval commits every assignment in one DynamoDB
transaction. Legality is re-checked at approval, because the roster may have changed while the
plan waited. Admins configure the system but cannot operate it unless they also hold an
operational role. All of this is enforced in code in the MCP server, never by a prompt.

Traded away: speed on routine cover. One extra human step is accepted because a crew change
moves a real person and is costly to undo. An admin-set auto-approval threshold for low-cost
reserve callouts is a possible later addition, not part of the first build.

## D12. Infrastructure as code from the first AWS resource

Every AWS resource is defined in a Python CDK app from the commit that first needs it, and
deployed as it is built. Problems with a service surface the day it is added, not in the last
week. CDK's high-level constructs cover DynamoDB, SQS with dead-letter queues, SNS, Lambda
event sources, Cognito, AppSync Events and AgentCore; S3 Vectors and Bedrock Guardrails use
the lower-level CloudFormation constructs.

## D13. Deploy to the project's home region, Sydney

The AWS account this runs in is a project in AWS's new account experience. AWS-managed
policies pin each project to one home region and deny almost every service elsewhere.
This project's home region is Asia Pacific (Sydney), and everything the system uses is
available there: DynamoDB, SQS, SNS, Lambda, S3 Vectors, AgentCore, and Bedrock with Titan
embeddings and Guardrails. The chat models run on Nebius Token Factory, reached over HTTPS,
so they do not depend on the AWS region.

Not chosen: activating the account's advanced features to lift the region policy. It is
irreversible and removes the spend limit, for no gain this project needs.

## D14. Tools answer or ask; they never guess

Every tool resolves what the user named against the real data. An id that does not exist is
an error listing the nearest real ids, never a silent correction: C-1042 and C-1024 differ by
one transposed digit, and acting on the wrong one sends a different person to an aircraft. A
name or flight number with several matches is a question naming them. Who may call which
tool is decided in code from the caller's verified roles, and the model is only shown the
tools its caller may use.

Traded away: a turn or two of back-and-forth when the user was vague. A wrong confident
answer costs far more than one clarifying question.

## D15. Inference on Nebius, everything else on AWS

Only the language-model calls run on Nebius Token Factory. The operation (DynamoDB),
search (S3 Vectors with Titan embeddings), safety screening (Bedrock Guardrails),
conversation memory and hosting (AgentCore), sign-in (Cognito) and every event and alert
path stay on AWS. Embeddings stay with Titan because the vector indexes were built with it,
and a query must be embedded by the model that built its index. The Token Factory key is a
SecureString in Parameter Store that only the MCP server's and the Alexa skill's roles may
read; it never appears in a deployed service's environment, a stack template, or the
deploy tools' environment (`scripts/cdk.sh` leaves it out).

Not chosen: moving the embeddings to a Token Factory embedding model at the same time. It
would mean re-indexing and re-calibrating the router's similarity floor for no gain the
answers need.
