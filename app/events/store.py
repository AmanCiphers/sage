"""Event store for persisting and querying events."""

import json
from typing import Any

from app.persistence.database import Database
from app.events.models import Event


class EventStore:
    """Persisted event log with FTS5 search (ArchitectureV2 §37-39)."""
    
    def __init__(self, database: Database):
        self.database = database
    
    async def initialize(self) -> None:
        """Create events table and FTS5 index."""
        await self.database.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        TEXT NOT NULL UNIQUE,
                event_type      TEXT NOT NULL,
                source          TEXT NOT NULL,
                payload_json    TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                sequence_number INTEGER NOT NULL DEFAULT 0,
                correlation_id  TEXT
            )
        """)
        await self.database.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type_time 
            ON events(event_type, timestamp DESC)
        """)
        await self.database.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_correlation 
            ON events(correlation_id)
        """)
        await self.database.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_event_id 
            ON events(event_id)
        """)
        await self.database.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
            USING fts5(payload, event_type UNINDEXED, source UNINDEXED)
        """)
        await self.database.commit()
    
    async def append(self, event: Event) -> None:
        """Persist an event."""
        cursor = await self.database.execute(
            """
            INSERT INTO events (event_id, event_type, source, payload_json, timestamp, sequence_number, correlation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.source,
                json.dumps(event.payload),
                event.timestamp,
                event.sequence_number,
                event.correlation_id,
            ),
        )
        rowid = cursor.lastrowid
        # Also index in FTS5 using the integer rowid
        await self.database.execute(
            """
            INSERT INTO events_fts (rowid, payload, event_type, source)
            VALUES (?, ?, ?, ?)
            """,
            (
                rowid,
                json.dumps(event.payload),
                event.event_type,
                event.source,
            ),
        )
        await self.database.commit()
    
    async def get_recent(self, limit: int = 100, event_type: str | None = None) -> list[Event]:
        """Get recent events, optionally filtered by type."""
        if event_type:
            cursor = await self.database.execute(
                """
                SELECT event_id, event_type, source, payload_json, timestamp, sequence_number, correlation_id
                FROM events
                WHERE event_type = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (event_type, limit),
            )
        else:
            cursor = await self.database.execute(
                """
                SELECT event_id, event_type, source, payload_json, timestamp, sequence_number, correlation_id
                FROM events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]
    
    async def get_by_correlation(self, correlation_id: str) -> list[Event]:
        """Get all events with a given correlation ID (ArchitectureV2 §43)."""
        cursor = await self.database.execute(
            """
            SELECT event_id, event_type, source, payload_json, timestamp, sequence_number, correlation_id
            FROM events
            WHERE correlation_id = ?
            ORDER BY sequence_number ASC
            """,
            (correlation_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]
    
    async def search(self, query: str, limit: int = 20) -> list[Event]:
        """Full-text search over event payloads."""
        cursor = await self.database.execute(
            """
            SELECT e.event_id, e.event_type, e.source, e.payload_json, e.timestamp, e.sequence_number, e.correlation_id
            FROM events_fts f
            JOIN events e ON e.id = f.rowid
            WHERE events_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]
    
    def _row_to_event(self, row: tuple) -> Event:
        return Event(
            event_id=row[0],
            event_type=row[1],
            source=row[2],
            payload=json.loads(row[3]),
            timestamp=row[4],
            sequence_number=row[5],
            correlation_id=row[6],
        )