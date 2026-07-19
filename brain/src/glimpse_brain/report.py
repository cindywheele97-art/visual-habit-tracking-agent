"""选品周刊 report server — the flywheel's first consumer surface.

Read-only, stdlib-only, local-only: aggregates behavior.sqlite3 into the
trajectory → outcome join (audit goal 3) and serves a street-weekly styled
dashboard on 127.0.0.1. Zero new dependencies (per-device pricing constraint);
the brain process is untouched — this is a separate short-lived viewer.

Run:  python -m glimpse_brain.report [--db ~/.glimpse/behavior.sqlite3] [--port 8787]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_HTML = Path(__file__).with_name("report.html")


def build_report(db_path: Path) -> dict[str, Any]:
    """Aggregate the behavior store into sessions → pages → outcomes.

    Raises FileNotFoundError for a missing db (fail loud — an empty dashboard
    must mean "no data", never "wrong path")."""
    if not db_path.exists():
        raise FileNotFoundError(f"behavior db not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT session_id, kind, ts, app, window_title, payload"
            " FROM events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    sessions: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    noise_clicks = 0
    for sid, kind, ts, app, title, payload_text in rows:
        payload = json.loads(payload_text)
        if not sid:
            if kind == "click":
                noise_clicks += 1
            continue
        if sid not in sessions:
            sessions[sid] = {
                "session_id": sid,
                "start_ts": ts,
                "end_ts": ts,
                "explicit": False,
                "clicks": 0,
                "dwell_seconds": 0.0,
                "pages": {},
                "events": [],
                "outcomes": [],
            }
            order.append(sid)
        s = sessions[sid]
        s["end_ts"] = max(s["end_ts"], ts)
        page = s["pages"].setdefault(
            title or "(未识别页面)", {"dwell_seconds": 0.0, "clicks": 0}
        ) if title or kind in ("click", "dwell") else None
        detail = ""
        if kind == "selection_control":
            if payload.get("action") == "start":
                s["explicit"] = True
                detail = "开始选品"
            else:
                detail = "结束选品"
        elif kind == "click":
            s["clicks"] += 1
            if page is not None:
                page["clicks"] += 1
            detail = " / ".join(payload.get("texts", []))[:80]
        elif kind == "dwell":
            secs = float(payload.get("seconds", 0.0))
            s["dwell_seconds"] += secs
            if page is not None:
                page["dwell_seconds"] += secs
            detail = f"{secs:.0f}s"
        elif kind == "selection_outcome":
            outcome = {
                "verdict": payload.get("verdict", ""),
                "product_key": payload.get("product_key", ""),
                "note": payload.get("note", ""),
                "ts": ts,
            }
            s["outcomes"].append(outcome)
            detail = outcome["verdict"]
        s["events"].append(
            {"kind": kind, "ts": ts, "title": title, "detail": detail}
        )

    out_sessions: list[dict[str, Any]] = []
    verdicts = {"selected": 0, "shortlisted": 0, "rejected": 0}
    total_clicks = 0
    total_dwell = 0.0
    for sid in order:
        s = sessions[sid]
        s["events"].sort(key=lambda e: e["ts"])
        s["pages"] = [
            {"title": t, **v}
            for t, v in sorted(
                s["pages"].items(),
                key=lambda kv: (-kv[1]["dwell_seconds"], -kv[1]["clicks"]),
            )
        ]
        total_clicks += s["clicks"]
        total_dwell += s["dwell_seconds"]
        for o in s["outcomes"]:
            if o["verdict"] in verdicts:
                verdicts[o["verdict"]] += 1
        out_sessions.append(s)

    return {
        "stats": {
            "sessions": len(out_sessions),
            "clicks": total_clicks,
            "dwell_seconds": total_dwell,
            "outcomes": verdicts,
            "span": [
                out_sessions[0]["start_ts"] if out_sessions else "",
                out_sessions[-1]["end_ts"] if out_sessions else "",
            ],
        },
        "sessions": out_sessions,
        "noise_clicks": noise_clicks,
    }


class _Handler(BaseHTTPRequestHandler):
    db_path: Path  # set by serve()

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path.split("?")[0] == "/api/report":
            try:
                body = json.dumps(build_report(self.db_path), ensure_ascii=False)
            except FileNotFoundError as exc:
                self._send(404, "application/json", json.dumps({"error": str(exc)}))
                return
            self._send(200, "application/json; charset=utf-8", body)
        elif self.path.split("?")[0] == "/":
            self._send(200, "text/html; charset=utf-8", _HTML.read_text(encoding="utf-8"))
        else:
            self._send(404, "text/plain", "not found")

    def _send(self, code: int, ctype: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        pass


def serve(db_path: Path, port: int) -> None:
    handler = type("BoundHandler", (_Handler,), {"db_path": db_path})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"选品周刊 → http://127.0.0.1:{port}  (db: {db_path}, Ctrl-C 退出)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Glimpse 选品周刊 dashboard")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("~/.glimpse/behavior.sqlite3").expanduser(),
        help="behavior store path (default: ~/.glimpse/behavior.sqlite3)",
    )
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    build_report(args.db)  # fail loud on a bad path BEFORE binding the port
    serve(args.db, args.port)


if __name__ == "__main__":
    main()
