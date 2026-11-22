"""`contract-check --base-url` against a conforming and a deviating service."""

from __future__ import annotations

import json

import pytest
from contract_check.cli import main

from tests.fixtures.library_app import serve


@pytest.fixture
def run_against(capsys):
    servers = []

    def run(defect=None):
        server, base_url = serve(defect)
        servers.append(server)
        code = main(["--base-url", base_url, "--json"])
        return code, json.loads(capsys.readouterr().out)

    yield run
    for server in servers:
        server.shutdown()


def failures(report):
    return [c for c in report["checks"] if c["status"] == "fail"]


def test_conforming_service_passes_every_check(run_against):
    code, report = run_against()
    assert failures(report) == []
    assert code == 0
    assert {c["status"] for c in report["checks"]} == {"pass"}


def test_one_missing_field_fails_only_the_check_that_reads_it(run_against):
    code, report = run_against("reservation_get_without_queue_position")
    assert code == 1
    [failure] = failures(report)
    assert failure["name"] == "reservation_get"
    assert "queue_position" in failure["detail"]


def test_contention_answer_is_retried_not_failed(run_against):
    code, report = run_against("contend_once")
    assert failures(report) == []
    assert code == 0
