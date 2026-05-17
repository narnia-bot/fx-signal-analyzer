# analyzers/stats_analyzer.py
"""
収集したシグナルを統計的に分析する。
勝率、期待値、時間帯別パフォーマンスなどを算出。
"""
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from storage.database import SignalDatabase


@dataclass
class PerformanceStats:
    """パフォーマンス統計"""
    total: int = 0
    wins: int = 0
    losses: int = 0
    pending: int = 0
    win_rate: Optional[float] = None
    avg_pips: Optional[float] = None
    avg_rr: Optional[float] = None
    expected_value: Optional[float] = None  # 期待値（pips）

    def __str__(self):
        return (
            f"総シグナル: {self.total}\n"
            f"  勝率: {self.win_rate:.1%}\n"
            f"  平均pips: {self.avg_pips:+.1f}\n"
            f"  期待値: {self.expected_value:+.2f} pips/シグナル\n"
            f"  未決済: {self.pending}件"
        ) if self.win_rate is not None else f"データ不足（{self.total}件）"


class StatsAnalyzer:
    """シグナル統計分析"""

    def __init__(self, db: SignalDatabase):
        self.db = db

    def overall_stats(self) -> PerformanceStats:
        """全体統計"""
        raw = self.db.get_stats()
        wins = raw.get("wins") or 0
        losses = raw.get("losses") or 0
        total_closed = wins + losses
        stats = PerformanceStats(
            total=raw.get("total") or 0,
            wins=wins,
            losses=losses,
            win_rate=raw.get("win_rate"),
            avg_pips=raw.get("avg_pips"),
        )
        # 期待値 = 勝率×平均利益 + 負け率×平均損失
        if stats.win_rate is not None and stats.avg_pips is not None:
            stats.expected_value = stats.avg_pips  # 既に加重平均なので近似値
        return stats

    def by_pair(self) -> List[dict]:
        """通貨ペア別パフォーマンス"""
        return self.db.get_stats_by_pair()

    def by_hour(self) -> List[dict]:
        """時間帯別（UTC）パフォーマンス"""
        rows = self.db.conn.execute("""
        SELECT
            CAST(strftime('%H', timestamp) AS INTEGER) as hour_utc,
            COUNT(*) as total,
            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
            AVG(pips_result) as avg_pips
        FROM signals
        WHERE outcome IN ('WIN','LOSS')
        GROUP BY hour_utc
        ORDER BY hour_utc
        """).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["hour_jst"] = (d["hour_utc"] + 9) % 24
            closed = d["wins"] + (d["total"] - d["wins"])
            d["win_rate"] = round(d["wins"] / d["total"], 3) if d["total"] else None
            result.append(d)
        return result

    def by_direction(self) -> dict:
        """買い/売り別パフォーマンス"""
        rows = self.db.conn.execute("""
        SELECT
            direction,
            COUNT(*) as total,
            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
            AVG(pips_result) as avg_pips
        FROM signals
        WHERE outcome IN ('WIN','LOSS') AND direction IS NOT NULL
        GROUP BY direction
        """).fetchall()
        return {row["direction"]: dict(row) for row in rows}

    def print_report(self):
        """コンソールにサマリーレポートを出力"""
        print("\n" + "="*50)
        print("📊 シグナル分析レポート")
        print("="*50)

        overall = self.overall_stats()
        print(f"\n【全体】\n{overall}")

        print("\n【通貨ペア別】")
        for p in self.by_pair():
            wr = f"{p['win_rate']:.1%}" if p['win_rate'] else "未集計"
            pips = f"{p['avg_pips']:+.1f}" if p['avg_pips'] else "-"
            print(f"  {p['pair']:8s} | 勝率:{wr:6s} | {p['total']:3d}件 | avg {pips} pips")

        print("\n【時間帯別（JST）勝率 TOP5】")
        hours = sorted(
            [h for h in self.by_hour() if h["total"] >= 3],
            key=lambda x: x.get("win_rate") or 0, reverse=True
        )[:5]
        for h in hours:
            print(f"  {h['hour_jst']:02d}時 | 勝率:{h['win_rate']:.1%} | {h['total']}件")

        print("\n【方向別】")
        for direction, stats in self.by_direction().items():
            wr = stats["wins"] / stats["total"] if stats["total"] else 0
            print(f"  {direction:4s} | 勝率:{wr:.1%} | {stats['total']}件")

        print("="*50)
