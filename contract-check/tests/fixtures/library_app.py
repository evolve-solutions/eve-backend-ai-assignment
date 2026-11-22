"""A tiny in-memory Library service used to exercise contract-check.

It follows contract/openapi.yaml with a fixed three-work catalog and a
deterministic assistant (no LLM). `defect` switches on one deliberate deviation.
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

CATALOG = {
    "/works/OL1W": {"title": "A History of Libraries", "copies": ["c1"]},
    "/works/OL2W": {"title": "History of Paper", "copies": ["c2", "c3"]},
    "/works/OL3W": {"title": "Gardening Basics", "copies": []},
}

DEFECTS = (
    None,
    "reservation_get_without_queue_position",  # GET /reservations/{id} drops one field
    "contend_once",  # first POST /reservations answers 503 + Retry-After
)


class LibraryApp:
    def __init__(self, defect: str | None = None):
        if defect not in DEFECTS:
            raise ValueError(defect)
        self.defect = defect
        self.lock = threading.Lock()
        self.reservations: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.used: set[str] = set()
        self.contended = False

    def reserve(self, user: str, work_key: str, edition_key=None) -> tuple[int, dict]:
        with self.lock:
            for reservation in self.reservations.values():
                if reservation["user_id"] == user and reservation["work_key"] == work_key:
                    return 200, reservation
            held = {r["assigned_copy_id"] for r in self.reservations.values()}
            free = [c for c in CATALOG[work_key]["copies"] if c not in held]
            queued = [r for r in self.reservations.values() if r["work_key"] == work_key and r["status"] == "QUEUED"]
            reservation = {
                "id": uuid.uuid4().hex,
                "user_id": user,
                "work_key": work_key,
                "edition_key": edition_key,
                "status": "READY_FOR_PICKUP" if free else "QUEUED",
                "assigned_copy_id": free[0] if free else None,
                "queue_position": None if free else len(queued) + 1,
            }
            self.reservations[reservation["id"]] = reservation
            return 201, reservation

    def handle(self, method: str, raw_path: str, headers, body: bytes) -> tuple[int, dict | None, dict]:
        url = urlsplit(raw_path)
        segments = [unquote(s) for s in url.path.split("/")[1:]]
        user = headers.get("X-User-Id")
        payload = json.loads(body) if body else None

        if method == "GET" and segments == ["health"]:
            return 200, {"status": "ready"}, {}
        if method == "GET" and segments == ["search"]:
            query = parse_qs(url.query)
            if not query.get("q"):
                return 422, None, {}
            limit = int(query.get("limit", ["10"])[0])
            if not 1 <= limit <= 100:
                return 422, None, {}
            words = query["q"][0].lower().split()
            hits = [
                {
                    "work_key": key,
                    "title": work["title"],
                    "score": float(sum(w in work["title"].lower() for w in words)),
                }
                for key, work in CATALOG.items()
            ]
            hits = sorted((h for h in hits if h["score"] > 0), key=lambda h: -h["score"])
            return 200, {"results": hits[:limit]}, {}
        if method == "POST" and segments == ["reservations"]:
            if not user:
                return 401, None, {}
            if not isinstance(payload, dict) or "work_key" not in payload:
                return 422, None, {}
            if payload["work_key"] not in CATALOG:
                return 404, None, {}
            if self.defect == "contend_once" and not self.contended:
                self.contended = True
                return 503, None, {"Retry-After": "0"}
            status, reservation = self.reserve(user, payload["work_key"], payload.get("edition_key"))
            return status, reservation, {}
        if method == "GET" and len(segments) == 2 and segments[0] == "reservations":
            reservation = self.reservations.get(segments[1])
            if reservation is None:
                return 404, None, {}
            if self.defect == "reservation_get_without_queue_position":
                reservation = {k: v for k, v in reservation.items() if k != "queue_position"}
            return 200, reservation, {}
        if method == "GET" and len(segments) == 3 and segments[0] == "works" and segments[2] == "reservations":
            if segments[1] not in CATALOG:
                return 404, None, {}
            found = [r for r in self.reservations.values() if r["work_key"] == segments[1]]
            return 200, {"reservations": found}, {}
        if method == "POST" and segments == ["agent", "turns"]:
            if not user:
                return 401, None, {}
            if not isinstance(payload, dict) or not payload.get("message"):
                return 422, None, {}
            actions = []
            for key, work in CATALOG.items():
                if "reserve" in payload["message"].lower() and work["title"].lower() in payload["message"].lower():
                    confirmation_id = uuid.uuid4().hex
                    self.pending[confirmation_id] = {"user": user, "work_key": key}
                    actions.append(
                        {"confirmation_id": confirmation_id, "tool": "place_reservation", "input": {"work_key": key}}
                    )
            conversation_id = payload.get("conversation_id") or uuid.uuid4().hex
            return 200, {"conversation_id": conversation_id, "reply": "Please confirm.", "pending_actions": actions}, {}
        if method == "POST" and len(segments) == 3 and segments[:2] == ["agent", "confirmations"]:
            if not user:
                return 401, None, {}
            pending = self.pending.get(segments[2])
            if pending is None or pending["user"] != user:
                return 404, None, {}
            if segments[2] in self.used:
                return 409, None, {}
            self.used.add(segments[2])
            status, reservation = self.reserve(user, pending["work_key"])
            return status, reservation, {}
        return 404, None, {}


def serve(defect: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    """Start the app on a free local port; return the server and its base URL."""
    app = LibraryApp(defect)

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            status, body, extra = app.handle(self.command, self.path, self.headers, self.rfile.read(length))
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            for name, value in extra.items():
                self.send_header(name, value)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
