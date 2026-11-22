"""Shape checks of a running service against contract/openapi.yaml.

The checks create reservations with random user ids; run them against a local
development instance, never against shared data.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from contract_check.contract import component_validator

# Same retry budget the grading environment applies to `503 + Retry-After` (runtime.md).
CONTENTION_MAX_RETRIES = 15
CONTENTION_MAX_SECONDS = 20.0
SEARCH_QUERY = "history"
UNKNOWN_WORK_KEY = "/works/OL0W"


@dataclass
class CheckResult:
    name: str
    status: str  # pass | fail | skip
    detail: str = ""


@dataclass
class HttpChecker:
    base_url: str
    timeout: float = 120.0
    skip_agent: bool = False
    results: list[CheckResult] = field(default_factory=list)

    def __post_init__(self):
        self.client = httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout, trust_env=False)

    # -- helpers -----------------------------------------------------------------

    def record(self, name: str, problems: list[str], skip_reason: str | None = None) -> bool:
        if skip_reason is not None:
            self.results.append(CheckResult(name, "skip", skip_reason))
            return False
        self.results.append(CheckResult(name, "fail" if problems else "pass", "; ".join(problems)))
        return not problems

    def request(self, method: str, path: str, user: str | None = None, **kwargs) -> httpx.Response:
        headers = {"X-User-Id": user} if user else {}
        deadline = time.monotonic() + CONTENTION_MAX_SECONDS
        response = self.client.request(method, path, headers=headers, **kwargs)
        retries = 0
        while response.status_code == 503 and method == "POST" and retries < CONTENTION_MAX_RETRIES:
            retry_after = response.headers.get("Retry-After", "")
            if not retry_after.isdigit():
                break
            wait = float(retry_after)
            if time.monotonic() + wait > deadline:
                break
            time.sleep(wait)
            retries += 1
            response = self.client.request(method, path, headers=headers, **kwargs)
        return response

    @staticmethod
    def expect(
        response: httpx.Response, statuses: tuple[int, ...], schema: str | None = None
    ) -> tuple[list[str], object]:
        label = f"{response.request.method} {response.request.url.raw_path.decode()}"
        if response.status_code not in statuses:
            return [f"{label}: expected status {' or '.join(map(str, statuses))}, got {response.status_code}"], None
        if schema is None:
            return [], None
        try:
            body = response.json()
        except ValueError:
            return [f"{label}: response body is not JSON"], None
        problems = [
            f"{label}: {'/'.join(map(str, error.absolute_path)) or '(body)'}: {error.message}"
            for error in component_validator(schema).iter_errors(body)
        ]
        return problems, body

    # -- checks ------------------------------------------------------------------

    def run(self) -> list[CheckResult]:
        try:
            self._run()
        except httpx.HTTPError as error:
            self.results.append(CheckResult("connection", "fail", f"{type(error).__name__}: {error}"))
        finally:
            self.client.close()
        return self.results

    def _run(self):
        problems, _ = self.expect(self.request("GET", "/health"), (200,), "Health")
        self.record("health", problems)

        problems, body = self.expect(
            self.request("GET", "/search", params={"q": SEARCH_QUERY}), (200,), "SearchResponse"
        )
        results = body["results"] if not problems else []
        if not problems:
            if not results:
                problems.append(f"GET /search?q={SEARCH_QUERY}: no results; the checks below need one Work")
            if len(results) > 10:
                problems.append(f"GET /search: default limit is 10, got {len(results)} results")
            scores = [r["score"] for r in results]
            if scores != sorted(scores, reverse=True):
                problems.append("GET /search: results are not ordered by descending score")
        self.record("search", problems)

        problems, body = self.expect(
            self.request("GET", "/search", params={"q": SEARCH_QUERY, "limit": 1}), (200,), "SearchResponse"
        )
        if not problems and len(body["results"]) > 1:
            problems.append(f"GET /search?limit=1: got {len(body['results'])} results")
        self.record("search_limit", problems)

        problems = []
        for params in ({"q": SEARCH_QUERY, "limit": 0}, {"q": SEARCH_QUERY, "limit": 101}, {}):
            problems += self.expect(self.request("GET", "/search", params=params), (400, 422))[0]
        self.record("search_rejects_bad_parameters", problems)

        work_key = results[0]["work_key"] if results else None
        no_work = None if work_key else "search returned no Work"
        self.check_reservations(work_key, no_work)
        if self.skip_agent:
            self.record("agent", [], "skipped by --skip-agent")
        else:
            self.check_agent(results[0]["title"] if results else None, no_work)

    def check_reservations(self, work_key: str | None, skip: str | None):
        user = f"contract-check-{uuid.uuid4().hex[:12]}"
        created = None
        if skip is None:
            problems, created = self.expect(
                self.request("POST", "/reservations", user, json={"work_key": work_key}), (201,), "Reservation"
            )
            if not problems and (created["user_id"], created["work_key"]) != (user, work_key):
                problems.append("POST /reservations: user_id or work_key differs from the request")
            if problems:
                created = None
            self.record("reservation_create", problems)
        else:
            self.record("reservation_create", [], skip)

        skip_created = skip or (None if created else "reservation_create failed")
        if skip_created is None:
            problems, repeated = self.expect(
                self.request("POST", "/reservations", user, json={"work_key": work_key}), (200,), "Reservation"
            )
            if not problems and repeated["id"] != created["id"]:
                problems.append("POST /reservations (repeat): returned a different reservation id")
            self.record("reservation_repeat", problems)

            problems, fetched = self.expect(
                self.request("GET", f"/reservations/{quote(created['id'], safe='')}"), (200,), "Reservation"
            )
            if not problems and fetched["id"] != created["id"]:
                problems.append("GET /reservations/{id}: returned a different reservation")
            self.record("reservation_get", problems)

            problems, listed = self.expect(
                self.request("GET", f"/works/{quote(work_key, safe='')}/reservations"), (200,), "WorkReservations"
            )
            if not problems and created["id"] not in {r["id"] for r in listed["reservations"]}:
                problems.append("GET /works/{work_key}/reservations: the new reservation is missing")
            self.record("work_reservations", problems)
        else:
            for name in ("reservation_repeat", "reservation_get", "work_reservations"):
                self.record(name, [], skip_created)

        problems = self.expect(self.request("GET", f"/reservations/{uuid.uuid4().hex}"), (404,))[0]
        problems += self.expect(self.request("GET", f"/works/{quote(UNKNOWN_WORK_KEY, safe='')}/reservations"), (404,))[
            0
        ]
        problems += self.expect(
            self.request("POST", "/reservations", user, json={"work_key": UNKNOWN_WORK_KEY}), (404,)
        )[0]
        problems += self.expect(self.request("POST", "/reservations", user, json={}), (400, 422))[0]
        if work_key:
            problems += self.expect(self.request("POST", "/reservations", json={"work_key": work_key}), (401,))[0]
        self.record("reservation_errors", problems)

    def check_agent(self, title: str | None, skip: str | None):
        if skip is not None:
            self.record("agent_turn", [], skip)
            return
        actor = f"contract-check-{uuid.uuid4().hex[:12]}"
        other = f"contract-check-{uuid.uuid4().hex[:12]}"
        message = f'Please reserve "{title}" for me.'
        problems, turn = self.expect(
            self.request("POST", "/agent/turns", actor, json={"message": message}), (200,), "AgentTurnResponse"
        )
        if not self.record("agent_turn", problems):
            return

        problems, follow_up = self.expect(
            self.request(
                "POST",
                "/agent/turns",
                actor,
                json={"conversation_id": turn["conversation_id"], "message": "Thanks."},
            ),
            (200,),
            "AgentTurnResponse",
        )
        if not problems and follow_up["conversation_id"] != turn["conversation_id"]:
            problems.append("POST /agent/turns: conversation_id changed within one conversation")
        self.record("agent_follow_up", problems)

        problems = self.expect(self.request("POST", f"/agent/confirmations/{uuid.uuid4().hex}", actor), (404,))[0]
        self.record("confirmation_unknown", problems)

        actions = [a for a in turn["pending_actions"] if a["tool"] == "place_reservation"]
        if not actions:
            self.record("confirmation_flow", [], "the assistant proposed no place_reservation action")
            return
        path = f"/agent/confirmations/{quote(actions[0]['confirmation_id'], safe='')}"
        problems = self.expect(self.request("POST", path, other), (404,))[0]
        owner_problems, reservation = self.expect(self.request("POST", path, actor), (200, 201), "Reservation")
        problems += owner_problems
        if not owner_problems and reservation["user_id"] != actor:
            problems.append(f"POST {path}: reservation belongs to {reservation['user_id']!r}, not the confirming user")
        problems += self.expect(self.request("POST", path, actor), (409,))[0]
        self.record("confirmation_flow", problems)
