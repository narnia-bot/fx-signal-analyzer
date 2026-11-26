"""
ペーパートレード（仮想口座）。実際の発注は一切しない。

ルール:
  - エントリーはパケットの終値で成行約定とみなす
  - 数量 = 口座残高 × risk_pct% ÷ |エントリー − 損切り|
  - 利確①で半分決済し、残りの損切りを建値へ移動
  - 利確②で残りを決済
  - 同じ足で損切りと利確の両方に届いた場合は損切りを優先（保守的）
"""
from dataclasses import dataclass
from typing import List, Optional

from ai_agent.store import AgentStore


@dataclass
class TradeEvent:
    trade_id: int
    tickerid: str
    tf: str
    side: str
    kind: str        # target1_hit / target2_hit / stop_hit / breakeven_stop
    price: float
    pnl: float       # このイベントで確定した損益
    equity: float    # イベント後の口座残高


class PaperTrader:
    def __init__(self, store: AgentStore, initial_equity: float = 10000.0):
        self.store = store
        self.initial_equity = initial_equity

    @property
    def conn(self):
        return self.store.conn

    def equity(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM trades").fetchone()
        return self.initial_equity + row["p"]

    def open_trades(self, tickerid: Optional[str] = None, tf: Optional[str] = None) -> list:
        q, args = "SELECT * FROM trades WHERE status='OPEN'", []
        if tickerid:
            q += " AND tickerid=?"
            args.append(tickerid)
        if tf:
            q += " AND tf=?"
            args.append(tf)
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def account_snapshot(self) -> dict:
        return {
            "initial_equity": self.initial_equity,
            "equity": round(self.equity(), 2),
            "open_positions": [
                {k: t[k] for k in ("tickerid", "tf", "side", "entry", "stop", "target1", "target2", "remaining", "target1_hit")}
                for t in self.open_trades()
            ],
        }

    def open_trade(self, decision_id: int, packet: dict, d: dict) -> Optional[dict]:
        """Claude の判断からポジションを建てる。同じ銘柄・時間足で保有中なら建てない。"""
        if self.open_trades(packet["tickerid"], packet["tf"]):
            return None
        risk_per_unit = abs(d["entry"] - d["stop"])
        if risk_per_unit <= 0 or d["risk_pct"] <= 0:
            return None
        size = self.equity() * d["risk_pct"] / 100 / risk_per_unit
        cur = self.conn.execute(
            """INSERT INTO trades (decision_id, tickerid, tf, side, entry, stop, initial_stop, target1, target2,
                                   size, remaining, risk_pct, opened_bar_time, last_bar_time)
               VALUES (?,?,?,?,?,?,?,?,?,?,1.0,?,?,?)""",
            (decision_id, packet["tickerid"], packet["tf"], d["decision"], d["entry"], d["stop"], d["stop"],
             d["target1"], d["target2"], size, d["risk_pct"], int(packet["time"]), int(packet["time"])),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM trades WHERE id=?", (cur.lastrowid,)).fetchone())

    def on_packet(self, packet: dict) -> List[TradeEvent]:
        """パケットに含まれる確定足（直近5本）で、保有ポジションの損切り・利確を判定する。"""
        bars = sorted(packet.get("recent") or [], key=lambda b: b["t"])
        if not bars:
            b = packet["bar"]
            bars = [{"t": packet["time"], "h": b["h"], "l": b["l"]}]
        events: List[TradeEvent] = []
        for trade in self.open_trades(packet["tickerid"], packet["tf"]):
            for bar in bars:
                if bar["t"] <= trade["last_bar_time"]:
                    continue
                events.extend(self._apply_bar(trade, bar))
                trade["last_bar_time"] = bar["t"]
                if trade["status"] == "CLOSED":
                    break
            self._save(trade)
        return events

    def _apply_bar(self, t: dict, bar: dict) -> List[TradeEvent]:
        long = t["side"] == "LONG"
        sign = 1 if long else -1
        hi, lo = float(bar["h"]), float(bar["l"])
        stop_hit = lo <= t["stop"] if long else hi >= t["stop"]
        t1_hit = hi >= t["target1"] if long else lo <= t["target1"]
        t2_hit = hi >= t["target2"] if long else lo <= t["target2"]
        out = []

        def realize(kind: str, price: float, fraction: float):
            pnl = sign * (price - t["entry"]) * t["size"] * fraction
            t["realized_pnl"] += pnl
            t["remaining"] -= fraction
            self._save(t)
            out.append(TradeEvent(t["id"], t["tickerid"], t["tf"], t["side"], kind, price, pnl, self.equity()))

        if stop_hit:
            realize("breakeven_stop" if t["target1_hit"] else "stop_hit", t["stop"], t["remaining"])
            self._close(t, out[-1].kind)
        elif t2_hit:
            if not t["target1_hit"]:
                t["target1_hit"] = 1
                realize("target1_hit", t["target1"], 0.5)
            realize("target2_hit", t["target2"], t["remaining"])
            self._close(t, "target2_hit")
        elif t1_hit and not t["target1_hit"]:
            t["target1_hit"] = 1
            realize("target1_hit", t["target1"], 0.5)
            t["stop"] = t["entry"]
        return out

    def _close(self, t: dict, reason: str):
        t["status"] = "CLOSED"
        t["exit_reason"] = reason
        t["remaining"] = 0.0
        initial_risk = abs(t["entry"] - t["initial_stop"]) * t["size"]
        t["r_multiple"] = t["realized_pnl"] / initial_risk if initial_risk else None
        self._save(t, closed=True)

    def _save(self, t: dict, closed: bool = False):
        self.conn.execute(
            f"""UPDATE trades SET stop=?, remaining=?, target1_hit=?, last_bar_time=?, status=?, exit_reason=?,
                   realized_pnl=?, r_multiple=? {", closed_at=datetime('now')" if closed else ""} WHERE id=?""",
            (t["stop"], t["remaining"], t["target1_hit"], t["last_bar_time"], t["status"], t.get("exit_reason"),
             t["realized_pnl"], t.get("r_multiple"), t["id"]),
        )
        self.conn.commit()

    def stats(self) -> dict:
        closed = [dict(r) for r in self.conn.execute("SELECT * FROM trades WHERE status='CLOSED'").fetchall()]
        wins = [t for t in closed if t["realized_pnl"] > 0]
        losses = [t for t in closed if t["realized_pnl"] < 0]
        gross_win = sum(t["realized_pnl"] for t in wins)
        gross_loss = -sum(t["realized_pnl"] for t in losses)
        rs = [t["r_multiple"] for t in closed if t["r_multiple"] is not None]
        return {
            "equity": round(self.equity(), 2),
            "return_pct": round((self.equity() / self.initial_equity - 1) * 100, 2),
            "closed_trades": len(closed),
            "open_trades": len(self.open_trades()),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        }
