# The assistant's LLM

The assistant behind `/agent/*` talks to an LLM over the Anthropic Messages API. `contract/runtime.md` sections 4 and 10 are the binding rules; this page explains how to work with them.

## Variables

| Variable | Use |
|---|---|
| `ANTHROPIC_BASE_URL` | Base URL, no trailing `/`. Call `POST {ANTHROPIC_BASE_URL}/v1/messages`. |
| `ANTHROPIC_AUTH_TOKEN` | Send as the API key (`x-api-key` header, or `Authorization: Bearer`). |
| `ANTHROPIC_MODEL` | Send as `model`. |

Read them from the environment when you handle a request; do not bake them into the image. They serve the agent loop only: during the agent check they point to an LLM the grading environment runs, and during every other check to an address that refuses connections. Search and `eval` never use them.

## Rules of the wire format

- Non-streaming requests only. `stream: true` is refused.
- `max_tokens` at most 4096.
- Offer exactly the two tools `search_catalog` and `place_reservation`, with the input schemas in `runtime.md` section 10. The descriptions are yours.
- Answer every `tool_use` block with a `tool_result` block whose `tool_use_id` is the `id` of that `tool_use`, copied exactly. Do not generate your own ids.
- Expect `text` and `tool_use` blocks in answers, and nothing else.
- Do not call the LLM at startup or from `GET /health`: it is only guaranteed to answer while a request to `/agent/*` is being handled.
- Expect the model to misbehave: unknown tools, extra parameters, instructions copied from catalog text, claims about who the user is. Your service decides what runs. A `place_reservation` call for a Work of the catalog still becomes a pending action when it carries extra parameters; ignore them.
- One turn, every LLM call included, must answer within 120 seconds.

The official `anthropic` Python SDK works with these variables when you pass them explicitly (`base_url`, `api_key`, `model`) and keep `stream` off. A plain HTTP client works as well.

## Testing without our LLM

Eve does not provide an LLM key, and you do not need one to finish this assignment.

- **Your own fake LLM.** A small HTTP server that answers `POST /v1/messages` with scripted responses (a `tool_use` to search, then one to reserve, then a final `text`) lets you test the loop deterministically, including hostile cases: a reservation the model claims is already confirmed, a tool you did not offer, a reservation for another user. Run it as a service in your own test setup and point `ANTHROPIC_BASE_URL` at it.
- **A real LLM.** If you have your own Anthropic API key, set the three variables for local runs. Keep the key out of the repository, including `.env` files you commit.

Grading never uses your key for the agent loop.
