# collectors/telegram_collector.py
"""
Telethon を使って公開Telegramチャンネルのメッセージを収集する。
初回は過去履歴を全取得、以降はポーリングで新着を監視。
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from telethon import TelegramClient, events
from telethon.tl.types import Message

from config.settings import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH,
    TELEGRAM_PHONE, TARGET_CHANNELS, HISTORY_DAYS
)
from parsers.signal_parser import SignalParser, FXSignal
from storage.database import SignalDatabase

logger = logging.getLogger(__name__)


class TelegramCollector:
    """公開チャンネルからFXシグナルを収集するコレクター"""

    def __init__(self, db: SignalDatabase, parser: SignalParser):
        self.db = db
        self.parser = parser
        self.client = TelegramClient(
            "session/collector",  # セッションファイルの保存先
            TELEGRAM_API_ID,
            TELEGRAM_API_HASH,
        )
        self._on_new_signal: list[Callable] = []

    def on_new_signal(self, callback: Callable[[FXSignal], None]):
        """新シグナル検知時のコールバックを登録"""
        self._on_new_signal.append(callback)

    async def start(self):
        """起動：ログイン → 履歴取得（初回のみ） → リアルタイム監視"""
        await self.client.start(phone=TELEGRAM_PHONE)
        logger.info("Telegram に接続しました")

        # 過去履歴を取得（初回のみ）
        for channel in TARGET_CHANNELS:
            count = self.db.conn.execute(
                "SELECT COUNT(*) FROM signals WHERE channel=?", (channel,)
            ).fetchone()[0]
            if count == 0:
                logger.info(f"過去履歴を収集中: {channel}")
                await self.collect_history(channel)
            else:
                logger.info(f"履歴取得済み: {channel} ({count}件)")

        # リアルタイム監視を登録
        @self.client.on(events.NewMessage(chats=TARGET_CHANNELS))
        async def handle_new_message(event):
            await self._process_message(event.message, event.chat.username or str(event.chat_id))

        logger.info("リアルタイム監視を開始しました")
        await self.client.run_until_disconnected()

    async def collect_history(self, channel: str) -> int:
        """過去メッセージを一括収集。収集件数を返す。"""
        since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
        count = 0

        async for message in self.client.iter_messages(
            channel,
            offset_date=None,
            reverse=False,
        ):
            if not isinstance(message, Message):
                continue
            if message.date < since:
                break
            if not message.text:
                continue

            # シグナルっぽいメッセージのみ処理（高速フィルター）
            if not self._looks_like_signal(message.text):
                continue

            signal = self.parser.parse(
                message_id=message.id,
                channel=channel,
                text=message.text,
                timestamp=message.date,
            )
            self.db.upsert_signal(signal)
            count += 1

            # レートリミット対策
            if count % 50 == 0:
                logger.info(f"  {count}件処理済み...")
                await asyncio.sleep(0.5)

        logger.info(f"{channel}: {count}件のシグナルを収集しました")
        return count

    async def _process_message(self, message: Message, channel: str):
        """新着メッセージを処理"""
        if not message.text or not self._looks_like_signal(message.text):
            return

        signal = self.parser.parse(
            message_id=message.id,
            channel=channel,
            text=message.text,
            timestamp=message.date,
        )
        self.db.upsert_signal(signal)

        if signal.is_valid:
            logger.info(f"新シグナル: {signal.pair} {signal.direction} @ {signal.entry_price}")
            for cb in self._on_new_signal:
                await asyncio.create_task(asyncio.coroutine(cb)(signal)) \
                    if asyncio.iscoroutinefunction(cb) else cb(signal)

    @staticmethod
    def _looks_like_signal(text: str) -> bool:
        """シグナルらしいメッセージかを高速判定"""
        text_upper = text.upper()
        has_direction = any(k in text_upper for k in [
            "BUY", "SELL", "LONG", "SHORT", "買", "売"
        ])
        has_price_keyword = any(k in text_upper for k in [
            "SL", "TP", "STOP", "TARGET", "ENTRY", "PRICE"
        ])
        return has_direction or has_price_keyword


async def run_collector():
    """コレクターのエントリーポイント"""
    import os
    import json
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    db = SignalDatabase()
    parser = SignalParser(use_claude=True)
    collector = TelegramCollector(db=db, parser=parser)

    # 注: 根拠分析は不要（既に過去170件分析済み）

    # Telegram通知機を初期化
    from notifiers.telegram_notifier import (
        TelegramNotifier, format_strong_signal, format_watch_signal, format_zone_signal
    )
    notifier = TelegramNotifier()

    # 価格監視エンジンを初期化
    from monitors.price_monitor import PriceMonitor
    price_monitor = PriceMonitor(db_path="data/signals.db", notifier=notifier)
    # バックグラウンドで価格監視を開始
    asyncio.create_task(price_monitor.run())

    # 新シグナル受信時: ゾーン登録のみ（分析不要）
    def on_signal(signal: FXSignal):
        print(f"\n{'='*50}")
        print(f"📍 新シグナル受信")
        print(f"  {signal.pair} {signal.direction}")

        if signal.entry_zone_low and signal.entry_zone_high:
            print(f"  ゾーン: {signal.entry_zone_low:.1f} - {signal.entry_zone_high:.1f}")

            # ゾーン通知
            msg = (
                f"📍 <b>ゾーン認識</b>\n"
                f"<b>{signal.direction}</b> ZONE\n"
                f"<b>{signal.entry_zone_low:.1f} - {signal.entry_zone_high:.1f}</b>\n"
                f"\n<i>価格がゾーン内に到達したら HIT通知します</i>"
            )
            notifier.send_sync(msg)
            print(f"  → Telegram通知送信")

            # 価格監視対象に追加
            price_monitor.add_zone(
                signal_id=signal.message_id,
                direction=signal.direction,
                zone_low=signal.entry_zone_low,
                zone_high=signal.entry_zone_high,
                entry_price=signal.entry_price,
            )
            print(f"  → 価格監視を開始")
        else:
            print(f"  ゾーン情報なし — スキップ")

        print(f"{'='*50}")

    collector.on_new_signal(on_signal)
    await collector.start()


if __name__ == "__main__":
    asyncio.run(run_collector())
