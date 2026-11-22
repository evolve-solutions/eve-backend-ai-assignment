# Evaluation queries

`public-queries.jsonl` holds 91 search queries with relevance judgments over the corpus in `data/corpus/`. Use it to measure your retrieval systems and to produce `results/metrics.json` (`contract/runtime.md` section 11). At grading time `LIBRARY_QUERIES_PATH` points to a byte-identical copy of this file, so the `queries_sha256` you commit is the SHA-256 of this file.

## Fields

One JSON object per line:

| Field | Meaning |
|---|---|
| `query_id` | Unique id of the query |
| `query` | The query text as a user would type it |
| `language` | `en`, `vi`, or `none` for a query made of digits only (an ISBN) |
| `category` | One of `exact_title`, `author`, `isbn`, `topic`, `problem_semantic`, `paraphrase_synonym`, `vi_no_diacritics`, `ambiguous`, `no_relevant`, `adversarial` |
| `adversarial_type` | For `adversarial` queries only, else `null`: `prompt_injection`, `misleading_document`, `negation`, `keyword_sense_trap`, `conflicting_constraint`, `nonexistent_identifier`, `spam_record` |
| `pair_id` | Queries with the same `pair_id` express the same need (a Vietnamese and an English version, a rewording, a version without diacritics) and share one set of judgments; `null` for a standalone query |
| `judgments` | Judged Works: `[{"work_key", "relevance"}]`, including Works judged 0 |
| `label_source` | Who produced the judgments; `assistant_provisional` for every query in this file |

## Relevance scale

| Relevance | Meaning |
|---|---|
| 3 | Meets the need of the query directly and fully, or is the exact record asked for (title, author, ISBN queries) |
| 2 | Clearly useful but covers only the main part of the need, or targets a slightly wider or narrower audience |
| 1 | Marginal: touches the need but has the wrong audience or genre |
| 0 | Read and judged not relevant; often a keyword trap that matches the words but is about something else |

A Work is relevant when its relevance is 1 or more. `no_relevant` queries, and some `adversarial` ones, have no relevant Work.

## How reliable the judgments are

- Every judgment is `assistant_provisional`: a language model produced it and no person has reviewed it yet.
- Candidates for judging came from two kinds of pools. For some queries, keyword and subject matches in the corpus; these lean towards systems that match words. For the others, the top 10 of several retrieval systems.
- A Work without a judgment is not known to be irrelevant; nobody has looked at it. Every metric in `contract/runtime.md` counts it as relevance 0, and `judged@10` reports how much of your top 10 was judged. A low `judged@10` means your scores say more about the coverage of the judgments than about your system.
- Where several systems were pooled, each gave at most its top 10, so `recall@100` there is a lower bound.
- Few queries have relevant Works described in Vietnamese; the corpus holds few Vietnamese descriptions on these subjects.
