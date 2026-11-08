"""Flask API for genealogy-evidence-forge.

Local-first: everything runs on your own machine, nothing is sent anywhere,
no API keys, no network calls.
"""

import io
import json
import os

from flask import Flask, Response, jsonify, request, send_from_directory

from gh_analysis import MODES, analyze, to_csv, to_json, to_markdown
from db import init_db, get_session, list_sessions, save_session, update_session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "samples")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="/static")


def _samples():
    manifest_path = os.path.join(SAMPLE_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        return []
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    out = []
    for item in manifest:
        path = os.path.join(SAMPLE_DIR, item["file"])
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        out.append({
            "id": item["id"],
            "title": item["title"],
            "description": item.get("description", ""),
            "text": body,
        })
    return out


@app.after_request
def _no_cache(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "app": "genealogy-evidence-forge", "version": "1.0.0"})


@app.route("/api/modes")
def modes():
    return jsonify([{"key": k, "label": v["label"], "weights": v["weights"]}
                    for k, v in MODES.items()])


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    mode = payload.get("mode") or "standard"
    if mode not in MODES:
        mode = "standard"
    if not text.strip():
        return jsonify({"error": "text is required"}), 400
    result = analyze(text, mode)
    saved_id = None
    if payload.get("save"):
        saved_id = save_session(mode, text, result)
    return jsonify({"result": result, "session_id": saved_id})


@app.route("/api/samples")
def api_samples():
    return jsonify(_samples())


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    return jsonify(list_sessions())


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    mode = payload.get("mode") or "standard"
    result = payload.get("result")
    if not result:
        if not text.strip():
            return jsonify({"error": "text is required"}), 400
        result = analyze(text, mode)
    sid = save_session(mode, text, result)
    return jsonify({"id": sid, "result": result}), 201


@app.route("/api/sessions/<int:sid>")
def api_session(sid):
    data = get_session(sid)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.route("/api/sessions/<int:sid>", methods=["PATCH"])
def api_patch_session(sid):
    payload = request.get_json(silent=True) or {}
    rating = payload.get("rating")
    notes = payload.get("notes")
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return jsonify({"error": "rating must be an integer 1-5"}), 400
        if rating < 1 or rating > 5:
            return jsonify({"error": "rating must be between 1 and 5"}), 400
    data = update_session(sid, rating=rating, notes=notes)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.route("/api/export/<int:sid>")
def api_export(sid):
    data = get_session(sid)
    if not data:
        return jsonify({"error": "not found"}), 404
    fmt = (request.args.get("fmt") or "md").lower()
    result = data["result"]
    if fmt == "md":
        body = to_markdown(result)
        mimetype = "text/markdown"
        filename = "genealogy-worksheet-{0}.md".format(sid)
    elif fmt == "csv":
        body = to_csv(result)
        mimetype = "text/csv"
        filename = "genealogy-claims-{0}.csv".format(sid)
    else:
        body = to_json(result)
        mimetype = "application/json"
        filename = "genealogy-analysis-{0}.json".format(sid)
    resp = Response(body, mimetype=mimetype)
    resp.headers["Content-Disposition"] = 'attachment; filename="{0}"'.format(filename)
    return resp


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5001, debug=False)
