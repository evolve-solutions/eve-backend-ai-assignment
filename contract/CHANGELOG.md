# Contract changelog

## 1.0.0 - 2026-11-22

Contract frozen. Later changes only relax a rule or clarify wording.

- `runtime.md` section 1: the grading machine runs Docker on `linux/arm64`; every image you build or pull must run there.
- `runtime.md` section 6: the build must finish within 60 minutes from an empty build cache (was 20 minutes).
- `runtime.md` section 7 lists every time limit of grading: build 60 minutes, `docker compose up -d` 600 seconds, readiness 600 seconds, one `GET /search` 60 seconds, one `POST /agent/turns` 120 seconds, any other request 60 seconds, one `eval` run 30 minutes.
- `runtime.md` section 10: a `place_reservation` call with a `work_key` of the catalog must become a pending action even when it carries extra parameters.
- `runtime.md` section 3: `/model-cache` and `LIBRARY_RESULTS_DIR` are writable by any user.
- `runtime.md` section 10: every `tool_result` must carry the `tool_use` block's `id` verbatim in `tool_use_id`.
- `runtime.md` section 11: the grading machine is `linux/arm64`, so `metrics.json` produced on `linux/amd64` is compared with the wider tolerance. With `search_deterministic = false`, `GET /search` is compared with the committed rankings by top-10 overlap.
- `runtime.md` section 2: `eval` runs while the other services of the project keep running.
- `openapi.yaml`: where a response lists both `400` and `422` for the same problem, either is accepted.

## 0.7.0 - 2026-11-20

- `metrics.json` may carry an optional `platform` (`linux/amd64` when absent); numbers from another platform than the grading machine are compared with a wider tolerance.
- `runtime.md` section 11 states how the committed numbers are checked: aggregates recomputed from `rankings`, `GET /search?limit=100` compared with the served system, and `eval` run again (twice when `eval_deterministic = false`). A check that fails only because some calls did not answer `200` is reported as an error, not a failure.
- `grading.schema.json` and `runtime.md` section 5 also reserve `external_secrets` names starting with `BUILDX_`, `DOCKER_` or `COMPOSE_`, and `PATH` and `HOME`.
- `runtime.md` section 2: the grading environment runs `eval` with `docker compose --profile eval up --no-build --no-deps eval` instead of `docker compose run --rm eval`, so `eval` never builds; its image comes from the build step.
- `contract-check` runs `docker compose config` with an empty home that links only the caller's Compose and Buildx plugins, so it also works where Docker keeps CLI plugins under `~/.docker/cli-plugins` (Docker Desktop on macOS); before, every project was reported as `invalid_compose` there. Where a plugin exists both there and in a system directory, the copy under `~/.docker/cli-plugins` is used, as in the caller's own shell.
- `runtime.md` section 9 documents a rule that was already enforced: `pull_policy: build` is rejected under `host_image`, because it makes `docker compose up` build again outside the grading build step.

## 0.6.0 - 2026-11-20

- `runtime.md` section 4: the LLM of the agent check is guaranteed to answer only while a request to `/agent/*` is handled, not at startup.
- `runtime.md` section 10 states what never runs an action (model parameters such as `confirmed`, a user writing "I confirm", later turns), that an action runs for the confirming user whatever identity the model sends, that another user's `conversation_id` answers `404`, and which parts of the Messages API the grading LLM implements.
- Static rule `reserved_service_name` also covers `links` and a `hostname` or `domainname` whose first label is a reserved name.

## 0.5.0 - 2026-11-20

- New static rule `host_image`: `image` together with `build`, `build.tags`, `build.cache_from` or `build.cache_to` of `type=local` (in any letter case), volumes of `type: image`, and (checked by the grading environment only) an `image` or a `docker-image://` entry of `build.additional_contexts` that exists on the grading machine without a registry digest.
- `runtime.md` section 6: the build must finish within 20 minutes.

## 0.4.0 - 2026-11-20

- The retry budget for `503` with `Retry-After` under contention is 15 retries within 20 seconds (was 5 within 10). `contract-check` uses the same budget.
- Static rules also run on the configuration `docker compose config` resolves with an empty environment, so a value that only appears after interpolation (for example a variable default that points outside the repository) is a violation. A project that `docker compose config` rejects is reported as `invalid_compose`.
- `runtime.md` section 6: `NO_PROXY` lists `localhost`, `127.0.0.1`, `::1`, every service of the project and the host of `ANTHROPIC_BASE_URL`; the proxy variables are also set in lower case; the proxy refuses hosts that resolve to non-public addresses.

## 0.3.0 - 2026-11-20

- Corpus records lose three fields that described how the corpus was collected, not the Works themselves. `corpus.schema.json` now sets `additionalProperties: false` on a record and on each author, so a corpus with any other field is invalid; `data/corpus/openlibrary-works.jsonl` and `SHA256SUMS` changed accordingly. `content_hash` values did not change.
- `runtime.md` lists the record fields that are outside `content_hash` and states that equal hashes mean equal values in the nine hashed fields.
- The queries file at `LIBRARY_QUERIES_PATH` is a byte-identical copy of `evaluation/public-queries.jsonl`; `runtime.md` section 11 lists the fields of a query line.

## 0.2.0 - 2026-11-20

- Each check ships its own corpus and inventory in the same format. Services load and index the corpus at `LIBRARY_CORPUS_PATH` at startup; derived data computed ahead of time may be reused for records with a matching `content_hash`, whose recipe is now defined. New `corpus.schema.json` and `data_rules.py`; `contract-check --corpus ... --inventory ...` checks a pair.
- The inventory refers to the corpus of the same check, not to `data/corpus/openlibrary-works.jsonl`.
- Static rules also forbid `extra_hosts`, `dns`, `dns_search`, `dns_opt`, device reservations under `deploy`, and a `name` on top-level volumes. They now allow `security_opt: [no-new-privileges]` and `logging` with `driver: json-file`.
- `runtime.md` states how keys in `external_secrets` reach the services and that egress is HTTPS to port 443 of `egress_hosts` only.

## 0.1.0 - 2026-11-20

- First draft: `openapi.yaml`, `runtime.md`, `grading.schema.json`, `metrics.schema.json`, `inventory.schema.json` and the static compose rules in `static_rules.py`.
- The contract may still change before 1.0.0; every change is listed here.
