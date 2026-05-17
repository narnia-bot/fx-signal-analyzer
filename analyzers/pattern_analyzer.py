# analyzers/pattern_analyzer.py
"""
過去シグナルからパターンを定義し、新着シグナルとのマッチを判定する。

パターンの条件:
  - エントリーゾーン（価格帯）
  - 方向 (BUY / SELL / ANY)
  - SL幅の上限
  - RR比の下限
"""
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from parsers.signal_parser import FXSignal


@dataclass
class Pattern:
    name: str
    direction: Optional[str]       # "BUY" / "SELL" / None(どちらでも)
    price_low: Optional[float]     # エントリーゾーン下限
    price_high: Optional[float]    # エントリーゾーン上限
    sl_max: Optional[float]        # SL幅の上限（pips相当）
    rr_min: Optional[float]        # RR比の下限

    def match(self, signal: FXSignal) -> tuple[bool, list[str]]:
        """シグナルがこのパターンにマッチするか判定。(マッチ, 理由リスト) を返す"""
        reasons = []

        if self.direction and signal.direction != self.direction:
            return False, []

        if signal.entry_price is None:
            return False, []

        if self.price_low is not None and signal.entry_price < self.price_low:
            return False, []
        if self.price_high is not None and signal.entry_price > self.price_high:
            return False, []

        sl_width = None
        if signal.entry_price and signal.stop_loss:
            sl_width = abs(signal.entry_price - signal.stop_loss)
            if self.sl_max is not None and sl_width > self.sl_max:
                return False, []

        rr = None
        if sl_width and signal.take_profits:
            rr = round(abs(signal.take_profits[0] - signal.entry_price) / sl_width, 2)
            if self.rr_min is not None and rr < self.rr_min:
                return False, []

        # マッチ理由をまとめる
        if self.direction:
            reasons.append(f"方向={self.direction}")
        if self.price_low or self.price_high:
            reasons.append(f"価格帯={self.price_low}-{self.price_high}")
        if sl_width is not None:
            reasons.append(f"SL幅={sl_width:.1f}")
        if rr is not None:
            reasons.append(f"RR={rr:.2f}")

        return True, reasons


class PatternAnalyzer:
    def __init__(self, db_path: str = "data/signals.db"):
        self.db_path = db_path
        self.patterns: list[Pattern] = []

    def load_patterns_from_db(self) -> None:
        """過去データの統計からパターンを自動生成する"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT direction, entry_price, stop_loss, take_profits
            FROM signals
            WHERE pair='XAUUSD' AND entry_price IS NOT NULL AND stop_loss IS NOT NULL
        """).fetchall()
        conn.close()

        sl_widths, rr_ratios = [], []
        for _, entry, sl, tp_json in rows:
            sl_w = abs(entry - sl)
            tps = json.loads(tp_json or "[]")
            sl_widths.append(sl_w)
            if tps and sl_w:
                rr_ratios.append(abs(tps[0] - entry) / sl_w)

        if not sl_widths:
            return

        # 統計値から閾値を決定（中央値ベース）
        sl_widths.sort()
        rr_ratios.sort()
        median_sl = sl_widths[len(sl_widths) // 2]
        median_rr = rr_ratios[len(rr_ratios) // 2] if rr_ratios else 1.0

        # 価格帯を100刻みで分割してパターン生成
        conn = sqlite3.connect(self.db_path)
        zones = conn.execute("""
            SELECT direction,
                   CAST(entry_price / 100 AS INT) * 100 as zone,
                   COUNT(*) as cnt
            FROM signals
            WHERE pair='XAUUSD' AND entry_price IS NOT NULL AND direction IS NOT NULL
            GROUP BY direction, zone
            HAVING cnt >= 3
            ORDER BY cnt DESC
        """).fetchall()
        conn.close()

        self.patterns = []
        for direction, zone_base, cnt in zones:
            self.patterns.append(Pattern(
                name=f"XAUUSD {direction} @ {zone_base}-{zone_base+100} ({cnt}件)",
                direction=direction,
                price_low=float(zone_base),
                price_high=float(zone_base + 100),
                sl_max=round(median_sl * 1.5, 1),
                rr_min=round(median_rr * 0.8, 2),
            ))

    def check(self, signal: FXSignal) -> list[tuple[Pattern, list[str]]]:
        """シグナルにマッチするパターン一覧を返す"""
        matched = []
        for pattern in self.patterns:
            ok, reasons = pattern.match(signal)
            if ok:
                matched.append((pattern, reasons))
        return matched

    def print_report(self) -> None:
        """検出されたパターン一覧を表示"""
        self.load_patterns_from_db()
        if not self.patterns:
            print("パターンが検出されませんでした（データ不足）")
            return

        print(f"\n{'='*60}")
        print(f"検出パターン一覧 ({len(self.patterns)}件)")
        print(f"{'='*60}")
        for p in self.patterns:
            dir_str = p.direction or "ANY"
            zone_str = f"{p.price_low}-{p.price_high}" if p.price_low else "制限なし"
            print(f"\n  [{p.name}]")
            print(f"    方向: {dir_str} | 価格帯: {zone_str}")
            print(f"    SL上限: {p.sl_max} | RR下限: {p.rr_min}")
