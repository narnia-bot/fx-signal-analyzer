# notifiers/telegram_notifier.py
"""
新着シグナルをTelegramで自分に通知する。
"""
import os
import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
USER_ID = os.getenv("TELEGRAM_USER_ID", "")


class TelegramNotifier:
    def __init__(self):
        self.bot_token = BOT_TOKEN
        self.user_id = USER_ID
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

        if not self.bot_token or not self.user_id:
            logger.warning("Telegram Bot Token または User ID が未設定")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"Telegram通知: 有効（User ID: {self.user_id}）")

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """メッセージを送信。成功時True"""
        if not self.enabled:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": self.user_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        logger.warning(f"Telegram送信失敗: {resp.status}")
                        return False
        except Exception as e:
            logger.warning(f"Telegram送信エラー: {e}")
            return False

    def send_sync(self, text: str, parse_mode: str = "HTML") -> bool:
        """同期版（asyncio外での使用用）"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 既にループが動いている場合は非同期タスクとして追加
                asyncio.create_task(self.send(text, parse_mode))
                return True
            else:
                return loop.run_until_complete(self.send(text, parse_mode))
        except RuntimeError:
            # ループが存在しない場合は新規作成
            return asyncio.run(self.send(text, parse_mode))


def format_strong_signal(signal_dict: dict, result: dict, score: int) -> str:
    """STRONG信号用フォーマット"""
    direction = signal_dict["direction"]
    entry = signal_dict["entry_price"]
    sl = signal_dict["stop_loss"]
    tps = signal_dict["take_profits"]
    primary = result.get("primary_reason", "")
    reasons = " / ".join(result.get("reasons", []))
    note = result.get("note", "")[:100]

    return (
        f"<b>🔥 STRONG SIGNAL</b>\n"
        f"<b>{direction}</b> @ {entry}\n"
        f"SL={sl} | RR={signal_dict['rr']}\n"
        f"\n<b>主因:</b> {primary}\n"
        f"<b>根拠:</b> {reasons}\n"
        f"<b>分析:</b> {note}\n"
        f"\n<i>Score: {score}</i>"
    )


def format_watch_signal(signal_dict: dict, result: dict, score: int) -> str:
    """WATCH信号用フォーマット"""
    direction = signal_dict["direction"]
    entry = signal_dict["entry_price"]
    primary = result.get("primary_reason", "")

    return (
        f"<b>👀 WATCH</b>\n"
        f"{direction} @ {entry}\n"
        f"主因: {primary}\n"
        f"(Score: {score})"
    )


def format_zone_signal(direction: str, zone_low: float, zone_high: float,
                       primary_reason: str, score: int, strength: str) -> str:
    """ゾーン認識通知用フォーマット"""
    emoji = "📍" if strength != "SKIP" else "ℹ️"
    strength_label = {"STRONG": "🔥 STRONG", "WATCH": "👀 WATCH", "SKIP": "INFO"}[strength]

    return (
        f"{emoji} <b>ゾーン認識</b>\n"
        f"<b>{direction}</b> ZONE\n"
        f"<b>{zone_low:.1f} - {zone_high:.1f}</b>\n"
        f"\n主因: {primary_reason}\n"
        f"{strength_label} (Score: {score})\n"
        f"\n<i>価格がゾーン内に到達したら HIT通知します</i>"
    )
