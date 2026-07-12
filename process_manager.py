"""Lifecycle management for the child anti-spam bot processes.

Design notes (these fix concrete bugs in the original single-file version):

* Each child's stdout/stderr is redirected to a dedicated log file, never to an
  unread ``PIPE``. An unread pipe buffer fills up (~64 KB) and then blocks the
  child forever on its next ``print`` -- a silent hang that no crash-monitor can
  detect.
* A recorded PID is only ever killed / considered "alive" after confirming that
  the process is actually one of *our* child bots (via ``/proc/<pid>/cmdline``).
  PIDs get recycled after a crash, so a naive ``os.kill`` can hit an unrelated
  process, and a naive liveness probe can mistake a stranger for a live bot.
* On startup, already-running children are *adopted* rather than blindly
  re-spawned, which prevents two processes polling the same token (Telegram 409).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

import config
import db

log = config.setup_logging("antispam.procmgr")

CHILD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child_bot.py")
_CHILD_MARKER = "child_bot.py"

# user_id -> Popen. Only holds processes started by *this* manager instance.
_processes: dict[int, subprocess.Popen] = {}
_lock = threading.Lock()


def _proc_cmdline(pid: int) -> Optional[str]:
    """Return the process command line, or None if the pid is gone / unreadable."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError:
        return None


def is_our_child(pid: Optional[int]) -> bool:
    """True only if ``pid`` is alive AND its command line is one of our child bots."""
    if not pid:
        return False
    cmdline = _proc_cmdline(pid)
    if cmdline is not None:
        return _CHILD_MARKER in cmdline
    # /proc unavailable (non-Linux): fall back to a liveness probe.
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _spawn(user_id: int, token: str) -> Optional[int]:
    """Start a child bot process and return its pid (or None on failure)."""
    os.makedirs(config.BOTS_LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.BOTS_LOG_DIR, f"bot_{user_id}.log")

    env = os.environ.copy()
    env["BOT_TOKEN"] = token
    env["OWNER_ID"] = str(user_id)

    log_file = open(log_path, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, CHILD_SCRIPT],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except Exception as exc:
        log.error("Error spawning bot for user %s: %s", user_id, exc)
        log_file.close()
        return None

    with _lock:
        _processes[user_id] = process
    db.set_process_id(user_id, process.pid)
    log.info("Started bot for user %s (pid %s), logging to %s", user_id, process.pid, log_path)
    return process.pid


def start_bot(user_id: int, token: str) -> Optional[int]:
    """Public entry point used by the manager after a bot is created."""
    return _spawn(user_id, token)


def is_running(user_id: int) -> bool:
    with _lock:
        proc = _processes.get(user_id)
    if proc is not None and proc.poll() is None:
        return True
    # Not tracked in-memory (e.g. adopted across a manager restart) -> check pid.
    row = db.get_user_bot(user_id)
    return bool(row) and is_our_child(row.get("process_id"))


def stop_bot(user_id: int) -> None:
    """Terminate a user's bot, refusing to kill a recycled/foreign pid."""
    with _lock:
        proc = _processes.pop(user_id, None)

    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    else:
        row = db.get_user_bot(user_id)
        pid = row.get("process_id") if row else None
        if is_our_child(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                log.error("Error killing pid %s: %s", pid, exc)

    db.set_process_id(user_id, None)
    log.info("Stopped bot for user %s", user_id)


def reconcile_on_startup() -> None:
    """Adopt live children, (re)start the rest. Prevents duplicate pollers (409)."""
    for row in db.all_user_bots():
        user_id = row["user_id"]
        pid = row.get("process_id")
        if is_our_child(pid):
            log.info("Adopting already-running bot for user %s (pid %s)", user_id, pid)
            continue
        log.info("Bot for user %s is not running; starting it", user_id)
        _spawn(user_id, row["bot_token"])


def monitor_loop(interval: int = 60) -> None:
    """Background loop: restart any child that has died."""
    while True:
        try:
            time.sleep(interval)
            for row in db.all_user_bots():
                user_id = row["user_id"]
                if is_running(user_id):
                    continue
                log.warning("Bot for user %s is down. Restarting...", user_id)
                _spawn(user_id, row["bot_token"])
        except Exception as exc:
            log.error("Error in monitor loop: %s", exc)


def shutdown_all() -> None:
    """Terminate every child this manager started (graceful shutdown)."""
    with _lock:
        items = list(_processes.items())
        _processes.clear()
    for user_id, proc in items:
        if proc.poll() is None:
            proc.terminate()
    log.info("Terminated %d child bot(s) on shutdown", len(items))
