"""受信パケット 1 件の処理: 保存 → ペーパートレード決済判定 → (SETUP なら) Claude 判断 → 通知。"""
import logging
from typing import Optional

from ai_agent.config import AgentConfig
from ai_agent.judge import TradeJudge
from ai_agent.notify import Notifier, format_decision, format_trade_event
from ai_agent.paper_trader import PaperTrader
from ai_agent.store import AgentStore

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("kind", "tickerid", "tf", "time", "bar")


class PacketError(ValueError):
    pass


def validate_packet(packet) -> dict:
    if not isinstance(packet, dict):
        raise PacketError("JSON オブジェクトではない")
    missing = [k for k in REQUIRED_KEYS if k not in packet]
    if missing:
        raise PacketError(f"必須キーがない: {missing}")
    if packet["kind"] not in ("BAR", "SETUP", "TRADE_UPDATE"):
        raise PacketError(f"不明な kind: {packet['kind']}")
    bar = packet["bar"]
    if not isinstance(bar, dict) or any(not isinstance(bar.get(k), (int, float)) for k in ("o", "h", "l", "c")):
        raise PacketError("bar の OHLC が不正")
    return packet


class Pipeline:
    def __init__(self, cfg: Optional[AgentConfig] = None, judge: Optional[TradeJudge] = None,
                 notifier: Optional[Notifier] = None, store: Optional[AgentStore] = None, use_ai: bool = True):
        self.cfg = cfg or AgentConfig()
        self.store = store or AgentStore(self.cfg.db_path)
        self.trader = PaperTrader(self.store, self.cfg.initial_equity)
        self.notifier = notifier or Notifier(self.cfg)
        self.use_ai = use_ai
        self._judge = judge

    @property
    def judge(self) -> TradeJudge:
        if self._judge is None:
            self._judge = TradeJudge(self.cfg)
        return self._judge

    def process(self, packet: dict) -> dict:
        validate_packet(packet)
        packet_id = self.store.save_packet(packet)
        if packet_id is None:
            logger.info("重複パケットをスキップ: %s %s %s", packet["tickerid"], packet["tf"], packet["kind"])
            return {"status": "duplicate"}

        result = {"status": "ok", "kind": packet["kind"], "trade_events": [], "decision": None}

        # 1) 保有ポジションの損切り / 利確判定
        for ev in self.trader.on_packet(packet):
            logger.info("ペーパートレード %s: %s @ %s (損益 %+.2f)", ev.tickerid, ev.kind, ev.price, ev.pnl)
            result["trade_events"].append(ev.__dict__)
            self.notifier.send(format_trade_event(ev))

        # 2) セットアップなら Claude に判断させる
        if packet["kind"] == "SETUP" and self.use_ai:
            account = self.trader.account_snapshot()
            recent = self.store.recent_decisions(packet["tickerid"], packet["tf"])
            d = self.judge.decide(packet, account, recent).to_dict()
            decision_id = self.store.save_decision(packet_id, packet, d)
            result["decision"] = d
            logger.info("Claude 判断 %s %s: %s (確信度 %s) %s", packet["tickerid"], packet["tf"],
                        d["decision"], d["confidence"], d.get("note", ""))

            size = None
            if d["decision"] in ("LONG", "SHORT"):
                trade = self.trader.open_trade(decision_id, packet, d)
                if trade:
                    size = trade["size"]
                    result["trade_opened"] = trade["id"]
                else:
                    d["note"] = (d.get("note") + " / " if d.get("note") else "") + "同じ銘柄・時間足で保有中のため新規建てなし"
            if d["decision"] != "WAIT" or self.cfg.notify_wait:
                self.notifier.send(format_decision(packet, d, size))
        return result
