"""SQLite 时序存储（v0 极简实现，schema 演进见架构文档）。

表 samples(ts REAL, engine TEXT, payload TEXT)：
payload 为 Sample 的 JSON 序列化。规则引擎阶段再做窗口聚合查询。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .collector.models import Sample


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._init()

    def _init(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts      REAL NOT NULL,
                engine  TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
        self._conn.commit()

    def insert_sample(self, sample: Sample) -> None:
        self._conn.execute(
            "INSERT INTO samples (ts, engine, payload) VALUES (?, ?, ?)",
            (sample.ts, sample.engine, json.dumps(sample.to_dict())),
        )
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        return int(row[0]) if row else 0

    def latest(self, n: int = 10) -> list[Sample]:
        rows = self._conn.execute(
            "SELECT payload FROM samples ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
        return [Sample(**json.loads(r[0])) for r in rows]

    def close(self) -> None:
        self._conn.close()
