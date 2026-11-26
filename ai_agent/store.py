"""AI Agent Lens の SQLite 永続化（受信パケット・Claude の判断・ペーパートレード）。"""
import json
import sqlite3
from pathlib import Path
from typing import Optional


class AgentStore:
    def __init__(self, db_path: str = "data/ai_agent.db"):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS packets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tickerid    TEXT NOT NULL,
            tf          TEXT NOT NULL,
            bar_time    INTEGER NOT NULL,
            kind        TEXT NOT NULL,
            event       TEXT,
            payload     TEXT NOT NULL,
            received_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tickerid, tf, bar_time, kind)
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id   INTEGER REFERENCES packets(id),
            tickerid    TEXT NOT NULL,
            tf          TEXT NOT NULL,
            bar_time    INTEGER NOT NULL,
            decision    TEXT NOT NULL,
            confidence  INTEGER,
            payload     TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id     INTEGER REFERENCES decisions(id),
            tickerid        TEXT NOT NULL,
            tf              TEXT NOT NULL,
            side            TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
            entry           REAL NOT NULL,
            stop            REAL NOT NULL,
            initial_stop    REAL NOT NULL,
            target1         REAL NOT NULL,
            target2         REAL NOT NULL,
            size            REAL NOT NULL,
            remaining       REAL NOT NULL,     -- 残りの割合 (1.0 → 0.5 → 0)
            target1_hit     INTEGER NOT NULL DEFAULT 0,
            risk_pct        REAL NOT NULL,
            opened_bar_time INTEGER NOT NULL,
            last_bar_time   INTEGER NOT NULL,
            status          TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED')),
            exit_reason     TEXT,
            realized_pnl    REAL NOT NULL DEFAULT 0,
            r_multiple      REAL,
            opened_at       TEXT DEFAULT (datetime('now')),
            closed_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(status, tickerid, tf);
        """)
        self.conn.commit()

    # ── packets ──
    def save_packet(self, packet: dict) -> Optional[int]:
        """保存して id を返す。同じ足・同じ種類のパケットが既にあれば None（重複配信）。"""
        public = {k: v for k, v in packet.items() if k != "secret"}
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO packets (tickerid, tf, bar_time, kind, event, payload) VALUES (?,?,?,?,?,?)",
            (packet["tickerid"], packet["tf"], int(packet["time"]), packet["kind"], packet.get("event", ""),
             json.dumps(public, ensure_ascii=False)),
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    # ── decisions ──
    def save_decision(self, packet_id: Optional[int], packet: dict, decision: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO decisions (packet_id, tickerid, tf, bar_time, decision, confidence, payload) VALUES (?,?,?,?,?,?,?)",
            (packet_id, packet["tickerid"], packet["tf"], int(packet["time"]), decision["decision"],
             decision.get("confidence"), json.dumps(decision, ensure_ascii=False)),
        )
        self.conn.commit()
        return cur.lastrowid

    def recent_decisions(self, tickerid: str, tf: str, limit: int = 3) -> list:
        rows = self.conn.execute(
            "SELECT bar_time, payload FROM decisions WHERE tickerid=? AND tf=? ORDER BY id DESC LIMIT ?",
            (tickerid, tf, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["payload"])
            out.append({"bar_time": r["bar_time"], "decision": d["decision"], "confidence": d.get("confidence"),
                        "reasoning": d.get("reasoning", "")[:200]})
        return out
