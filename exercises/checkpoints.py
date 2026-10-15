"""SQLite checkpointer with explicit support for the lab's Pydantic types."""
from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from common.db import db_path


@asynccontextmanager
async def open_checkpointer():
    serde = JsonPlusSerializer(allowed_msgpack_modules=[
        ('common.schemas', 'PRAnalysis'), ('common.schemas', 'ReviewComment'),
    ])
    async with aiosqlite.connect(db_path()) as conn:
        saver = AsyncSqliteSaver(conn, serde=serde)
        await saver.setup()
        yield saver
