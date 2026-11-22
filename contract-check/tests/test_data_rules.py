"""`contract-check --corpus --inventory`: a check may ship its own corpus.

Records come from the shipped corpus, so their content_hash values were computed by
the corpus pipeline, not by the code under test. `work_key` is not a content field,
so renaming it yields a valid Work that is absent from the shipped corpus.
"""

from __future__ import annotations

import copy
import json

import pytest
from contract_check.cli import main
from contract_check.contract import contract_dir

SHIPPED_CORPUS = contract_dir().parent / "data/corpus/openlibrary-works.jsonl"
EXTRA_WORK_KEY = "/works/OL999999999W"


def shipped_records(count: int) -> list[dict]:
    with SHIPPED_CORPUS.open(encoding="utf-8") as lines:
        return [json.loads(next(lines)) for _ in range(count)]


def valid_pair() -> tuple[list[dict], dict]:
    first, second, extra = shipped_records(3)
    extra["work_key"] = EXTRA_WORK_KEY
    corpus = [first, second, extra]
    inventory = {
        "copies": [
            {"copy_id": f"c{i}", "work_key": record["work_key"], "edition_key": record["edition_keys"][0]}
            for i, record in enumerate(corpus)
        ]
    }
    return corpus, inventory


def run(tmp_path, capsys, corpus, inventory) -> tuple[int, list[str]]:
    corpus_path, inventory_path = tmp_path / "corpus.jsonl", tmp_path / "inventory.json"
    corpus_path.write_text("".join(json.dumps(record) + "\n" for record in corpus))
    inventory_path.write_text(json.dumps(inventory))
    code = main(["--corpus", str(corpus_path), "--inventory", str(inventory_path), "--json"])
    return code, json.loads(capsys.readouterr().out)["problems"]


def test_check_specific_corpus_with_an_extra_work_is_valid(tmp_path, capsys):
    corpus, inventory = valid_pair()
    assert run(tmp_path, capsys, corpus, inventory) == (0, [])


def copy_of_absent_work(corpus, inventory):
    [absent] = shipped_records(4)[3:]
    inventory["copies"][0]["work_key"] = absent["work_key"]
    inventory["copies"][0]["edition_key"] = absent["edition_keys"][0]


def foreign_edition(corpus, inventory):
    inventory["copies"][0]["edition_key"] = corpus[1]["edition_keys"][0]


def duplicate_copy_id(corpus, inventory):
    inventory["copies"][1]["copy_id"] = inventory["copies"][0]["copy_id"]


def missing_field(corpus, inventory):
    del corpus[0]["description"]


def duplicate_work_key(corpus, inventory):
    corpus[2]["work_key"] = corpus[0]["work_key"]
    inventory["copies"].pop()


def extra_record_field(corpus, inventory):
    corpus[0]["extra_field"] = "placeholder"


def extra_author_field(corpus, inventory):
    corpus[0]["authors"][0]["role"] = "editor"


def stale_content_hash(corpus, inventory):
    corpus[2]["title"] += " (changed)"


CASES = [
    ("inventory-work-not-in-this-corpus", copy_of_absent_work, "is not in the corpus"),
    ("edition-of-another-work", foreign_edition, "is not an edition of"),
    ("duplicate-copy-id", duplicate_copy_id, "duplicate copy_id"),
    ("record-missing-field", missing_field, "'description' is a required property"),
    ("duplicate-work-key", duplicate_work_key, "duplicate work_key"),
    ("stale-content-hash", stale_content_hash, "content_hash does not match"),
    ("record-extra-field", extra_record_field, "Additional properties are not allowed ('extra_field'"),
    ("author-extra-field", extra_author_field, "Additional properties are not allowed ('role'"),
]


@pytest.mark.parametrize("case_id,mutate,expected", CASES, ids=[c[0] for c in CASES])
def test_each_rule_reports_exactly_one_problem(tmp_path, capsys, case_id, mutate, expected):
    corpus, inventory = copy.deepcopy(valid_pair())
    mutate(corpus, inventory)
    code, problems = run(tmp_path, capsys, corpus, inventory)
    assert code == 1
    assert len(problems) == 1 and expected in problems[0], problems
