# Friction log

Problems hit while building CrewOps's model layer on Nebius Token Factory with NVIDIA
Nemotron, each written when it happened, with the real error text and figures.

Entries FL-1 to FL-4 are about Token Factory and the model. FL-5 and FL-6 are bugs in our
own code that only showed up with the model in the loop, found by live runs, and how each
was fixed.

Severity: Blocker (no way forward without a workaround), High (cost hours), Medium (cost
under an hour), Low (annoyance). For our own code, severity is the harm the bug would have
done had it shipped, and High means a wrong answer, a security gap or lost data.

## Template

```
### FL-N: <short title>

- Date:
- Service / tool:
- Severity:
- Task attempted:
- Steps taken:
- Expected:
- Actual: (exact error text)
- Workaround:
- Suggestion: (for the service's or the model's maintainers)
```

Entries about our own code use a shorter form:

```
### FL-N: <short title>

- Date:
- Where: (the part of the system)
- Found by: (review, failing test, live run)
- Severity:
- What went wrong: (the exact wrong output or error)
- Cause:
- Fix:
```

## Nebius Token Factory and NVIDIA Nemotron

### FL-1: The model we planned on was not served, and the docs did not say so

- Date: 2026-10-13, resolved 2026-10-27
- Service / tool: Nebius Token Factory documentation and model catalogue
- Severity: Medium
- Task attempted: Point the three model roles at NVIDIA Llama-3.1-Nemotron-70B-Instruct on
  Token Factory, with function calling for the advisor.
- Steps taken: Read the Token Factory API reference and quickstart; once a key existed, ran
  `python -m crewops.agent.nebius`, which compares the configured models with the key's
  catalogue (`GET /v1/models`).
- Expected: The served model ids, and which of them accept `tools`, listed in the docs.
- Actual: The API reference shows the base URL (`https://api.tokenfactory.nebius.com/v1/`)
  and Llama example ids only. The catalogue check printed `NOT SERVED` for the 70B model
  and listed five other NVIDIA models: Nemotron 3 Nano, 3.5 Lightning, 3 Super, 3.5 Super
  VL and 3 Ultra.
- Workaround: `GET /v1/models?verbose=true` gives each model's price, context length and
  features (all five support tools). Measured on the real advisor loop, Nemotron 3.5
  Lightning was the cheapest and fastest; it is now the default for all three roles.
- Suggestion: A model catalogue page in the docs with each id, price and supported
  features, the same as the verbose endpoint returns.

### FL-2: Nemotron 3 models think by default, and the switch is undocumented

- Date: 2026-10-27
- Service / tool: Nebius Token Factory, Nemotron 3 Nano and 3.5 Lightning
- Severity: Medium
- Task attempted: Get a short, direct reply within the voice budget, and keep triage within
  its 100-token reply limit.
- Steps taken: Sent "Reply with the single word OK." three ways: as it is, with
  `/no_think` in the system prompt, and with `chat_template_kwargs: {"enable_thinking":
  false}`.
- Expected: A two-token reply; or, if thinking is on, a documented way to turn it off.
- Actual: As it is, the reply cost 146 output tokens (the thinking comes back in a separate
  field and is billed) and took up to 8 seconds. `/no_think` changed nothing.
  `enable_thinking: false` gave 2 tokens in 0.8 seconds, but it is not in the model's
  `supported_sampling_parameters`.
- Workaround: Every request sends `enable_thinking: false`, unless
  `CREWOPS_NEBIUS_REASONING=on`.
- Suggestion: List the thinking switch with each model's parameters, and say whether thinking
  is on by default.

### FL-3: The advisor gathered instead of answering, and once wrote a tool call as text

- Date: 2026-10-27
- Service / tool: Nemotron 3.5 Lightning in the advisor's tool loop
- Severity: Medium
- Task attempted: Answer "Which single flight leg has the most seats at risk if cancelled,
  and why?" within the six-second budget.
- Steps taken: Traced each round's tool calls and token counts.
- Expected: One sorted lookup, then an answer.
- Actual: The model paged through flights a day at a time (110,000 input tokens over six
  calls, no answer). With a rule asking for sort and limit, it made one sorted lookup but
  then kept calling `ripple` for each aircraft until the round limit. When the last round
  was sent with `tool_choice: "none"`, it once replied with its own `<tool_call>` syntax as
  text.
- Workaround: A rule in the advisor's instructions to keep calls few and narrow; a last
  round with `tool_choice: "none"` and an explicit "answer now"; a reply whose text holds
  tool-call syntax counts as no answer. The same question then got a written answer in 5.6
  seconds for about $0.002 (the verifier still holds back any claim no tool result
  supports).
- Suggestion: Honour `tool_choice: "none"` in the chat template, so the model is never asked
  for a call it may not make.

### FL-4: The hackathon code and the credit code went in different places, after a card

- Date: 2026-10-23, resolved 2026-10-27
- Service / tool: Nebius Token Factory billing and promo codes, the hackathon sign-up
- Severity: Low
- Task attempted: Get the hackathon's Token Factory credits onto the account, then a key to
  spend them.
- Steps taken: Registered for the hackathon with the code `NEBIUS-DEVPOST-GLOBAL26`. Then
  received, by email, a separate $25 Token Factory promo code, valid until 31/12/2026.
  Tried the hackathon code in the console's promo-code field first.
- Expected: One code, applied in one place, and promo credits usable without a payment card.
- Actual: The console refused `NEBIUS-DEVPOST-GLOBAL26` as an invalid code: it belongs to the
  registration form, not the console. The emailed code worked under Balance → Top up →
  With promo code, but the console asked for a payment card first, and the billing docs
  confirm a card is required before any credits.
- Workaround: Added a card, then applied the emailed code; $25 showed on the balance.
- Suggestion: Say on the hackathon page and in the email which code goes where, and that a
  card is needed before promo credits apply.

## Our own code, found with the model in the loop

### FL-5: Off-topic questions were answered with crew data

- Date: 2026-10-27
- Where: the router's triage (Nemotron 3.5 Lightning, one JSON intent, no tools) and the
  question pipeline
- Found by: live run
- Severity: Medium
- What went wrong: "Translate good morning into Hindi." came back as a list of crew risk
  scores, routed `LOOKUP_RISK via triage`. With that fixed, the right reply came back but
  still cost two model calls and 6,012 input tokens, with the note "model draft rejected: 0
  unsourced claim(s) ()".
- Cause: The triage instructions listed only crew-control intents, so the model had to pick
  one. Then, because no tool had answered, the pipeline handed the question to the advisor
  anyway; its draft cited no tool result, and the note did not say so.
- Fix: Triage may answer `NONE` ("not about crew control"), which gives the scope reply
  ("I can help with crew control: ...") with no tool and no advisor call: one call, 239
  input tokens. A draft that uses no tool result now says exactly that in its note.

### FL-6: A role refusal went to the advisor, which tried other tools

- Date: 2026-10-28
- Where: the question pipeline, with Nemotron as the advisor
- Found by: live evaluation (two role checks failed live and passed offline)
- Severity: Medium
- What went wrong: A crew member asking "How many duty hours does C-3310 have?" got a long
  refusal written by the model instead of "That isn't available to your role." A reporter
  asking "Rank the cover options for C-1042 on P-2291." got rule text and a raw tool error
  in the answer: `search_rules.top_k: must be at most 7`.
- Cause: When the caller's role could run none of the planned calls, or every call it ran
  was refused, the pipeline still asked the advisor, which worked around the refusal with
  the tools the role did have (one called with a `top_k` above the tool's maximum).
- Fix: Such a question gets the role refusal without a model call. The next live run passed
  every role check (4 of 4) and cost $0.0095 in Token Factory calls for the whole set.
