"""Local web UI:  python -m rbcart.server  ->  http://127.0.0.1:5057"""
from __future__ import annotations

import argparse
import traceback

from flask import Flask, jsonify, request, send_from_directory

from . import service
from .catalog import load_catalog

app = Flask(__name__, static_folder="static")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def get_state():
    return jsonify(service.load_state())


@app.post("/api/state")
def put_state():
    service.save_state(request.get_json())
    return jsonify(ok=True)


@app.post("/api/resolve")
def resolve():
    body = request.get_json()
    return jsonify(rows=service.resolve(body.get("deck_text", ""), load_catalog()))


@app.post("/api/optimize")
def run_optimize():
    body = request.get_json()
    try:
        service.save_state({k: body.get(k) for k in ("deck_text", "owned", "modes", "settings")})
        res = service.run(body["deck_text"], body.get("owned", {}), body.get("modes", {}), body.get("settings", {}),
                          refresh=bool(body.get("refresh")))
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify(error=str(e)), 500


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5057)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    load_catalog()  # warm the cache
    print(f"Open http://{a.host}:{a.port}")
    app.run(host=a.host, port=a.port, debug=False)


if __name__ == "__main__":
    main()
