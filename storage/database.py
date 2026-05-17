# storage/database.py
"""
シグナルデータの永続化。SQLiteを使用（後でPostgreSQLに移行可能）。
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from parsers.signal_parser import FXSignal


class SignalDatabase:
    def __init__(self, db_path: str = "data/signals.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id      INTEGER NOT NULL,
            channel         TEXT NOT NULL,
            raw_text        TEXT NOT NULL,
            timestamp       TEXT NOT NULL,

            pair            TEXT,
            direction       TEXT CHECK(direction IN ('BUY', 'SELL', NULL)),
            entry_price     REAL,
            stop_loss       REAL,
            take_profits    TEXT,   -- JSON array
            timeframe       TEXT,
            confidence      REAL,

            outcome         TEXT CHECK(outcome IN ('WIN','LOSS','PENDING','CANCELLED', NULL)),
            pips_result     REAL,
            closed_at       TEXT,
            parse_error     TEXT,

            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(channel, message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair);
        CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
        CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);

        -- シグナル分析結果のキャッシュ
        CREATE TABLE IF NOT EXISTS signal_analysis (
            signal_id       INTEGER PRIMARY KEY REFERENCES signals(id),
            pattern_tags    TEXT,   -- JSON array of detected patterns
            entry_reason    TEXT,   -- Claudeが推論したエントリー根拠
            market_context  TEXT,   -- そのときの相場状況
            analyzed_at     TEXT DEFAULT (datetime('now'))
        );
        """)
        self.conn.commit()

    def upsert_signal(self, signal: FXSignal) -> int:
        """シグナルを保存（既存の場合は更新）"""
        cursor = self.conn.execute("""
        INSERT INTO signals (
            message_id, channel, raw_text, timestamp,
            pair, direction, entry_price, stop_loss,
            take_profits, timeframe, confidence,
            outcome, pips_result, closed_at, parse_error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel, message_id) DO UPDATE SET
            pair = excluded.pair,
            direction = excluded.direction,
            entry_price = excluded.entry_price,
            stop_loss = excluded.stop_loss,
            take_profits = excluded.take_profits,
            outcome = excluded.outcome,
            pips_result = excluded.pips_result,
            closed_at = excluded.closed_at
        RETURNING id
        """, (
            signal.message_id, signal.channel, signal.raw_text,
            signal.timestamp.isoformat(),
            signal.pair, signal.direction, signal.entry_price,
            signal.stop_loss,
            json.dumps(signal.take_profits),
            signal.timeframe, signal.confidence,
            signal.outcome, signal.pips_result,
            signal.closed_at.isoformat() if signal.closed_at else None,
            signal.parse_error,
        ))
        row = cursor.fetchone()
        self.conn.commit()
        return row[0] if row else -1

    def get_signals(self, pair: str = None, outcome: str = None,
                    limit: int = 100) -> List[dict]:
        """シグナル一覧を取得"""
        query = "SELECT * FROM signals WHERE 1=1"
        params = []
        if pair:
            query += " AND pair = ?"
            params.append(pair.upper())
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)
        query += f" ORDER BY timestamp DESC LIMIT {limit}"

        rows = self.conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["take_profits"] = json.loads(d["take_profits"] or "[]")
            results.append(d)
        return results

    def get_stats(self) -> dict:
        """全体統計を取得"""
        row = self.conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'WIN'  THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN outcome IN ('WIN','LOSS') THEN pips_result END) as avg_pips,
            AVG(CASE WHEN outcome IS NULL THEN 1.0 ELSE 0 END) as pending_rate
        FROM signals
        WHERE pair IS NOT NULL
        """).fetchone()

        d = dict(row)
        total_closed = (d["wins"] or 0) + (d["losses"] or 0)
        d["win_rate"] = round(d["wins"] / total_closed, 3) if total_closed else None
        return d

    def get_stats_by_pair(self) -> List[dict]:
        """通貨ペア別統計"""
        rows = self.conn.execute("""
        SELECT
            pair,
            COUNT(*) as total,
            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses,
            AVG(pips_result) as avg_pips,
            AVG(CASE WHEN direction='BUY' THEN 1.0 ELSE 0 END) as buy_ratio
        FROM signals
        WHERE pair IS NOT NULL
        GROUP BY pair
        ORDER BY total DESC
        """).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            closed = (d["wins"] or 0) + (d["losses"] or 0)
            d["win_rate"] = round(d["wins"] / closed, 3) if closed else None
            results.append(d)
        return results

    def close(self):
        self.conn.close()
