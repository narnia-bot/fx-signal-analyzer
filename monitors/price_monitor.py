# monitors/price_monitor.py
"""
リアルタイム相場価格を監視し、シグナルのエントリーゾーンに価格が侵入したら通知する。
"""
import logging
import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 10  # 10秒ごとにチェック


async def get_xauusd_price() -> Optional[float]:
    """metals.live API から XAUUSD 現在価格を取得"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.metals.live/v1/spot/gold",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # {"gold": 3234.56} 形式
                    price = data.get("gold") or data.get("price")
                    if price:
                        return float(price)
    except Exception as e:
        logger.debug(f"metals.live 取得失敗: {e}")
    return None


class PriceMonitor:
    """エントリーゾーン監視エンジン"""

    def __init__(self, db_path: str = "data/signals.db", notifier=None):
        self.db_path = db_path
        self.notifier = notifier
        self.monitored_zones = {}  # {signal_id: {"direction": "SELL", "low": 4566, "high": 4569, "hit": False}}
        self._load_untraded_zones()

    def _load_untraded_zones(self):
        """DBから未トレード・未HIT のゾーンを読み込む"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT id, direction, entry_zone_low, entry_zone_high, entry_price
            FROM signals
            WHERE pair='XAUUSD'
                AND entry_zone_low IS NOT NULL
                AND entry_zone_high IS NOT NULL
                AND outcome IS NULL  -- 未決済
                AND direction IN ('BUY', 'SELL')
            ORDER BY timestamp DESC
            LIMIT 20  -- 直近20件
        """).fetchall()
        conn.close()

        for sig_id, direction, zone_low, zone_high, entry_price in rows:
            self.monitored_zones[sig_id] = {
                "direction": direction,
                "low": zone_low,
                "high": zone_high,
                "entry_price": entry_price,
                "hit": False,
                "notified_hit": False,
            }

        logger.info(f"監視ゾーン {len(self.monitored_zones)}件 をロード")

    async def run(self):
        """常時監視ループ"""
        logger.info("価格監視を開始します")
        while True:
            try:
                await self._check_price()
            except Exception as e:
                logger.warning(f"価格チェックエラー: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _check_price(self):
        """現在価格を取得してゾーン監視"""
        try:
            current_price = await get_xauusd_price()
            if current_price is None:
                return

            timestamp = datetime.now(timezone.utc)

            for sig_id, zone_info in self.monitored_zones.items():
                if zone_info["notified_hit"]:
                    continue

                zone_low = zone_info["low"]
                zone_high = zone_info["high"]
                direction = zone_info["direction"]

                # ゾーン内判定
                in_zone = zone_low <= current_price <= zone_high

                if in_zone and not zone_info["notified_hit"]:
                    logger.info(f"[HIT] Signal ID {sig_id}: {direction} ゾーン到達 @ {current_price:.1f}")

                    # HIT通知
                    if self.notifier:
                        msg = self._format_hit_message(
                            sig_id, direction, zone_low, zone_high, current_price, timestamp
                        )
                        await self.notifier.send(msg)

                    zone_info["notified_hit"] = True

        except Exception as e:
            logger.debug(f"価格取得失敗: {e}")

    def _format_hit_message(
        self, sig_id: int, direction: str, zone_low: float, zone_high: float,
        current_price: float, timestamp
    ) -> str:
        """HIT通知用メッセージフォーマット"""
        emoji = "🚀" if direction == "BUY" else "💥"
        return (
            f"{emoji} <b>ZONE HIT!</b>\n"
            f"<b>{direction}</b> ゾーン: {zone_low:.1f}-{zone_high:.1f}\n"
            f"<b>現在価格: {current_price:.1f}</b>\n"
            f"時刻: {timestamp.strftime('%H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)}\n"
            f"\n<i>エントリーしてください</i>"
        )

    def add_zone(self, signal_id: int, direction: str, zone_low: float, zone_high: float, entry_price: Optional[float] = None):
        """新しいゾーンを監視対象に追加"""
        self.monitored_zones[signal_id] = {
            "direction": direction,
            "low": zone_low,
            "high": zone_high,
            "entry_price": entry_price,
            "hit": False,
            "notified_hit": False,
        }
        logger.info(f"監視ゾーン追加: {direction} {zone_low}-{zone_high}")
