# Round 1: the library service

A public library wants one service behind its website and its chat assistant. Readers search the catalog, reserve books, and ask the assistant for help finding and reserving them. You build that service.

Python is the only required technology. You choose the database, the web framework, the search engine, the models (if any) and how they fit together, and you explain those choices in `REPORT.md`.

## The domain

- A **Work** is a book as an idea, whatever the printing. Each Work has a `work_key` such as `/works/OL45883W`.
- An **edition** is one published version of a Work (a language, a publisher, a year), with an `edition_key` such as `OL7353617M`.
- A **copy** is one physical item on a shelf. Each copy belongs to one edition of one Work and has a `copy_id`.

The corpus in `data/corpus/` describes the Works and their editions. The inventory lists the copies the library holds.

## What you build

### 1. Catalog search you can measure

`GET /search?q=...` returns Works ranked best first. Readers type all sorts of things:

- a topic or a problem in their own words ("books to help a child who is afraid of the dark"), in English or in Vietnamese, with or without Vietnamese diacritics;
- an author's name;
- an exact title or an ISBN.

Exact lookups must put the matching Work **first**. These forms must all find it:

- an ISBN-10 or ISBN-13, with or without hyphens, with or without an `ISBN ` prefix (`9786041266001`, `ISBN 0-385-72932-4`);
- the full title, ignoring letter case, Vietnamese diacritics and punctuation (`canh dong bat tan` finds `Cánh đồng bất tận`).

Some queries have no good answer in the catalog. Decide what your service returns for them and explain it in `REPORT.md`.

Measure what you build. `evaluation/public-queries.jsonl` holds 91 queries with relevance judgments; `evaluation/README.md` explains them and how far to trust them. Compare the system you serve with at least one simpler alternative, and let the numbers drive your choices. Your project's `eval` service writes the results to `metrics.json` (`contract/runtime.md` section 11), and you commit a copy as `results/metrics.json`. Record in its `platform` field where `eval` ran (`linux/arm64` on an Apple Silicon Mac, for example).

### 2. Reservations that stay correct under load

`POST /reservations` reserves a Work for the calling user, optionally a specific edition.

- When a suitable copy is free, the reservation is `READY_FOR_PICKUP` and holds that copy. A reservation for a specific edition only takes a copy of that edition; one without an edition takes a copy of any edition.
- When no suitable copy is free, the reservation is `QUEUED` with its position in the Work's queue, starting at 1.
- A new reservation answers `201`. A user holds at most one active reservation per Work. Asking again returns the reservation they already have with `200`, even when the second request names another edition.
- Each Work has one queue, whatever edition its reservations ask for.
- This assignment has no pickup, return or cancellation: once made, a reservation stays as it is.

Example: the library holds one copy of a Work. An and Binh press "reserve" at the same moment. Exactly one of them gets `READY_FOR_PICKUP` with that copy; the other is `QUEUED` at position 1. If An's browser sends the request twice, An still holds one reservation.

The library runs more than one `api` container, and requests for the same Work arrive at the same time on different containers. Under that load, no copy is given to two people, no user ends up with two reservations of one Work, queue positions stay 1, 2, 3 without gaps or repeats, every success response matches what was stored, and every container answers with the same state. When you cannot decide a request right away because others compete for the same Work, you may answer `503` with `Retry-After`; any other error caused by contention counts as a failure.

### 3. An assistant that asks before it acts

`POST /agent/turns` is a chat turn with the library assistant. Behind it, your service runs a loop with an LLM that has exactly two tools: `search_catalog` and `place_reservation`. `docs/llm-setup.md` and `contract/runtime.md` section 10 describe the wire format.

- The assistant may search on its own.
- It never reserves anything during a turn. A reservation it proposes comes back as a pending action, and runs only when the same user calls `POST /agent/confirmations/{confirmation_id}`, exactly once: a second call answers `409`.
- A proposed reservation of a Work in the catalog always becomes a pending action, even when the model adds parameters of its own; ignore them rather than rejecting the proposal.
- Treat the model and the catalog text as untrusted. Book descriptions can contain instructions, and the model may follow them, call tools you did not offer, add parameters such as `confirmed: true`, or claim to act for another user. None of that may run an action, change who an action is for, or reach another user's conversation.

## How round 1 is graded

Four conditions come first. A submission that misses any of them does not pass round 1, whatever else it does well:

1. **Reservations stay correct under concurrency**, as described above, with two `api` containers started at the same time and many users reserving the same Works at once.
2. **The assistant never acts without confirmation.** We run it against an LLM of our own that tries hard to make it misbehave, including through catalog text.
3. **Search quality holds up on queries you have not seen.** We run a private set of queries of the same kinds as the public ones. On queries about meaning (a subject, a problem or a need put in other words), your served search must clearly beat a plain keyword search over the same corpus. On exact title and ISBN queries, the matching Work must come first.
4. **Your numbers are reproducible.** We recompute every aggregate in your `results/metrics.json` from its rankings, check that `GET /search` returns those rankings for the served system, and run your `eval` again to compare the numbers.

Before any of these, the grading environment checks your `compose.yaml` against the rules of `contract/runtime.md`; a violation stops grading before your code runs. `contract-check` runs the same static rules on your machine.

Then we read your code and `REPORT.md` for judgment: the trade-offs you made, how you tested, what you measured and what you concluded from it, and what you say you did not get to. Round 2 builds on the same work.

## The grading environment

`contract/runtime.md` is the full set of rules. The parts that most often surprise people:

- We build your repository with `docker compose build` and run it with `docker compose up`, always with `-f compose.yaml`. The build must finish within 60 minutes from an empty cache, and every other step has a time limit too (`contract/runtime.md` section 7). The grading machine runs `linux/arm64`.
- Your services must not publish ports in `compose.yaml`. Put your development port mappings in `compose.override.yaml`, which we never load.
- Each check gives your project its own corpus and inventory through `LIBRARY_CORPUS_PATH` and `LIBRARY_INVENTORY_PATH`. Load them at startup; do not rely on the repository copy or on `data/inventory.sample.json`.
- While your project runs, it sits on an internal network. The only way out is an HTTPS proxy that lets through only the hosts you list in `grading.toml`.
- `compose.grading-like.yaml` runs your project on a network shaped like that one, so you can see what breaks before we do.

Copy `grading.toml.example` to `grading.toml` and fill it in.

## Using an external API

You may call an external API (a hosted embedding model or a hosted LLM, for example) from `/search` or `eval`. Eve does not provide keys.

- Use a key of your own with a spending limit and an expiry date. Name its variable in `external_secrets` and its host in `egress_hosts` of `grading.toml`.
- Send the key through the channel the coordinator names. Never commit it.
- Plan your spending limit for one grading run: at most 500 calls to `GET /search`, most of them with `limit=100` (fewer than 100 private queries, each at most three times, plus the public queries), and at most two runs of `eval` in total over the 91 public queries (one when `eval_deterministic = true`).
- Your project also starts from scratch four times in one grading run, twice on the full corpus. Whatever it computes through the API at startup is paid for each time, unless you keep it in `/model-cache` (`contract/runtime.md` section 3). If a check ends in an error, we contact you before running it again.
- The queries we send to that service during grading pass through your account.
- Revoke the key once you have your result.
- Your agent loop uses `ANTHROPIC_*` only, never a key of your own (`contract/runtime.md` section 4).

## REPORT.md

Commit a `REPORT.md` at the root of your repository. It covers:

- your choice of database and search engine, and why;
- a table of your measurements, generated from `results/metrics.json`, comparing the systems you evaluated;
- what your service does with queries that have no good answer, and why;
- how you kept reservations correct under concurrency, and how you tested it;
- how the assistant stays safe when the model or the catalog text misbehaves;
- the limits of your solution that you know about, and what you would do next.

## Time, AI and data

- You have **7 calendar days** from the moment you receive the link; the coordinator states the exact deadline. We expect about **12–16 hours** of work (to be calibrated). Spend your time on the four conditions first.
- You may use AI assistants. In round 2 you will change your own code live, without AI, so make sure you understand and can defend every part of it.
- The corpus comes from Open Library. Use it only for this assignment; `data/corpus/NOTICE.md` has the terms.

## Submitting

Follow the steps in the repository `README.md`: work in your public fork, open a pull request inside your fork, and send its link to the coordinator before the deadline. We grade the last commit of your working branch at the deadline.
