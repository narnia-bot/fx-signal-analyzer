# analyzers/chart_analyzer.py
"""
yfinanceでXAUUSD(GC=F)のOHLCVを取得し、シグナル時点の複数時間足データを返す。
"""
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Optional

import yfinance as yf

SYMBOL = "GC=F"

TIMEFRAMES = {
    "M15": ("15m", 96),   # 15分足 × 96本 = 24時間
    "H1":  ("1h",  72),   # 1時間足 × 72本 = 3日
    "H4":  ("1h",  60),   # 1時間足を4本まとめてH4相当（yfinanceに4h足なし）
    "D1":  ("1d",  30),   # 日足 × 30本
}


def fetch_ohlcv(tf_key: str, signal_time: datetime) -> Optional[pd.DataFrame]:
    """シグナル発生時点の直前N本のOHLCVを取得する"""
    interval, bars = TIMEFRAMES[tf_key]

    # 取得範囲: シグナル時点から十分前まで
    end = signal_time + timedelta(hours=1)
    if tf_key == "M15":
        start = signal_time - timedelta(hours=bars // 4 + 4)
    elif tf_key in ("H1", "H4"):
        start = signal_time - timedelta(hours=bars + 24)
    else:
        start = signal_time - timedelta(days=bars + 5)

    try:
        df = yf.download(SYMBOL, start=start, end=end,
                         interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            return None

        # MultiIndex列をフラット化
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # タイムゾーンをUTCに統一
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        # シグナル発生時点以前のデータのみ
        df = df[df.index <= signal_time]

        if tf_key == "H4":
            # 1h足を4本リサンプリング
            df = df.resample("4h").agg({
                "Open": "first", "High": "max",
                "Low": "min", "Close": "last", "Volume": "sum"
            }).dropna()

        return df.tail(bars)
    except Exception:
        return None


def ohlcv_to_text(df: pd.DataFrame, tf_key: str, n: int = 20) -> str:
    """OHLCVをClaudeに渡すテキスト形式に変換（直近n本）"""
    rows = df.tail(n)
    lines = [f"{tf_key} OHLCV (直近{len(rows)}本, 新しい順):"]
    for ts, row in reversed(list(rows.iterrows())):
        lines.append(
            f"  {ts.strftime('%m/%d %H:%M')} "
            f"O={row['Open']:.1f} H={row['High']:.1f} "
            f"L={row['Low']:.1f} C={row['Close']:.1f}"
        )
    return "\n".join(lines)
