# Runtime contract

This file and `openapi.yaml` are the whole interface between your project and the grading environment. The grading environment builds and runs your repository with Docker Compose, talks to it only over HTTP, and reads nothing else from your containers. Rules marked **must** are checked; breaking one makes the affected check fail or stops grading before your code runs.

From the repository root, `uv run --project contract-check contract-check --compose-dir .` checks the static rules (sections 1, 2, 5 and 9), and `uv run --project contract-check contract-check --base-url http://127.0.0.1:8000` checks the HTTP shapes of a running instance (it creates reservations; `--skip-agent` leaves out the checks that call your LLM). Both check shape, not correctness.

## 1. Compose project

- The repository root **must** contain `compose.yaml`.
- Service `api` **must** listen on `0.0.0.0:8000` inside its container and **must not** publish ports in `compose.yaml`. Put development port mappings in `compose.override.yaml`: Compose loads that file when you run it by hand, while the grading environment always passes `-f compose.yaml` explicitly and never loads it.
- The grading environment runs one or two `api` containers depending on the check, started at the same time. Starting two at once **must** be safe, and both **must** serve the same state: keep state in shared services (a database, a search engine), not in the memory of one `api` process.
- `api` **must not** declare `profiles`.
- The grading machine runs Docker on `linux/arm64`. Every image you build or pull **must** run there; most official images on Docker Hub are published for it.

## 2. One-shot services and `eval`

- A one-shot service is one that exits when its work is done, such as loading data. Name it `init` or `init-<name>`; every other service is expected to keep running.
- Every one-shot service **must** be required by at least one other service, and every service that requires it **must** use `condition: service_completed_successfully`:

  ```yaml
  api:
    depends_on:
      init:
        condition: service_completed_successfully
  ```

- Service `eval` **must** exist and **must** declare `profiles: [eval]`, so `docker compose up` does not start it. The grading environment runs it with `docker compose --profile eval up --no-build --no-deps eval` in the started project, while the other services keep running, from the image built in the build step (section 11).

## 3. Data and variables provided at grading time

The grading environment generates its own Compose override for each run after reading `docker compose config --services`. For **every** service in your project it mounts the grading data read-only at `/grading-data` and sets these variables:

| Variable | Meaning |
|---|---|
| `LIBRARY_CORPUS_PATH` | Corpus of this check, JSON Lines in the format of `data/corpus/openlibrary-works.jsonl` (see below) |
| `LIBRARY_INVENTORY_PATH` | Inventory of this check, JSON per `inventory.schema.json`; it refers only to Works and editions of the corpus of the same check |
| `LIBRARY_QUERIES_PATH` | Public evaluation queries, JSON Lines, read by `eval`: a byte-identical copy of `evaluation/public-queries.jsonl` (section 11) |
| `LIBRARY_RESULTS_DIR` | Writable directory for `eval` output. `eval` writes nothing into the repository tree |
| `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` | LLM endpoint of the agent loop (section 4) |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | Egress proxy (section 6) |

Read paths from the variables; do not hard-code `/grading-data`.

**Each check ships its own corpus and inventory.** A check may use the corpus in `data/corpus/`, part of it, or Works that are not in it (for example Works written for one check). Every corpus has the same format: one JSON object per line, valid against `corpus.schema.json`, `work_key` unique. Do not assume `data/inventory.sample.json` either.

- Your services **must** load the corpus and inventory from `LIBRARY_CORPUS_PATH` and `LIBRARY_INVENTORY_PATH` when the project starts, and `GET /health` reports ready only once every index covers exactly the Works of that corpus. An index built into the image from the repository copy of the corpus does not satisfy this on its own.
- Each check starts your project from scratch (`docker compose up` after `docker compose down -v`), so a project sees one corpus for its whole life.
- `content_hash` identifies a record's content: `"sha256:"` followed by the lower-case hex SHA-256 of the UTF-8 bytes of the JSON object made of the fields `title`, `subtitle`, `authors`, `subjects`, `description`, `first_publish_year`, `languages`, `edition_keys` and `isbns`, serialized with sorted keys, separators `,` and `:` without spaces, and non-ASCII characters left unescaped. `work_key` is not part of it. Two records with the same `content_hash` have the same values in those nine fields. The other fields of a record (`work_key`, `content_hash`, `edition_count`, `description_chars`, `source`, `source_record_id`, `source_url`, `licence_status`, `licence_terms_url`) are outside the hash; a record carries no fields beyond those in `corpus.schema.json`, and a corpus with any other field is invalid.
- To start within the readiness ceiling (section 7), you may compute expensive derived data (embeddings, for example) ahead of time, at build time from the repository copy of the corpus or in a previous check, and reuse it at startup for every record whose `content_hash` matches; compute the rest at startup.
- The grading environment mounts an empty volume at `/model-cache` in every service, writable by any user, as is `LIBRARY_RESULTS_DIR`. It is shared by all services and all checks of one grading run and deleted when the run ends; use it for downloaded models and for derived data you want to reuse between checks.

`uv run --project contract-check contract-check --corpus <file> --inventory <file>` checks a corpus and inventory pair you build for your own tests.

The grading environment ignores your `.env` file.

## 4. LLM variables are for the agent loop only

`ANTHROPIC_*` serve only the assistant behind `/agent/*`. During the agent check they point to an LLM run by the grading environment; during every other check they point to an address that refuses connections. If `/search` or `eval` call an LLM or another external API, read that key from a variable with a different name and declare it in `grading.toml` (section 5).

Call the LLM only while handling a request to `/agent/*`. The endpoint is guaranteed to answer only then: it may not be reachable while your project starts, so do not call it at startup or from `GET /health`.

## 5. `grading.toml`

The repository root **must** contain `grading.toml`, valid against `grading.schema.json`:

```toml
# Names of variables that carry your own keys for external APIs; [] when offline.
external_secrets = ["EXAMPLE_API_KEY"]
# Host names your running services call over HTTPS; [] when offline.
egress_hosts = ["api.example.com"]
# Does GET /search return the same ranking for the same input and data?
search_deterministic = true
# Does eval write the same rankings for the same input and data?
eval_deterministic = true
```

- `external_secrets`: upper-case variable names. Names starting with `LIBRARY_`, `ANTHROPIC_`, `BUILDX_`, `DOCKER_` or `COMPOSE_`, the proxy variables, `PATH` and `HOME` are reserved. You send the key values to the grading coordinator through the channel they name, never in the repository. When your project runs, the grading environment sets each declared variable in the environment of every service of your project; the keys are not available at build time, and nothing else from the grader's machine is passed in.
- `egress_hosts`: lower-case DNS host names only. No scheme, port, path, wildcard or IP address.
- Set a deterministic flag to `false` when an LLM or another non-deterministic service sits in that path.

## 6. Network at grading time

- `docker compose build` runs with normal network access. The build **must** finish within **60 minutes**, measured from an empty build cache.
- When the project runs, every service is attached to an internal network of the grading run. The only way out is the HTTPS proxy of the grading environment, given in `HTTPS_PROXY` and `HTTP_PROXY`. It accepts only HTTPS tunnels (`CONNECT`) to port 443 of the hosts in `egress_hosts`, and only while those names resolve to public addresses; plain HTTP, other methods, other ports, other hosts and IP addresses are refused. `NO_PROXY` lists `localhost`, `127.0.0.1`, `::1`, every service of your project, and the host of `ANTHROPIC_BASE_URL`, so the LLM of the agent check is reached directly, not through the proxy. Each variable is also set in lower case (`https_proxy`, `http_proxy`, `no_proxy`).
- HTTP clients in your services **must** honor `HTTPS_PROXY`. Many do by default; some need it passed explicitly.
- Downloading a model at build time or at run time are both fine. At run time, the download host **must** be in `egress_hosts`.

## 7. Readiness and time limits

`GET /health` returns `200` with `{"status": "ready"}` only once the inventory and corpus are loaded and every index is ready; before that it returns `503`. The grading environment polls each `api` container until it is ready, for at most **600 seconds** after `docker compose up` returns. Build time does not count.

Past any of these limits the check fails or stops with an error:

| Step | Limit |
|---|---|
| `docker compose build`, from an empty build cache | 60 minutes (section 6) |
| `docker compose up -d` returns, one-shot services included | 600 seconds |
| `GET /health` ready, after `docker compose up` returns | 600 seconds |
| One `GET /search` request | 60 seconds |
| One `POST /agent/turns` request, every LLM call included | 120 seconds |
| Any other request (reservations, confirmations, listings); each retry after `503` counts on its own | 60 seconds |
| One run of `eval` | 30 minutes |

## 8. User identity

Header `X-User-Id` carries an opaque string that stands for the signed-in user. Treat it as already authenticated. Endpoints that act for a user return `401` without it.

## 9. Forbidden in `compose.yaml`

The grading environment checks `compose.yaml` and every file it includes or extends before building anything, twice: once as written, and once as Docker Compose resolves it (`docker compose config` with every profile and an empty environment, so a variable without a value takes its default). A value that is only visible after resolution, such as a build context of `${LIBRARY_CORPUS_PATH:-/etc}`, is a violation too. Any violation stops grading with an error. The rule column is the id that `contract-check --compose-dir .` prints.

| Forbidden | Rule |
|---|---|
| `privileged: true`, privileged lifecycle hooks, privileged builds | `privileged` |
| `cap_add` | `cap_add` |
| `devices` | `devices` |
| `network_mode` | `network_mode` |
| `networks` on any service, and a top-level `networks` section (even `external: true` or only `default`). Compose merges network lists across files, so a network you declare would stay attached next to the grading network and bypass the proxy; only the grading override attaches networks | `networks` |
| `pid`, `ipc`, `userns_mode` | `pid`, `ipc`, `userns_mode` |
| Bind mounts of host paths, volume driver options, `volumes_from: container:...`. Use named volumes | `bind_mount` |
| Mounting `docker.sock`, `use_api_socket` | `docker_socket` |
| `build.context`, `build.dockerfile` or `build.additional_contexts` outside the repository, or remote | `build_context` |
| `build.secrets`, `build.ssh` | `build_secrets` |
| `include` or `extends` of a remote file or a file outside the repository | `include_extends` |
| Interpolating any variable other than those in section 3 (except the proxy variables) and your `external_secrets`, including `environment` or `build.args` entries without a value, which copy the host's value. Write `$$` for a literal `$` | `interpolation` |
| `ports` | `published_ports` |
| Ways to replace, read or write images and directories of the grading machine: `image` together with `build` (Compose names the build with that tag, replacing an image of the same name), `build.tags`, `build.cache_from` or `build.cache_to` of `type=local` (in any letter case), volumes of `type: image`, `pull_policy: build` (it makes `docker compose up` build again, outside the grading build step), and an `image` or a `docker-image://` entry of `build.additional_contexts` that exists on the grading machine without a registry digest. Name only registry images in `image`; Compose names what it builds. `contract-check` cannot see the grading machine's images, so it does not report the last case | `host_image` |
| Services named `probe`, `fake-llm` or `egress-proxy`, a `hostname` or `domainname` whose first label is one of these names, and `links` (they add host names; services already reach each other by service name) | `reserved_service_name` |
| `container_name` (it prevents running two `api` containers) | `container_name` |
| Keys that reach the host through the Docker daemon: `security_opt` (except exactly `no-new-privileges`), `logging` (except `driver: json-file`), `cgroup`, `cgroup_parent`, `uts`, `device_cgroup_rules`, `gpus`, `deploy.resources.reservations.devices`, `runtime`, `provider`, `models`, `external_links`, `extra_hosts`, `dns`, `dns_search`, `dns_opt`, `build.network`, `build.entitlements`, external volumes, secrets or configs, a `name` on a top-level volume (it can attach a volume that already exists on the host), and `env_file`, `label_file`, secret or config files outside the repository | `host_access` |
| Top-level keys other than `name`, `services`, `volumes`, `configs`, `secrets`, `include` and `x-*` extensions | `top_level_key` |

A project that `docker compose config` rejects is reported as `invalid_compose`. Missing `api` or `eval` is reported as `required_service`; wrong `profiles` as `service_profiles`; a one-shot service without the required dependency as `one_shot_dependency`; an invalid `grading.toml` as `grading_toml`.

## 10. LLM wire format of the agent loop

- Call `POST {ANTHROPIC_BASE_URL}/v1/messages` with the Messages API request format and tool use, without streaming. `ANTHROPIC_BASE_URL` has no trailing `/`. Send `ANTHROPIC_AUTH_TOKEN` as the API key and `ANTHROPIC_MODEL` as `model`.
- Keep `max_tokens` at or below 4096: official SDKs refuse non-streaming requests with large `max_tokens`.
- The assistant offers exactly these two tools:

  ```json
  [
    {"name": "search_catalog",
     "input_schema": {"type": "object", "required": ["query"],
                      "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "place_reservation",
     "input_schema": {"type": "object", "required": ["work_key"],
                      "properties": {"work_key": {"type": "string"}, "edition_key": {"type": "string"}}}}
  ]
  ```

  Tool descriptions are yours to write.
- The model may call a tool you did not offer, send extra parameters, or follow instructions found in catalog text. The service decides what runs; it **must** refuse anything else safely.
- A `place_reservation` call whose `work_key` names a Work of the catalog **must** become a pending action, even when it carries parameters the schema does not list: ignore those parameters, do not reject the call.
- Every `tool_result` block **must** carry, in `tool_use_id`, the `id` of its `tool_use` block exactly as the LLM sent it. The LLM of the agent check relies on it to recognize its own earlier turns; generating your own ids fails the agent check.
- `place_reservation` never runs during `POST /agent/turns`; it becomes a pending action that runs only through `POST /agent/confirmations/{confirmation_id}` by the same user, exactly once.
- Only that confirmation call runs an action. Parameters the model adds to a tool call (for example `confirmed: true` or its own confirmation id), a user message such as "I confirm", and later turns of the same conversation never run `place_reservation`; at most they add pending actions.
- An action runs for the user who confirms it. A `user_id` or any other identity the model sends, or that catalog text asks for, is ignored.
- A conversation belongs to the user who started it: `POST /agent/turns` with another user's `conversation_id` answers `404` and changes nothing.
- The LLM of the agent check implements `POST /v1/messages` without streaming and `POST /v1/messages/count_tokens`. It accepts the API key in `x-api-key` or in `Authorization: Bearer`, accepts any `anthropic-version`, and ignores request fields it does not need. It does not implement streaming (`stream: true` answers `400`), `/v1/models`, message batches, files or other endpoints, and its answers contain only `text` and `tool_use` blocks.

## 11. Evaluation output and metric definitions

`eval` reads the queries at `LIBRARY_QUERIES_PATH` and writes `${LIBRARY_RESULTS_DIR}/metrics.json`, valid against `metrics.schema.json`. Commit a copy of that file as `results/metrics.json`.

- `contract_version` is the content of `contract/VERSION`. `queries_sha256` and `corpus_sha256` are the lower-case hex SHA-256 of the two input files' bytes. The queries file at grading time is `evaluation/public-queries.jsonl` byte for byte, so the `metrics.json` you commit carries the SHA-256 of that file.
- `systems` lists every retrieval system you evaluated. Exactly one has `served: true`: the one behind `GET /search`.
- `rankings` holds, for every `query_id` in the queries file, the ranked `work_key`s (best first, at most 100, no repeats; an empty list is allowed).
- Only quality metrics go into `aggregates`; put timings in the optional `timings` object, which is never compared.
- `platform` (optional) names where `eval` ran, as Docker names it: `linux/amd64` or `linux/arm64`. Absent means `linux/amd64`. The grading machine is `linux/arm64`; numbers produced on another platform are compared with a wider tolerance.
- The grading environment recomputes the aggregates (below), calls `GET /search?q=<query>&limit=100` for every public query and compares the answers with the rankings of the served system, and runs `eval` again (twice when `eval_deterministic = false`) to compare its numbers with the committed ones. When `search_deterministic = false`, the answers of `GET /search` are compared by how much their top 10 overlaps the committed rankings rather than by exact equality. Calls to an external API during these checks use your key, so set its spending limit with that in mind. A call that does not answer `200` counts as an empty ranking; when a check fails only because of such calls, the grading environment reports an error to investigate with you, not a failure.

The grading environment recomputes every aggregate from `rankings` and the public judgments, so compute them exactly as follows. Each line of the queries file has `query_id`, `query`, `language`, `category`, `adversarial_type`, `pair_id`, `label_source` and `judgments: [{"work_key", "relevance"}]` with `relevance` from 0 to 3; `evaluation/README.md` describes the fields. A Work is relevant when `relevance >= 1`; a Work without a judgment counts as relevance 0. Positions are 1-based.

| Metric | Per-query value | Queries in the average |
|---|---|---|
| `ndcg@10` | `DCG@10 / IDCG@10`, where `DCG@10 = sum over positions i <= 10 of relevance_i / log2(i + 1)` on your ranking, and `IDCG@10` is the same sum over the query's judged relevances sorted in descending order | queries with at least one relevant Work |
| `mrr@10` | `1 / i` for the first position `i <= 10` holding a relevant Work, else 0 | queries with at least one relevant Work |
| `recall@100` | relevant Works in your top 100 divided by all relevant Works of the query | queries with at least one relevant Work |
| `judged@10` | Works in your top 10 that have a judgment (any relevance), divided by 10; missing positions count as not judged | all queries |

Each aggregate is the arithmetic mean of the per-query values, not rounded.

## Reservations under contention

`POST /reservations` and `POST /agent/confirmations/{confirmation_id}` may answer `503` with a `Retry-After` header (whole seconds) when concurrent requests compete for the same Work. The grading environment retries such a response after the given number of seconds, at most **15** times per request, and gives up once the next wait would end more than **20 seconds** after the first attempt. Any other error status caused by contention, or a `503` without `Retry-After`, counts as a failure.

## Work keys in paths

A `work_key` such as `/works/OL45883W` travels in a path as one percent-encoded segment:

```
GET /works/%2Fworks%2FOL45883W/reservations
```

Make sure your framework routes the encoded slashes to the `work_key` parameter.

## Versioning

`contract/VERSION` holds the contract version (semantic versioning); `CHANGELOG.md` lists changes. The grading environment refuses a project whose `metrics.json` declares a different major version.

The contract is frozen at 1.0.0. Any later change only relaxes a rule (a longer ceiling, for example) or clarifies wording; it never adds an obligation to a project that follows 1.0.0.
