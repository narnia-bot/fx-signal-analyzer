"""スマホ通知（LINE Messaging API / Telegram Bot）。設定されているものすべてに送る。"""
import logging
from typing import Optional

import requests

from ai_agent.config import AgentConfig

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: Optional[AgentConfig] = None):
        self.cfg = cfg or AgentConfig()

    @property
    def enabled(self) -> bool:
        c = self.cfg
        return bool((c.line_token and c.line_user_id) or (c.telegram_token and c.telegram_user_id))

    def send(self, text: str) -> None:
        c = self.cfg
        if c.line_token and c.line_user_id:
            self._post(
                "LINE",
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {c.line_token}"},
                json={"to": c.line_user_id, "messages": [{"type": "text", "text": text[:5000]}]},
            )
        if c.telegram_token and c.telegram_user_id:
            self._post(
                "Telegram",
                f"https://api.telegram.org/bot{c.telegram_token}/sendMessage",
                json={"chat_id": c.telegram_user_id, "text": text[:4000]},
            )

    @staticmethod
    def _post(name: str, url: str, **kwargs) -> None:
        try:
            r = requests.post(url, timeout=10, **kwargs)
            if r.status_code >= 300:
                logger.warning("%s 通知失敗: %s %s", name, r.status_code, r.text[:200])
        except requests.RequestException as e:
            logger.warning("%s 通知エラー: %s", name, e)


def _fmt(x) -> str:
    if x is None:
        return "-"
    return f"{x:,.5f}".rstrip("0").rstrip(".")


def format_decision(packet: dict, d: dict, size: Optional[float] = None) -> str:
    head = {"LONG": "🟢 ロング", "SHORT": "🔴 ショート", "WAIT": "⏸ 静観"}[d["decision"]]
    lines = [f"🤖 AI判断: {head}", f"{packet['tickerid']} · {packet['tf']}",
             f"確信度 {d['confidence']} / 失敗確率 {d['failure_probability']}%"]
    if d["decision"] != "WAIT":
        lines += [
            f"エントリー {_fmt(d['entry'])} / 損切り {_fmt(d['stop'])}",
            f"利確① {_fmt(d['target1'])} / 利確② {_fmt(d['target2'])}",
            f"リスク {d['risk_pct']}%" + (f" → 数量 {size:.6g}" if size else ""),
        ]
    if d.get("key_factors"):
        lines.append("根拠: " + " / ".join(d["key_factors"]))
    if d.get("reasoning"):
        lines.append("判断: " + d["reasoning"])
    if d.get("failure_scenario"):
        lines.append("失敗シナリオ: " + d["failure_scenario"])
    if d.get("note"):
        lines.append("※ " + d["note"])
    lines.append("[ペーパートレード]")
    return "\n".join(lines)


def format_trade_event(ev) -> str:
    label = {"target1_hit": "✅ 利確① 到達（残りは建値ストップ）", "target2_hit": "🏁 利確② 到達・決済",
             "stop_hit": "❌ 損切り", "breakeven_stop": "➖ 建値で決済"}[ev.kind]
    return (f"{label}\n{ev.tickerid} · {ev.tf} · {ev.side}\n"
            f"価格 {_fmt(ev.price)} / 損益 {ev.pnl:+,.2f}\n口座残高 {ev.equity:,.2f} [ペーパー]")
