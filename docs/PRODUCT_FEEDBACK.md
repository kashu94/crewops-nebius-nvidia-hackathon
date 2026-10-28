# Product feedback

For the submission form's feedback section: Nebius Token Factory and the NVIDIA model
CrewOps uses, what each was used for, what worked, what needs work and how onboarding
went. Every "needs work" point has a full entry (task,
steps, expected, actual, severity, workaround, suggestion) in
[FRICTION_LOG.md](FRICTION_LOG.md), cited as FL-n.

## Summary

| Tool | Used for |
|---|---|
| Nebius Token Factory | Serving the chat models through its OpenAI-compatible API |
| NVIDIA Nemotron 3.5 Lightning | Triage, the tool-calling advisor and the explainer |

## Nebius Token Factory

**Used for.** Serving NVIDIA Nemotron to the three model roles (triage, advisor, explainer)
from the MCP server on AgentCore Runtime and the Alexa skill's Lambda, through the
OpenAI-compatible chat completions API at `https://api.tokenfactory.nebius.com/v1/`, with
function calling for the advisor's read-only tools. The key lives in AWS Parameter Store.

**What worked well.**
- The OpenAI-compatible API meant the roles could keep their message shapes behind a
  translation layer, not a rewrite: one small client (`crewops.agent.nebius`) with plain
  `urllib`, so the MCP package and the Lambda ship no new dependency.
- Standard Bearer-token auth and a `/v1/models` endpoint, which let us add a one-command
  check that a key can use the configured models. With `?verbose=true` it also gives each
  model's price, context length and supported features (tools, reasoning), which made the
  model choice a measurement rather than a guess.
- Turning thinking off with `chat_template_kwargs: {"enable_thinking": false}` works and
  cut a short reply from about 150 billed tokens to 2.
- Cost: our whole live evaluation (58 questions, 66 model calls, about 137,000 input and
  6,500 output tokens) cost $0.0098.

**What needs work.**
- The public API reference names only Llama example models. The model we first planned on
  (Llama-3.1-Nemotron-70B-Instruct) turned out not to be served to our key (FL-1).
- Thinking is on by default for the Nemotron 3 models, and the switch that turns it off is
  not listed in the model's supported parameters (FL-2).
- A `tool_choice: "none"` round did not always stop the model from writing a tool call, as
  text (FL-3).

**Onboarding.** Once we had a key, it worked on the first call with a standard Bearer
header against the OpenAI-compatible endpoint.

## NVIDIA Nemotron 3.5 Lightning

**Used for.** All three model roles. Triage classifies a question the router's rules and
semantic match could not place (JSON intent, no tools). The advisor runs the tool loop
over read-only crew-control tools and drafts an answer. The explainer rewrites verified
answers for speech. Every draft and rewrite is checked by the deterministic verifier
against the tool results before anyone hears it.

**What worked well.**
- Fast and cheap: one to two seconds for a reworded answer, and a $25 credit covers
  thousands of questions. Tool calls were dependable, and triage and the explainer worked
  first time.
- Measured on the held-out evaluation, live against the deployed data: with Nemotron, 32 of
  38 reworded questions were routed right against 25 with the rules alone, and answers
  agreed with the gold answers' facts 0.85 of the time against 0.72. No answer stated an
  unsourced fact and none leaked personal data; every role, injection, follow-up and
  clarifying-question check passed. Half the answers took under 3.2 seconds end to end,
  95% under 8.

**What needs work.**
- Left alone, the advisor gathered rather than concluded: it paged through rows instead of
  using the lookup's sort and limit, and kept calling tools until the round limit. When
  told not to call tools, it once wrote its own tool-call syntax as text (FL-3). A prompt
  rule and a final no-tools round fixed both.
- Given a closed list of intents, triage always picked one, even for an off-topic question;
  it needs an explicit "none" choice to decline. With that choice, it then declined plain
  flight questions too, until the instructions named what counts as the operation (FL-4).
- When the asker's role was refused a lookup, the advisor tried to answer with other tools
  instead of stopping, once with an argument above a tool's stated maximum. We now refuse
  without a model call (FL-5).

## Feature requests

| Request | Why it matters to CrewOps | Priority |
|---|---|---|
| Token Factory: a model catalogue in the docs, with each served id, price and supported features | We could not confirm the Nemotron model id or its tool-calling support before calling the API (FL-1) | Important |
| Document the thinking switch for each model, and whether thinking is on by default | Hidden thinking tokens are billed and push a voice reply past its budget (FL-2) | Important |
| Honour `tool_choice: "none"` in the Nemotron chat template | A final round must answer, never ask for a call it may not make (FL-3) | Important |
