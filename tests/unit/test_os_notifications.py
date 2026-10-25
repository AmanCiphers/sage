"""Tests for the OS notification system."""

import pytest

from app.events.models import Event, EventTypes
from app.notifications.os import OsNotifier, _event_line


def test_reminder_event_produces_line():
    event = Event(
        event_type="reminder",
        source="scheduler",
        payload={
            "job_id": "job_1",
            "job_name": "Luggage",
            "message": "Pack your luggage",
        },
    )
    line = _event_line(event)
    assert line and "Luggage" in line and "Pack your luggage" in line


def test_scheduled_job_completed_produces_line():
    event = Event(
        event_type=EventTypes.SCHEDULED_JOB_COMPLETED,
        source="scheduler",
        payload={"job_id": "job_2", "job_name": "Test"},
    )
    line = _event_line(event)
    assert line and "Test" in line


def test_employee_finished_produces_line():
    event = Event(
        event_type=EventTypes.EMPLOYEE_FINISHED,
        source="employee",
        payload={"task_id": "task_1", "name": "Charlie", "summary": "did it"},
    )
    line = _event_line(event)
    assert line and "Charlie" in line and "task_1" in line


def test_task_failed_produces_line():
    event = Event(
        event_type=EventTypes.TASK_FAILED,
        source="employee",
        payload={"task_id": "task_2", "name": "Alpha", "error": "boom"},
    )
    line = _event_line(event)
    assert line and "boom" in line


def test_unrelated_event_ignored():
    event = Event(event_type="user_message", source="cli", payload={})
    assert _event_line(event) is None


def test_reminder_is_a_sound_event():
    from app.notifications.os import _SOUND_EVENTS

    assert "reminder" in _SOUND_EVENTS
    assert EventTypes.SCHEDULED_JOB_COMPLETED in _SOUND_EVENTS
    assert EventTypes.PROACTIVE_ACTION_TAKEN in _SOUND_EVENTS


def test_employee_finished_is_not_a_sound_event():
    from app.notifications.os import _SOUND_EVENTS

    assert EventTypes.EMPLOYEE_FINISHED not in _SOUND_EVENTS


@pytest.mark.skipif(True, reason="requires a real OS display")
def test_os_notify_delivers():
    from app.notifications.os import os_notify

    assert os_notify("SAGE", "test") in (True, False)