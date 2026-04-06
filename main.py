import os
import sys
import logging
import threading
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler

import database as db
from config import DATA_DIR, USER_LOOP_INTERVAL_MINUTES, SOUND_LOOP_INTERVAL_MINUTES, WEB_PORT
from loop import (
    run_user_loop, run_sound_loop,
    is_user_loop_running, is_sound_loop_running,
    set_user_loop_next_run, set_sound_loop_next_run,
    trigger_user_event, trigger_sound_event,
    check_and_clear_user_reschedule, check_and_clear_sound_reschedule,
)
from web import create_app

LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# ── Per-run log: run_current.log ──────────────────────────────────────────────
#
# On every startup the previous run_current.log is renamed to a timestamped
# file (run_YYYYMMDD_HHMMSS.log) so each run is self-contained and easy to
# retrieve.  Old run files beyond _RUN_LOG_KEEP are deleted automatically.
# The current run is always at the predictable path run_current.log.

_RUN_LOG_KEEP   = 10
_run_current    = os.path.join(LOGS_DIR, "run_current.log")

if os.path.exists(_run_current):
    _run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        os.rename(_run_current, os.path.join(LOGS_DIR, f"run_{_run_ts}.log"))
    except OSError:
        pass
    _old_runs = sorted(
        f for f in os.listdir(LOGS_DIR)
        if f.startswith("run_") and f != "run_current.log"
    )
    for _old in _old_runs[:-_RUN_LOG_KEEP]:  # keep the newest _RUN_LOG_KEEP
        try:
            os.remove(os.path.join(LOGS_DIR, _old))
        except OSError:
            pass

_run_file = open(_run_current, "w", encoding="utf-8", buffering=1)  # line-buffered


# ── Application log: stdout/stderr → daily-rotating transcript.log ────────────
#
# _Tee intercepts all print() output and writes it to the rotating file AND
# the per-run file.  We call shouldRollover/doRollover manually because
# writing directly to .stream bypasses the emit() path that normally triggers
# rotation.

_tee_lock = threading.Lock()   # prevents interleaved writes from concurrent threads

class _Tee:
    def __init__(self, original, handler, run_file):
        self._original = original
        self._handler  = handler
        self._run_file = run_file

    def write(self, msg):
        self._original.write(msg)
        if msg:
            with _tee_lock:
                try:
                    # TimedRotatingFileHandler.shouldRollover() checks time only,
                    # so the LogRecord argument is ignored — a dummy is fine.
                    _dummy = logging.LogRecord("", logging.INFO, "", 0, "", [], None)
                    if self._handler.shouldRollover(_dummy):
                        self._handler.doRollover()
                    self._handler.stream.write(msg)
                    self._handler.stream.flush()
                except Exception:
                    pass  # never let log I/O crash the application
                try:
                    self._run_file.write(msg)
                except Exception:
                    pass

    def flush(self):
        self._original.flush()
        try:
            self._handler.stream.flush()
        except Exception:
            pass
        try:
            self._run_file.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._original, name)


_transcript_handler = TimedRotatingFileHandler(
    os.path.join(LOGS_DIR, "transcript.log"),
    when="midnight", backupCount=30, encoding="utf-8",
)
_transcript_handler.setFormatter(logging.Formatter("%(message)s"))
sys.stdout = _Tee(sys.__stdout__, _transcript_handler, _run_file)
sys.stderr = _Tee(sys.__stderr__, _transcript_handler, _run_file)

# ── HTTP access log filter ─────────────────────────────────────────────────────
#
# The frontend polls /api/status every 5 s, /api/queue every 3 s, and
# /api/users every 15 s.  Those GET requests are completely uninteresting and
# would otherwise make up ~95 % of the log file.  Filter them from werkzeug's
# logger so only meaningful HTTP activity reaches the transcript.

_POLLING_ENDPOINTS = (
    '"GET /api/status HTTP',
    '"GET /api/queue HTTP',
    '"GET /api/users HTTP',
    '"GET /api/sounds HTTP',
)

class _SuppressPolling(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return not any(pat in msg for pat in _POLLING_ENDPOINTS)

logging.getLogger("werkzeug").addFilter(_SuppressPolling())


def _ts() -> str:
    return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"


# ── User loop scheduler ───────────────────────────────────────────────────────

def _user_loop_thread():
    while True:
        interval_minutes = int(db.get_setting("user_loop_interval_minutes", USER_LOOP_INTERVAL_MINUTES))
        next_at_ts = time.time() + interval_minutes * 60
        set_user_loop_next_run(datetime.fromtimestamp(next_at_ts, tz=timezone.utc).isoformat())
        print(
            f"{_ts()} User loop sleeping {interval_minutes} min"
            f" until {datetime.fromtimestamp(next_at_ts).strftime('%H:%M:%S')}."
        )

        remaining = next_at_ts - time.time()
        triggered = trigger_user_event.wait(timeout=max(remaining, 0))
        trigger_user_event.clear()

        if check_and_clear_user_reschedule():
            print(f"{_ts()} User loop: interval changed, rescheduling.")
            continue

        if triggered:
            print(f"{_ts()} User loop: manual trigger received.")

        set_user_loop_next_run(None)

        # Smart avoidance: wait for sound loop to finish, then add 5 min buffer
        was_waiting = False
        while is_sound_loop_running():
            was_waiting = True
            time.sleep(30)
        if was_waiting:
            print(f"{_ts()} User loop: sound loop finished, waiting 5 min buffer.")
            time.sleep(5 * 60)

        run_user_loop()


# ── Sound loop scheduler ──────────────────────────────────────────────────────

def _sound_loop_thread():
    while True:
        interval_minutes = int(db.get_setting("sound_loop_interval_minutes", SOUND_LOOP_INTERVAL_MINUTES))
        next_at_ts = time.time() + interval_minutes * 60
        set_sound_loop_next_run(datetime.fromtimestamp(next_at_ts, tz=timezone.utc).isoformat())
        print(
            f"{_ts()} Sound loop sleeping {interval_minutes} min"
            f" until {datetime.fromtimestamp(next_at_ts).strftime('%H:%M:%S')}."
        )

        remaining = next_at_ts - time.time()
        triggered = trigger_sound_event.wait(timeout=max(remaining, 0))
        trigger_sound_event.clear()

        if check_and_clear_sound_reschedule():
            print(f"{_ts()} Sound loop: interval changed, rescheduling.")
            continue

        if triggered:
            print(f"{_ts()} Sound loop: manual trigger received.")

        set_sound_loop_next_run(None)

        # Smart avoidance: wait for user loop to finish, then add 5 min buffer
        was_waiting = False
        while is_user_loop_running():
            was_waiting = True
            time.sleep(30)
        if was_waiting:
            print(f"{_ts()} Sound loop: user loop finished, waiting 5 min buffer.")
            time.sleep(5 * 60)

        run_sound_loop()


# ── Entry point ───────────────────────────────────────────────────────────────

def _check_config() -> None:
    """Warn about outdated docker-compose.yml env var patterns."""
    # Legacy LOOP_INTERVAL_MINUTES without the split replacements.
    # The old var still works via backward-compat in config.py, but
    # SOUND_LOOP_INTERVAL_MINUTES will silently use its hardcoded default.
    has_legacy  = bool(os.environ.get("LOOP_INTERVAL_MINUTES"))
    has_user    = bool(os.environ.get("USER_LOOP_INTERVAL_MINUTES"))
    has_sound   = bool(os.environ.get("SOUND_LOOP_INTERVAL_MINUTES"))

    if has_legacy and not (has_user and has_sound):
        print(
            f"{_ts()} [config] WARNING: your docker-compose.yml uses the deprecated\n"
            f"  LOOP_INTERVAL_MINUTES variable. Replace it with the two current variables:\n"
            f"\n"
            f"    USER_LOOP_INTERVAL_MINUTES:  \"180\"  # how often to check tracked users\n"
            f"    SOUND_LOOP_INTERVAL_MINUTES: \"60\"   # how often to check tracked sounds\n"
            f"\n"
            f"  Until then, SOUND_LOOP_INTERVAL_MINUTES defaults to 60 min."
        )


if __name__ == "__main__":
    _check_config()
    print(f"{_ts()} Initialising database...")
    db.init_db()

    n = db.migrate_del_prefix()
    if n:
        print(f"{_ts()} Migration: renamed {n} del_-prefixed video file(s) and updated DB paths.")

    n = db.migrate_username_history_to_profile_history()
    print(f"{_ts()} Migration: {n} username history record(s) in profile_history.")

    app = create_app()

    print(f"{_ts()} Starting loop threads...")
    threading.Thread(target=_user_loop_thread,  daemon=True, name="user-loop-thread").start()
    threading.Thread(target=_sound_loop_thread, daemon=True, name="sound-loop-thread").start()

    print(f"{_ts()} Web UI available at http://0.0.0.0:{WEB_PORT}")
    try:
        app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print(f"\n{_ts()} Shutting down.")
