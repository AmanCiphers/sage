from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = await aiosqlite.connect(self.path)

        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA foreign_keys=ON;")
        await self.connection.execute("PRAGMA busy_timeout=5000;")

        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def execute(self, query: str, parameters: tuple = ()):
        if self.connection is None:
            raise RuntimeError("Database is not connected")

        return await self.connection.execute(query, parameters)

    async def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Database is not connected")

        await self.connection.commit()
