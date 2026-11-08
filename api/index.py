"""Endpoint privado de Vercel para ejecutar el estudio Dune sin revelar la clave."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

DUNE = "https://api.dune.com/api/v1"
ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "deploy" / "dune" / "devs_mes.sql"


def _dune(path: str, *, body: dict | None = None) -> tuple[int, dict]:
    raw_key = os.environ.get("DUNE_API_KEY", "").strip()
    # Las terminales móviles pueden guardar marcas de bracketed-paste o un
    # ``~`` final. Dune las interpreta como parte de la clave y responde 401.
    raw_key = re.sub(r"\x1b\[20[01]~|\[20[01]~", "", raw_key)
    key = re.sub(r"[^A-Za-z0-9_-]", "", raw_key)
    if not key:
        return 503, {"error": "DUNE_API_KEY no configurada"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        DUNE + path,
        data=data,
        method="POST" if data is not None else "GET",
        headers={
            "Content-Type": "application/json",
            "X-Dune-Api-Key": key,
            "User-Agent": "definitivo-bot-dev-screen/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            detail = {"error": f"Dune HTTP {exc.code}"}
        return exc.code, detail


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        action = query.get("action", ["health"])[0]
        if action == "health":
            self._send(
                200,
                {
                    "ok": True,
                    "dune_configured": bool(os.environ.get("DUNE_API_KEY")),
                    "actions": ["start", "status", "results"],
                },
            )
            return
        if action == "start":
            sql = SQL_FILE.read_text(encoding="utf-8").replace(
                "{SOL_USD}", query.get("sol_usd", ["150"])[0]
            )
            status, payload = _dune(
                "/sql/execute", body={"sql": sql, "performance": "medium"}
            )
            self._send(status, payload)
            return
        execution = query.get("execution_id", [""])[0]
        if not execution or not all(c.isalnum() for c in execution):
            self._send(400, {"error": "execution_id invalido"})
            return
        if action == "status":
            status, payload = _dune(f"/execution/{execution}/status")
            self._send(status, payload)
            return
        if action == "results":
            limit = min(int(query.get("limit", ["5000"])[0]), 5000)
            offset = max(int(query.get("offset", ["0"])[0]), 0)
            status, payload = _dune(
                f"/execution/{execution}/results?limit={limit}&offset={offset}"
            )
            self._send(status, payload)
            return
        self._send(400, {"error": "action desconocida"})
