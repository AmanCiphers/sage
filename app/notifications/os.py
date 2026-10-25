"""OS-level notifications for SAGE.

Two ways notifications reach the user's desktop:
  1. Automatic — scheduled jobs, proactive actions, reminders, and background
     work (employee finished / task failed) notify silently by default.
  2. SAGE-initiated — the `send_notification` tool lets SAGE decide when
     "something important" warrants a (sounding) system nudge.

Backends (zero-dependency, fail-safe):
  - macOS : osascript (posts to Notification Center)
  - Linux : notify-send (if installed)
  - Windows: PowerShell best-effort
If the platform backend is missing or fails, we log and skip — never crash
the loop.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

from app.events.models import Event, EventTypes

logger = logging.getLogger(__name__)

# Event types that produce an automatic OS notification (mirrors the CLI's
# CompletionNotifier interest set so CLI and OS stay in sync).
_AUTO_NOTIFY_EVENTS = {
    EventTypes.EMPLOYEE_FINISHED,
    EventTypes.TASK_FAILED,
    EventTypes.SCHEDULED_JOB_TRIGGERED,
    EventTypes.SCHEDULED_JOB_COMPLETED,
    EventTypes.PROACTIVE_ACTION_TAKEN,
    "reminder",
}


def _notify_macos(title: str, message: str, sound: bool) -> bool:
    script = f'display notification {_apple_str(message)} with title {_apple_str(title)}'
    if sound:
        script += ' sound name "default"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return True
    except Exception:
        logger.debug("macos notification failed", exc_info=True)
        return False


def _apple_str(text: str) -> str:
    """Escape a string for embedding in an AppleScript literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _notify_linux(title: str, message: str, sound: bool) -> bool:
    if not shutil.which("notify-send"):
        logger.debug("notify-send not available")
        return False
    try:
        cmd = ["notify-send", "-a", "SAGE", title, message]
        if sound:
            cmd = ["notify-send", "-a", "SAGE", "-u", "critical", title, message]
        subprocess.run(cmd, check=False, capture_output=True, timeout=10)
        return True
    except Exception:
        logger.debug("linux notification failed", exc_info=True)
        return False


def _notify_windows(title: str, message: str, sound: bool) -> bool:
    try:
        ps = (
            f'New-BurntToastNotification -Text @({_apple_str(title)}, '
            f"{_apple_str(message)})"
        )
        subprocess.run(
            ["powershell", "-Command", ps],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return True
    except Exception:
        logger.debug("windows notification failed", exc_info=True)
        return False


def os_notify(title: str, message: str, sound: bool = False) -> bool:
    """Send a desktop notification. Returns True if delivered."""
    from app.config import settings

    if not settings.os_notifications_enabled:
        return False

    if sys.platform == "darwin":
        return _notify_macos(title, message, sound)
    if sys.platform.startswith("linux"):
        return _notify_linux(title, message, sound)
    if sys.platform.startswith("win"):
        return _notify_windows(title, message, sound)
    logger.debug("unsupported platform for notifications: %s", sys.platform)
    return False


def _event_line(event: Event) -> str | None:
    """Build a human line for an auto-notify event, or None if not relevant."""
    if event.event_type not in _AUTO_NOTIFY_EVENTS:
        return None

    payload = event.payload or {}

    if (
        event.event_type == "reminder"
        or event.event_type
        in (EventTypes.SCHEDULED_JOB_TRIGGERED, EventTypes.SCHEDULED_JOB_COMPLETED)
    ):
        name = payload.get("job_name") or payload.get("name") or "Scheduled job"
        body = (
            payload.get("message")
            or payload.get("reminder")
            or payload.get("body")
            or "reminder"
        )
        return f"⏰ {name}: {body}"

    if event.event_type == EventTypes.PROACTIVE_ACTION_TAKEN:
        return f"⚡ {payload.get('rule_name') or 'Proactive action'}"

    name = payload.get("name") or payload.get("worker") or "An employee"
    task_id = payload.get("task_id", "")
    if event.event_type == EventTypes.TASK_FAILED:
        return f"{name} failed {task_id}: {payload.get('error') or 'unknown error'}"
    return f"{name} finished {task_id} — {payload.get('summary') or 'finished the assigned work'}"


# Event types that should play a sound (user-requested attention): reminders,
# scheduled jobs, and proactive actions. Employee completions / task failures
# stay silent to avoid noise.
_SOUND_EVENTS = {
    "reminder",
    EventTypes.SCHEDULED_JOB_TRIGGERED,
    EventTypes.SCHEDULED_JOB_COMPLETED,
    EventTypes.PROACTIVE_ACTION_TAKEN,
}


class OsNotifier:
    """Listens to the event bus and sends desktop notifications."""

    async def handle_event(self, event: Event) -> None:
        line = _event_line(event)
        if not line:
            return
        title = "SAGE"
        sound = event.event_type in _SOUND_EVENTS
        os_notify(title, line, sound=sound)
        logger.debug("os_notification_sent event=%s sound=%s", event.event_type, sound)
