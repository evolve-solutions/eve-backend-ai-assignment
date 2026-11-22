"""Command line entry point: `contract-check --base-url URL` or `--compose-dir DIR`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict

from contract_check.contract import load_data_rules, load_static_rules
from contract_check.http_checks import HttpChecker


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="contract-check",
        description="Check a Library service against the published contract (shapes only, not correctness).",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--base-url", help="URL of a running service, for example http://127.0.0.1:8000")
    target.add_argument("--compose-dir", help="repository root holding compose.yaml and grading.toml")
    target.add_argument("--corpus", help="corpus file (JSON Lines) to check together with --inventory")
    parser.add_argument("--inventory", help="inventory file (JSON) that goes with --corpus")
    parser.add_argument("--skip-agent", action="store_true", help="skip the /agent checks (they call your LLM)")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds per HTTP request (default 120)")
    parser.add_argument("--json", action="store_true", help="print a JSON report instead of text")
    args = parser.parse_args(argv)
    if bool(args.corpus) != bool(args.inventory):
        parser.error("--corpus and --inventory go together")
    return args


def run_compose(args: argparse.Namespace) -> int:
    # The grading environment always checks the resolved configuration too; without
    # Docker only the files as written can be checked.
    resolve = shutil.which("docker") is not None
    violations = load_static_rules().check_project(args.compose_dir, resolve=resolve)
    if args.json:
        print(json.dumps({"resolved": resolve, "violations": [asdict(v) for v in violations]}, indent=2))
    else:
        if not resolve:
            print("note: docker not found; checked the files as written, not `docker compose config` output")
        for violation in violations:
            print(f"FAIL {violation}")
        print(f"{len(violations)} violation(s)" if violations else "compose.yaml and grading.toml follow the contract")
    return 1 if violations else 0


def run_data(args: argparse.Namespace) -> int:
    problems = load_data_rules().check_data(args.corpus, args.inventory)
    if args.json:
        print(json.dumps({"problems": problems}, indent=2))
    else:
        for problem in problems:
            print(f"FAIL {problem}")
        print(f"{len(problems)} problem(s)" if problems else "corpus and inventory follow the contract")
    return 1 if problems else 0


def run_http(args: argparse.Namespace) -> int:
    results = HttpChecker(args.base_url, timeout=args.timeout, skip_agent=args.skip_agent).run()
    failed = [r for r in results if r.status == "fail"]
    if args.json:
        print(json.dumps({"checks": [asdict(r) for r in results]}, indent=2))
    else:
        for result in results:
            line = f"{result.status.upper():4} {result.name}"
            print(f"{line}: {result.detail}" if result.detail else line)
        print(f"{len(failed)} of {len(results)} check(s) failed" if failed else "all checks passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.compose_dir:
        return run_compose(args)
    if args.corpus:
        return run_data(args)
    return run_http(args)


if __name__ == "__main__":
    sys.exit(main())
