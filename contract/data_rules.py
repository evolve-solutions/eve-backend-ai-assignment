"""Rules for a corpus and inventory pair, as described in `contract/runtime.md` section 3.

Every grading check supplies its own corpus and inventory. This module checks that a
pair follows the contract: each corpus line matches `corpus.schema.json` with a unique
`work_key` and a correct `content_hash`, and the inventory matches
`inventory.schema.json` and refers only to Works and editions of that same corpus.
Shared by `contract-check --corpus ... --inventory ...` and the grading harness.

Dependencies: Python 3.11+, jsonschema.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

# Fields that define a record's content; `content_hash` is computed over them.
CONTENT_FIELDS = (
    "title",
    "subtitle",
    "authors",
    "subjects",
    "description",
    "first_publish_year",
    "languages",
    "edition_keys",
    "isbns",
)
MAX_REPORTED = 20


def content_hash(record: dict) -> str:
    """`sha256:` + hex SHA-256 of the content fields as canonical JSON (UTF-8)."""
    content = {field: record[field] for field in CONTENT_FIELDS}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validator(file_name: str) -> Draft202012Validator:
    schema = json.loads(Path(__file__).with_name(file_name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def check_corpus(corpus_path: str | Path) -> tuple[list[str], dict[str, set[str]]]:
    """Return (problems, editions by work_key) for a JSON Lines corpus."""
    validator = _validator("corpus.schema.json")
    problems: list[str] = []
    editions: dict[str, set[str]] = {}
    with Path(corpus_path).open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            where = f"corpus line {number}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                problems.append(f"{where}: not JSON ({error.msg})")
                continue
            errors = list(validator.iter_errors(record))
            problems += [f"{where}: {'/'.join(map(str, e.absolute_path)) or '(record)'}: {e.message}" for e in errors]
            work_key = record.get("work_key") if isinstance(record, dict) else None
            if not isinstance(work_key, str):
                continue
            if work_key in editions:
                problems.append(f"{where}: duplicate work_key {work_key}")
                continue
            if not errors and record["content_hash"] != content_hash(record):
                problems.append(f"{where}: content_hash does not match the content of {work_key}")
            # Register even an invalid record, so its copies are not reported a second time.
            keys = record.get("edition_keys")
            editions[work_key] = {k for k in keys if isinstance(k, str)} if isinstance(keys, list) else set()
    if not editions and not problems:
        problems.append("corpus: no records")
    return problems, editions


def check_inventory(inventory: dict, editions: dict[str, set[str]]) -> list[str]:
    """Problems of an inventory against the editions of its own corpus."""
    problems = [
        f"inventory: {'/'.join(map(str, e.absolute_path)) or '(document)'}: {e.message}"
        for e in _validator("inventory.schema.json").iter_errors(inventory)
    ]
    if problems:
        return problems
    seen: set[str] = set()
    for index, copy in enumerate(inventory["copies"]):
        where = f"inventory copies/{index}"
        if copy["copy_id"] in seen:
            problems.append(f"{where}: duplicate copy_id {copy['copy_id']}")
        seen.add(copy["copy_id"])
        if copy["work_key"] not in editions:
            problems.append(f"{where}: work_key {copy['work_key']} is not in the corpus")
        elif copy["edition_key"] not in editions[copy["work_key"]]:
            problems.append(f"{where}: edition_key {copy['edition_key']} is not an edition of {copy['work_key']}")
    return problems


def check_data(corpus_path: str | Path, inventory_path: str | Path) -> list[str]:
    """Every problem of a corpus and inventory pair, at most MAX_REPORTED plus a count."""
    problems, editions = check_corpus(corpus_path)
    try:
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        problems.append(f"inventory: not JSON ({error.msg})")
    else:
        problems += check_inventory(inventory, editions)
    if len(problems) > MAX_REPORTED:
        problems = problems[:MAX_REPORTED] + [f"... and {len(problems) - MAX_REPORTED} more problem(s)"]
    return problems
