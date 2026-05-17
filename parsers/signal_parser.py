# parsers/signal_parser.py
"""
FXシグナルメッセージを構造化データに変換するパーサー。
Claude API を使ってどんな形式のシグナルも解析できる。
"""
import re
import json
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

import anthropic


@dataclass
class FXSignal:
    """パース済みFXシグナル"""
    message_id: int
    channel: str
    raw_text: str
    timestamp: datetime

    # パース結果
    pair: Optional[str] = None          # 例: "EURUSD", "USDJPY"
    direction: Optional[str] = None     # "BUY" or "SELL"
    entry_price: Optional[float] = None
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profits: list = field(default_factory=list)  # [TP1, TP2, TP3]
    timeframe: Optional[str] = None     # 例: "H1", "H4", "D1"
    confidence: Optional[float] = None  # 0.0〜1.0

    # 検証結果（後で埋める）
    outcome: Optional[str] = None       # "WIN", "LOSS", "PENDING", "CANCELLED"
    pips_result: Optional[float] = None
    closed_at: Optional[datetime] = None

    parse_error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        d["closed_at"] = self.closed_at.isoformat() if self.closed_at else None
        return d

    @property
    def is_valid(self) -> bool:
        return all([
            self.pair,
            self.direction in ("BUY", "SELL"),
            self.entry_price is not None,
        ])

    @property
    def risk_reward(self) -> Optional[float]:
        """リスクリワード比を計算"""
        if not (self.entry_price and self.stop_loss and self.take_profits):
            return None
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            return None
        reward = abs(self.take_profits[0] - self.entry_price)
        return round(reward / risk, 2)


class SignalParser:
    """
    Claudeを使ってFXシグナルメッセージをパースする。
    正規表現でまず試み、失敗したらClaude APIにフォールバック。
    """

    # よくあるFXシグナルのパターン
    _CURRENCIES = (
        r'(?:XAU|XAG|XPT|XPD'
        r'|USD|EUR|GBP|JPY|CHF|CAD|AUD|NZD'
        r'|SGD|HKD|NOK|SEK|DKK|MXN|ZAR|TRY'
        r'|CNH|CNY|INR|BTC|ETH)'
    )
    PAIR_PATTERN = re.compile(
        rf'\b({_CURRENCIES}[/_]?{_CURRENCIES})\b', re.IGNORECASE
    )
    DIRECTION_PATTERN = re.compile(
        r'\b(BUY|SELL|LONG|SHORT|買い?|売り?)\b', re.IGNORECASE
    )
    PRICE_PATTERN = re.compile(r'\b(\d{1,5}\.?\d{0,5})\b')

    def __init__(self, use_claude: bool = True):
        self.use_claude = use_claude
        if use_claude:
            import os
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def parse(self, message_id: int, channel: str,
              text: str, timestamp: datetime) -> FXSignal:
        """メッセージをFXSignalに変換"""
        signal = FXSignal(
            message_id=message_id,
            channel=channel,
            raw_text=text,
            timestamp=timestamp,
        )

        # まず正規表現で高速パース
        self._parse_regex(signal)

        # バリデーション通過済みならそのまま返す
        if signal.is_valid:
            return signal

        # 不完全な場合はClaude APIで再解析
        if self.use_claude:
            self._parse_with_claude(signal)

        return signal

    def _parse_regex(self, signal: FXSignal) -> None:
        """正規表現による高速パース（大半のシグナルはこれで対応）"""
        text = signal.raw_text.upper()

        # 通貨ペア
        pair_match = self.PAIR_PATTERN.search(text)
        if pair_match:
            signal.pair = pair_match.group(1).replace("/", "").replace("_", "")

        # 方向
        dir_match = self.DIRECTION_PATTERN.search(signal.raw_text, re.IGNORECASE)
        if dir_match:
            raw_dir = dir_match.group(1).upper()
            if raw_dir in ("BUY", "LONG", "買", "買い"):
                signal.direction = "BUY"
            elif raw_dir in ("SELL", "SHORT", "売", "売り"):
                signal.direction = "SELL"

        # 価格（エントリー、SL、TP）
        lines = signal.raw_text.split('\n')
        for line in lines:
            lower = line.lower()
            # OPENなど数値以外を除外してから変換
            prices = []
            for p in self.PRICE_PATTERN.findall(line):
                try:
                    v = float(p)
                    if 0.5 < v < 99999:
                        prices.append(v)
                except ValueError:
                    continue
            if not prices:
                continue

            # "BUY ZONE:" / "SELL ZONE:" → ゾーン保存 + 中値をentry_priceに
            if re.search(r'(buy|sell)\s+zone', lower):
                signal.entry_zone_low = min(prices)
                signal.entry_zone_high = max(prices)
                signal.entry_price = round(sum(prices) / len(prices), 5)
            elif any(k in lower for k in ['entry', '@', ' at ']):
                signal.entry_price = round(sum(prices) / len(prices), 5)
            elif any(k in lower for k in ['sl', 'stop', 'stoploss', 'stop loss']):
                signal.stop_loss = prices[0]
            elif any(k in lower for k in ['tp', 'take', 'target', 'profit']):
                signal.take_profits.extend(prices)

    def _parse_with_claude(self, signal: FXSignal) -> None:
        """Claude APIによる柔軟なパース（正規表現で拾えなかった場合）"""
        prompt = f"""以下はFXトレーダーのシグナルメッセージです。
構造化JSONとして抽出してください。

メッセージ:
{signal.raw_text}

エントリーがゾーン（範囲）の場合は中値を entry_price に入れてください。
take_profits の "OPEN" や文字列は除外し、数値のみリストに入れてください。
以下のJSON形式で返してください（不明な項目はnull）:
{{
  "pair": "EURUSD",
  "direction": "BUY",
  "entry_price": 1.0850,
  "stop_loss": 1.0820,
  "take_profits": [1.0880, 1.0910],
  "timeframe": "H4",
  "confidence": 0.9
}}

JSONのみ返してください。説明不要です。"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()

            # JSONブロックがあれば取り出す
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            data = json.loads(text)

            # パース結果をシグナルに反映（既存値を上書きしない）
            if not signal.pair and data.get("pair"):
                signal.pair = data["pair"].replace("/", "")
            if not signal.direction and data.get("direction"):
                signal.direction = data["direction"].upper()
            if not signal.entry_price and data.get("entry_price"):
                v = data["entry_price"]
                signal.entry_price = float(v[0] if isinstance(v, list) else v)
            if not signal.stop_loss and data.get("stop_loss"):
                v = data["stop_loss"]
                signal.stop_loss = float(v[0] if isinstance(v, list) else v)
            if not signal.take_profits and data.get("take_profits"):
                signal.take_profits = [float(p) for p in data["take_profits"]]
            if data.get("timeframe"):
                signal.timeframe = data["timeframe"]
            if data.get("confidence"):
                signal.confidence = float(data["confidence"])

        except (json.JSONDecodeError, KeyError, ValueError, IndexError, TypeError) as e:
            signal.parse_error = f"Claude parse error: {e}"
