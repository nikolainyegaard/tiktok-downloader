"""
Flask web application — user management UI and API.
"""

import asyncio
import os
from flask import Flask, jsonify, request, render_template

import database as db
from config import get_ms_token, cookies_info, COOKIES_PATH, DATA_DIR
from tiktok_api import get_user_info
from loop import is_running, get_state_snapshot, trigger_event


def create_app() -> Flask:
    app = Flask(__name__)

    # ── Pages ─────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("index.html")

    # ── Cookie API ────────────────────────────────────────────────────────────

    @app.route("/api/cookies", methods=["GET"])
    def get_cookies():
        return jsonify(cookies_info())

    @app.route("/api/cookies", methods=["POST"])
    def upload_cookies():
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        os.makedirs(DATA_DIR, exist_ok=True)
        f.save(COOKIES_PATH)
        return jsonify({"ok": True, **cookies_info()})

    @app.route("/api/cookies", methods=["DELETE"])
    def delete_cookies():
        if os.path.exists(COOKIES_PATH):
            os.remove(COOKIES_PATH)
        return jsonify({"ok": True})

    # ── User API ──────────────────────────────────────────────────────────────

    @app.route("/api/users", methods=["GET"])
    def list_users():
        users       = db.get_all_users()
        all_stats   = db.get_all_video_stats()
        all_history = db.get_all_username_history()

        for user in users:
            tid   = user["tiktok_id"]
            stats = all_stats.get(tid, {})
            user["video_total"]      = stats.get("video_total",      0)
            user["video_downloaded"] = stats.get("video_downloaded",  0)
            user["video_deleted"]    = stats.get("video_deleted",     0)
            user["video_undeleted"]  = stats.get("video_undeleted",   0)
            cur  = user["username"]
            user["old_usernames"] = list(dict.fromkeys(
                u for u in all_history.get(tid, []) if u != cur
            ))
        return jsonify(users)

    @app.route("/api/users", methods=["POST"])
    def add_user():
        body     = request.get_json(silent=True) or {}
        username = body.get("username", "").strip().lstrip("@")

        if not username:
            return jsonify({"error": "username is required"}), 400

        # Prevent duplicates (case-insensitive username check)
        existing = db.get_all_users()
        if any(u["username"].lower() == username.lower() for u in existing):
            return jsonify({"error": "User is already being tracked"}), 409

        ms_token = get_ms_token()

        async def _lookup():
            from TikTokApi import TikTokApi
            async with TikTokApi() as api:
                await api.create_sessions(
                    ms_tokens=[ms_token], num_sessions=1, sleep_after=3
                )
                return await get_user_info(api, username)

        try:
            info = asyncio.run(_lookup())
        except Exception as e:
            return jsonify({"error": f"TikTok API error: {e}"}), 502

        if not info.get("tiktok_id"):
            return jsonify({"error": "User not found"}), 404

        # Second duplicate check: same tiktok_id (different username casing / redirect)
        if db.get_user(info["tiktok_id"]):
            return jsonify({"error": "User is already being tracked"}), 409

        db.add_user(
            tiktok_id=info["tiktok_id"],
            username=info["username"],
            display_name=info["display_name"],
            bio=info["bio"],
            follower_count=info["follower_count"],
            following_count=info["following_count"],
            video_count=info["video_count"],
            join_date=info["join_date"],
        )
        return jsonify({"ok": True, "user": info}), 201

    @app.route("/api/users/<tiktok_id>", methods=["DELETE"])
    def remove_user(tiktok_id: str):
        db.remove_user(tiktok_id)
        return jsonify({"ok": True})

    @app.route("/api/users/<tiktok_id>/videos", methods=["GET"])
    def user_videos(tiktok_id: str):
        return jsonify(db.get_videos_for_user(tiktok_id))

    # ── Loop API ──────────────────────────────────────────────────────────────

    @app.route("/api/status", methods=["GET"])
    def get_status():
        return jsonify(get_state_snapshot())

    @app.route("/api/trigger", methods=["POST"])
    def trigger_now():
        if is_running():
            return jsonify({"error": "Loop is already running"}), 409
        trigger_event.set()
        return jsonify({"ok": True})

    return app
