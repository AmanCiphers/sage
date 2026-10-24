"""Tests for the timezone-aware scheduler."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.scheduler.engine import _iso_utc, _next_run, _tz_for


def test_next_run_in_ist_is_correct_utc():
    """'0 9 25 10 *' in Asia/Kolkata == 09:00 IST = 03:30 UTC on Oct 25."""
    nxt = _next_run("0 9 25 10 *", "Asia/Kolkata")
    assert nxt.tzinfo is not None  # must be timezone-aware
    # Convert back to IST to confirm it's 09:00 there.
    ist = nxt.astimezone(ZoneInfo("Asia/Kolkata"))
    assert ist.hour == 9 and ist.minute == 0
    assert ist.day == 25 and ist.month == 10
    # As UTC it is 03:30.
    assert nxt.hour == 3 and nxt.minute == 30


def test_iso_utc_formats_with_z():
    dt = datetime(2026, 10, 25, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    iso = _iso_utc(dt)
    assert iso == "2026-10-25T03:30:00Z"


def test_tz_for_unknown_falls_back_to_utc():
    tz = _tz_for("Not/AZone")
    assert tz == timezone.utc


async def test_run_at_one_time_job_uses_utc_and_max_runs_1():
    """run_at must parse as UTC (not crash on timezone shadowing) and default
    to max_runs=1 so a one-time reminder fires once then disables."""
    import tempfile
    from pathlib import Path

    from app.events.bus import EventBus
    from app.events.store import EventStore
    from app.persistence.database import Database
    from app.persistence.schema import initialize_schema
    from app.scheduler.engine import SchedulerEngine
    from app.scheduler.store import SchedulerStore

    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    await db.connect()
    await initialize_schema(db)
    store = SchedulerStore(db)
    await store.initialize()
    eng = SchedulerEngine(store, EventBus(EventStore(db)))

    # 22:53 IST on 2026-10-24 == 17:23 UTC.
    jid = await eng.add_job(
        name="one-off",
        cron_expression=None,
        event_type="reminder",
        run_at="2026-10-24T22:53:04+05:30",
        timezone="Asia/Kolkata",
    )
    job = await store.get_job(jid)
    assert job.next_run_at == "2026-10-24T17:23:04Z"
    assert job.max_runs == 1
    await db.close()