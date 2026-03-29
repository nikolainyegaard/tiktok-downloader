"""
Main download loop and shared state used by both the loop thread and the web server.
"""

import asyncio
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone

import database as db
from config import get_ms_token
from tiktok_api import get_user_info, get_user_videos
from downloader import download_video, download_photos

# ── Shared state ──────────────────────────────────────────────────────────────

loop_state = {
    "running":        False,
    "last_run_start": None,
    "last_run_end":   None,
    "next_run":       None,
    "current_user":   None,
    "logs":           deque(maxlen=1000),
}
_state_lock   = threading.Lock()
trigger_event = threading.Event()


# ── Public accessors (keeps _state_lock internal to this module) ──────────────

def is_running() -> bool:
    with _state_lock:
        return loop_state["running"]


def set_next_run(iso: str) -> None:
    with _state_lock:
        loop_state["next_run"] = iso


def get_state_snapshot(log_lines: int = 200) -> dict:
    """Return a serialisable copy of loop_state."""
    with _state_lock:
        state = {k: v for k, v in loop_state.items() if k != "logs"}
        state["logs"] = list(loop_state["logs"])[-log_lines:]
    return state


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with _state_lock:
        loop_state["logs"].append(line)


# ── Core async logic ──────────────────────────────────────────────────────────

async def _process_all_users(users: list[dict]):
    from TikTokApi import TikTokApi

    ms_token = get_ms_token()

    async with TikTokApi() as api:
        await api.create_sessions(
            ms_tokens=[ms_token], num_sessions=1, sleep_after=3,
            browser="webkit",
        )

        for idx, user in enumerate(users):
            if idx > 0:
                await asyncio.sleep(random.uniform(2, 5))
            tiktok_id = user["tiktok_id"]

            with _state_lock:
                loop_state["current_user"] = user["username"]

            _log(f"Processing @{user['username']} (ID: {tiktok_id})")

            try:
                info = await get_user_info(api, user["username"])
                db.update_user_info(
                    tiktok_id,
                    info["username"],
                    info["display_name"],
                    info["bio"],
                    info["follower_count"],
                    info["following_count"],
                    info["video_count"],
                    info["account_status"],
                )
                username     = info["username"]
                display_name = info["display_name"] or username
                if username != user["username"]:
                    _log(f"  Username changed: @{user['username']} → @{username}")
            except Exception as e:
                _log(f"  Failed to fetch profile info: {e}")
                username     = user["username"]
                display_name = user.get("display_name") or username

            try:
                remote_videos = await get_user_videos(api, username)
                _log(f"  {len(remote_videos)} videos visible on TikTok")
            except Exception as e:
                _log(f"  Failed to fetch video list: {e}")
                continue

            remote_ids            = {v["video_id"] for v in remote_videos}
            known_ids, active_ids = db.get_video_id_sets(tiktok_id)

            new_ids       = remote_ids - known_ids
            deleted_ids   = active_ids - remote_ids
            undeleted_ids = (known_ids - active_ids) & remote_ids

            if new_ids:
                _log(f"  New: {len(new_ids)}")
            if deleted_ids:
                _log(f"  Deleted: {len(deleted_ids)}")
            if undeleted_ids:
                _log(f"  Undeleted: {len(undeleted_ids)}")
            if not (new_ids or deleted_ids or undeleted_ids):
                _log("  No changes.")

            video_map = {v["video_id"]: v for v in remote_videos}
            for vid_id in new_ids:
                v = video_map[vid_id]
                db.add_video(
                    vid_id, tiktok_id, v["type"],
                    v["description"], v["upload_date"]
                )
                if v["type"] == "photo" and v.get("image_urls"):
                    path = download_photos(
                        video_id=vid_id,
                        username=username,
                        image_urls=v["image_urls"],
                        upload_date=v["upload_date"],
                    )
                else:
                    path = download_video(
                        video_id=vid_id,
                        username=username,
                        tiktok_id=tiktok_id,
                        display_name=display_name,
                        description=v["description"],
                        upload_date=v["upload_date"],
                        download_date=int(time.time()),
                    )
                if path:
                    db.update_video_downloaded(vid_id, path)

            for vid_id in deleted_ids:
                db.mark_video_deleted(vid_id)
                _log(f"  Marked deleted: {vid_id}")

            for vid_id in undeleted_ids:
                db.mark_video_undeleted(vid_id)
                _log(f"  Marked undeleted: {vid_id}")

    with _state_lock:
        loop_state["current_user"] = None


# ── Public entry point ────────────────────────────────────────────────────────

def run_loop():
    with _state_lock:
        loop_state["running"]        = True
        loop_state["last_run_start"] = datetime.now(timezone.utc).isoformat()

    _log("=== Loop started ===")
    users = db.get_all_users()

    if not users:
        _log("No users configured — nothing to do.")
    else:
        try:
            asyncio.run(_process_all_users(users))
        except Exception as e:
            _log(f"Unhandled loop error: {e}")

    _log("=== Loop complete ===")
    with _state_lock:
        loop_state["running"]      = False
        loop_state["last_run_end"] = datetime.now(timezone.utc).isoformat()
