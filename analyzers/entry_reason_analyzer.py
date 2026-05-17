# analyzers/entry_reason_analyzer.py
"""
各シグナルのエントリー根拠をClaudeに推論させ、DBに保存・集計する。
"""
import json
import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import anthropic

from analyzers.chart_analyzer import fetch_ohlcv, ohlcv_to_text

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """あなたはプロのFXトレーダーです。
以下のXAUUSD（ゴールド）のエントリーシグナルと、その時点の複数時間足チャートデータを見て、
このトレーダーがエントリーした根拠を分析してください。

## シグナル情報
- 方向: {direction}
- エントリー価格: {entry_price}
- エントリーゾーン: {zone}
- SL: {stop_loss}
- TP: {take_profits}
- RR比: {rr}

## チャートデータ（シグナル発生時点: {signal_time}）
{chart_data}

## 分析指示
このエントリーの根拠として考えられるものをすべて列挙してください。
以下のような観点から分析し、該当するものをJSONで返してください：

- FVG（Fair Value Gap）: 上昇/下降FVGへの戻り
- 水平S/R: 明確なサポート/レジスタンスライン付近
- レンジ上限/下限: 直近レンジの高値・安値付近
- トレンドライン: 上昇/下降トレンドラインへの接触
- 移動平均線: 主要MAへのタッチ（価格水準から推測）
- 過去高値/安値: 以前の重要高値・安値付近
- 心理的節目: キリ番（XXX0、XXX5など）付近
- 時間足の転換点: 特定時間足でのキャンドルパターン
- その他: 上記に当てはまらない根拠

以下のJSON形式のみで返してください（説明不要）:
{{
  "reasons": ["FVG", "水平S/R", "レンジ上限"],
  "primary_reason": "FVG",
  "confidence": 0.8,
  "note": "H4でFVGが発生しており、そこに日足レジスタンスが重なっている"
}}
"""


class EntryReasonAnalyzer:
    def __init__(self, db_path: str = "data/signals.db", api_key: str = None):
        self.db_path = db_path
        self.client = anthropic.Anthropic(api_key=api_key)
        self._ensure_columns()

    def _ensure_columns(self):
        conn = sqlite3.connect(self.db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
        if "entry_reasons" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN entry_reasons TEXT")
        if "primary_reason" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN primary_reason TEXT")
        if "reason_note" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN reason_note TEXT")
        if "reason_confidence" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN reason_confidence REAL")
        conn.commit()
        conn.close()

    def _fetch_signals(self, force: bool = False) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        where = "pair='XAUUSD' AND entry_price IS NOT NULL AND direction IS NOT NULL"
        if not force:
            where += " AND entry_reasons IS NULL"
        rows = conn.execute(f"""
            SELECT id, direction, entry_price, entry_zone_low, entry_zone_high,
                   stop_loss, take_profits, timestamp
            FROM signals WHERE {where}
            ORDER BY timestamp
        """).fetchall()
        conn.close()
        return [
            dict(zip(["id","direction","entry_price","entry_zone_low","entry_zone_high",
                      "stop_loss","take_profits","timestamp"], r))
            for r in rows
        ]

    def _save_result(self, signal_id: int, reasons: list, primary: str,
                     confidence: float, note: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE signals SET
                entry_reasons = ?,
                primary_reason = ?,
                reason_confidence = ?,
                reason_note = ?
            WHERE id = ?
        """, (json.dumps(reasons, ensure_ascii=False), primary,
              confidence, note, signal_id))
        conn.commit()
        conn.close()

    def analyze_signal(self, signal: dict) -> Optional[dict]:
        """1件のシグナルをChartデータ付きでClaudeに分析させる"""
        signal_time = datetime.fromisoformat(signal["timestamp"])
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)

        # 複数時間足のチャートデータを取得
        chart_parts = []
        for tf in ["D1", "H4", "H1", "M15"]:
            df = fetch_ohlcv(tf, signal_time)
            if df is not None and not df.empty:
                chart_parts.append(ohlcv_to_text(df, tf, n=15))

        if not chart_parts:
            logger.warning(f"ID={signal['id']}: チャートデータ取得失敗")
            return None

        tps = json.loads(signal["take_profits"] or "[]")
        zone_str = (f"{signal['entry_zone_low']}-{signal['entry_zone_high']}"
                    if signal["entry_zone_low"] else "なし")
        sl = signal["stop_loss"]
        rr = round(abs(tps[0] - signal["entry_price"]) /
                   abs(signal["entry_price"] - sl), 2) if tps and sl else "不明"

        prompt = PROMPT_TEMPLATE.format(
            direction=signal["direction"],
            entry_price=signal["entry_price"],
            zone=zone_str,
            stop_loss=sl,
            take_profits=tps,
            rr=rr,
            signal_time=signal_time.strftime("%Y-%m-%d %H:%M UTC"),
            chart_data="\n\n".join(chart_parts),
        )

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",  # コスト削減のためHaikuを使用
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as e:
            logger.warning(f"ID={signal['id']}: Claude解析失敗 {e}")
            return None

    def run(self, force: bool = False) -> None:
        """全シグナルを分析してDBに保存し、集計レポートを表示する"""
        signals = self._fetch_signals(force=force)
        logger.info(f"分析対象: {len(signals)}件")

        for i, signal in enumerate(signals, 1):
            logger.info(f"[{i}/{len(signals)}] ID={signal['id']} "
                        f"{signal['direction']} @ {signal['entry_price']}")
            result = self.analyze_signal(signal)
            if result:
                self._save_result(
                    signal["id"],
                    result.get("reasons", []),
                    result.get("primary_reason", ""),
                    result.get("confidence", 0.0),
                    result.get("note", ""),
                )
            # レートリミット対策
            time.sleep(0.3)

        self.print_report()

    def print_report(self) -> None:
        """分析済みデータの集計レポートを表示"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT entry_reasons, primary_reason, reason_confidence
            FROM signals
            WHERE pair='XAUUSD' AND entry_reasons IS NOT NULL
        """).fetchall()
        conn.close()

        if not rows:
            print("分析済みデータがありません。先に `python main.py chart-analyze` を実行してください。")
            return

        all_reasons = Counter()
        primary_reasons = Counter()
        for reasons_json, primary, _ in rows:
            for r in json.loads(reasons_json or "[]"):
                all_reasons[r] += 1
            if primary:
                primary_reasons[primary] += 1

        total = len(rows)
        print(f"\n{'='*60}")
        print(f"エントリー根拠分析レポート（{total}件）")
        print(f"{'='*60}")

        print(f"\n【主要根拠ランキング（primary_reason）】")
        for reason, count in primary_reasons.most_common():
            bar = "█" * int(count / total * 30)
            print(f"  {reason:<16} {count:>3}件 ({count/total*100:.0f}%) {bar}")

        print(f"\n【複合根拠ランキング（全タグ）】")
        for reason, count in all_reasons.most_common():
            bar = "█" * int(count / total * 30)
            print(f"  {reason:<16} {count:>3}件 ({count/total*100:.0f}%) {bar}")

        # サンプル表示（確信度上位3件）
        conn = sqlite3.connect(self.db_path)
        samples = conn.execute("""
            SELECT direction, entry_price, primary_reason, reason_confidence, reason_note
            FROM signals
            WHERE pair='XAUUSD' AND entry_reasons IS NOT NULL
            ORDER BY reason_confidence DESC LIMIT 3
        """).fetchall()
        conn.close()

        print(f"\n【高確信度サンプル（上位3件）】")
        for direction, entry, primary, conf, note in samples:
            print(f"\n  {direction} @ {entry} | {primary} (確信度:{conf:.0%})")
            print(f"  → {note}")
