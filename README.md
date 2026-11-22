# Library service: backend and AI engineer assignment

You build a small service for a public library in Python: a catalog search you measure, reservations that stay correct when many people reserve at once, and an assistant that searches the catalog and proposes reservations, but never makes one without the user's confirmation.

Start with [`docs/brief-round1.md`](docs/brief-round1.md). It explains the task, how it is graded, and how to submit.

## What is in this repository

| Path | What it is |
|---|---|
| `docs/brief-round1.md` | The assignment (round 1) |
| `docs/brief-round2.md` | The live session that follows (round 2) |
| `docs/llm-setup.md` | How the assistant talks to an LLM, and how to test it without our LLM |
| `contract/` | The interface your project must follow: `openapi.yaml` (HTTP), `runtime.md` (Docker Compose, data, network, time limits), JSON schemas |
| `contract-check/` | A checker for the static rules and HTTP shapes of the contract |
| `data/corpus/` | About 14,000 book records from Open Library; read `NOTICE.md` before use |
| `data/inventory.sample.json` | Sample inventory of physical copies |
| `evaluation/` | 91 public queries with relevance judgments, and how to read them |
| `grading.toml.example` | Template of the `grading.toml` your project needs |
| `compose.grading-like.yaml` | Runs your project on a network shaped like the grading one |

## Getting started

1. **Fork this repository** to your own GitHub account. A fork of a public repository is always public, so anyone can see your work in it.
2. Create a working branch in your fork and build there.
3. Read `contract/runtime.md` before you write your `compose.yaml`; check it with `contract-check` (see [`contract-check/README.md`](contract-check/README.md)).

You need Docker with Compose v2.24 or later, Python 3.12, and [`uv`](https://docs.astral.sh/uv/).

## Submitting

- Open a pull request **inside your fork**, from your working branch into your fork's default branch. Do not open pull requests or issues on this repository.
- Send the link to your fork or to that pull request to the coordinator before the deadline. The coordinator gives you the deadline (date, time and time zone) and the channel for keys together with the link to this repository.
- We grade the last commit of your working branch at the deadline.
- Never put a key, token or password in your fork, not even in a commit you later revert. If your project uses an external API, send its key through the channel the coordinator names (see the brief).
