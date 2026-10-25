"""Synthetic AITU Registration Service (Assignment 3, Fault Tolerance).

Educational code only: no real AITU endpoints, data or credentials.

Endpoints
  GET  /livez                     liveness  - is this process able to answer at all?
  GET  /ready                     readiness - can this pod do useful work (DB reachable)?
  GET  /api/registration/courses  list synthetic course sections with free seats
  POST /api/registration/enroll   {"student_id": "...", "section_id": "..."}  (Idempotency-Key header)
  GET  /chaos/crash   (localhost only, CHAOS_ENABLED=1)  -> process exits with code 137-like status
  GET  /chaos/hang    (localhost only, CHAOS_ENABLED=1)  -> /livez stops answering (simulated deadlock)

Design notes
  * Liveness never touches the database, so a DB outage makes pods NotReady
    (removed from the Service) instead of restarting them in a loop.
  * Every DB call has a hard socket timeout (DB_TIMEOUT_MS, default 400 ms) so
    the 500 ms API latency budget is respected even when the DB hangs; on error
    the service answers 503 immediately (fail fast) instead of queueing.
  * SIGTERM -> readiness turns false, the pod keeps serving in-flight requests
    for DRAIN_SECONDS, then exits. Together with the preStop hook this gives
    zero-error rolling updates.
"""
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pg8000.native

PORT = int(os.getenv("PORT", "8080"))
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "registration")
DB_USER = os.getenv("DB_USER", "registration_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "change-me-synthetic")
DB_TIMEOUT_S = int(os.getenv("DB_TIMEOUT_MS", "400")) / 1000.0
READY_CACHE_S = float(os.getenv("READY_CACHE_SECONDS", "0.5"))
DRAIN_SECONDS = float(os.getenv("DRAIN_SECONDS", "5"))
CHAOS_ENABLED = os.getenv("CHAOS_ENABLED", "0") == "1"
POD = os.getenv("POD_NAME", socket.gethostname())

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS course_section (
           section_id TEXT PRIMARY KEY,
           title      TEXT NOT NULL,
           capacity   INT  NOT NULL CHECK (capacity >= 0),
           enrolled   INT  NOT NULL DEFAULT 0 CHECK (enrolled <= capacity))""",
    """CREATE TABLE IF NOT EXISTS enrollment (
           idempotency_key TEXT PRIMARY KEY,
           student_id      TEXT NOT NULL,
           section_id      TEXT NOT NULL REFERENCES course_section(section_id),
           created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
           UNIQUE (student_id, section_id))""",
]
SEED = [
    ("CS-2101-01", "Fault Tolerant Systems (synthetic)", 60),
    ("CS-2101-02", "Fault Tolerant Systems (synthetic)", 60),
    ("SE-1203-01", "Cloud Computing (synthetic)", 80),
    ("MT-1101-03", "Discrete Mathematics (synthetic)", 120),
    ("CY-3304-01", "Network Security (synthetic)", 40),
]

state = {"draining": False, "hung": False, "schema_ok": False,
         "ready_at": 0.0, "ready_val": False, "ready_err": "not checked"}
_local = threading.local()
_schema_lock = threading.Lock()


def log(event, **kw):
    kw.update(ts=round(time.time(), 3), pod=POD, event=event)
    print(json.dumps(kw), flush=True)


class DBUnavailable(Exception):
    pass


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        try:
            c = pg8000.native.Connection(user=DB_USER, password=DB_PASSWORD, host=DB_HOST,
                                         port=DB_PORT, database=DB_NAME, timeout=DB_TIMEOUT_S)
        except Exception as e:  # connection refused, DNS failure, timeout...
            raise DBUnavailable(f"connect: {type(e).__name__}: {e}") from e
        _local.conn = c
    return c


def db(sql, **params):
    """Run one statement with a hard timeout. Drops the connection on any failure."""
    try:
        return _conn().run(sql, **params)
    except DBUnavailable:
        raise
    except pg8000.native.DatabaseError:
        raise  # SQL-level error (constraint etc.) - connection is still fine
    except Exception as e:
        bad = getattr(_local, "conn", None)
        _local.conn = None
        try:
            if bad is not None:
                bad.close()
        except Exception:
            pass
        raise DBUnavailable(f"{type(e).__name__}: {e}") from e


def ensure_schema():
    if state["schema_ok"]:
        return
    with _schema_lock:
        if state["schema_ok"]:
            return
        for stmt in SCHEMA:
            db(stmt)
        for sid, title, cap in SEED:
            db("INSERT INTO course_section(section_id,title,capacity) VALUES (:s,:t,:c) "
               "ON CONFLICT (section_id) DO NOTHING", s=sid, t=title, c=cap)
        state["schema_ok"] = True
        log("schema_ready")


def readiness():
    """DB-backed readiness, cached briefly so probes + traffic don't hammer the DB."""
    if state["draining"]:
        return False, "draining"
    now = time.monotonic()
    if now - state["ready_at"] < READY_CACHE_S:
        return state["ready_val"], state["ready_err"]
    try:
        ensure_schema()
        db("SELECT 1")
        val, err = True, ""
    except Exception as e:
        val, err = False, str(e)[:200]
    if val != state["ready_val"]:
        log("readiness_changed", ready=val, reason=err)
    state.update(ready_at=now, ready_val=val, ready_err=err)
    return val, err


class Handler(BaseHTTPRequestHandler):
    server_version = "aitu-registration-synthetic/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):  # silence default access log; we log JSON ourselves
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Served-By", POD)
        self.end_headers()
        self.wfile.write(raw)

    def _local_only(self):
        return CHAOS_ENABLED and self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/livez":
            while state["hung"]:          # simulated deadlock: never answer
                time.sleep(1)
            return self._send(200, {"status": "alive", "pod": POD})
        if path == "/ready":
            ok, why = readiness()
            return self._send(200 if ok else 503, {"ready": ok, "reason": why, "pod": POD})
        if path == "/api/registration/courses":
            try:
                ensure_schema()
                rows = db("SELECT section_id, title, capacity - enrolled FROM course_section "
                          "ORDER BY section_id")
            except DBUnavailable as e:
                return self._send(503, {"error": "database unavailable", "detail": str(e)[:120]})
            except pg8000.native.DatabaseError as e:  # e.g. table missing after logical corruption
                return self._send(500, {"error": "database error", "detail": str(e)[:120]})
            return self._send(200, {"sections": [
                {"section_id": r[0], "title": r[1], "free_seats": r[2]} for r in rows]})
        if path == "/chaos/crash" and self._local_only():
            log("chaos_crash")
            os._exit(1)
        if path == "/chaos/hang" and self._local_only():
            state["hung"] = True
            log("chaos_hang")
            return self._send(200, {"hung": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/registration/enroll":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
            student, section = str(body["student_id"]), str(body["section_id"])
        except Exception:
            return self._send(400, {"error": "body must be JSON with student_id and section_id"})
        key = self.headers.get("Idempotency-Key") or str(uuid.uuid4())
        try:
            ensure_schema()
            prev = db("SELECT student_id, section_id FROM enrollment WHERE idempotency_key=:k", k=key)
            if prev:
                return self._send(200, {"status": "already processed", "idempotency_key": key})
            # Atomic seat reservation + enrollment record in one statement (no lost updates).
            rows = db("""WITH seat AS (
                             UPDATE course_section SET enrolled = enrolled + 1
                             WHERE section_id = :s AND enrolled < capacity
                             RETURNING section_id)
                         INSERT INTO enrollment(idempotency_key, student_id, section_id)
                         SELECT :k, :st, section_id FROM seat
                         ON CONFLICT DO NOTHING RETURNING section_id""",
                      s=section, k=key, st=student)
        except DBUnavailable as e:
            return self._send(503, {"error": "database unavailable", "detail": str(e)[:120]})
        except pg8000.native.DatabaseError as e:
            return self._send(409, {"error": "conflict", "detail": str(e)[:120]})
        if not rows:
            return self._send(409, {"error": "section full, unknown, or student already enrolled"})
        return self._send(201, {"status": "enrolled", "section_id": section,
                                "student_id": student, "idempotency_key": key})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True

    def on_sigterm(*_):
        # Graceful drain: fail readiness first, keep serving, then stop.
        state["draining"] = True
        log("sigterm_received", drain_seconds=DRAIN_SECONDS)
        threading.Thread(target=lambda: (time.sleep(DRAIN_SECONDS), srv.shutdown()),
                         daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)
    log("starting", port=PORT, db_host=DB_HOST, db_timeout_s=DB_TIMEOUT_S, chaos=CHAOS_ENABLED)
    srv.serve_forever()
    log("stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
