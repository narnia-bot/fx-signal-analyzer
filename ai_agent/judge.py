"""
Claude にセットアップを判断させる（ロング / ショート / 静観）。
出力は構造化出力（JSON スキーマ）で受け取り、価格の整合性はコード側でも検証する。
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import anthropic

from ai_agent.config import AgentConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "trader_system.md").read_text(encoding="utf-8")

# サーバー側フォールバック（安全分類器で拒否されたとき別モデルで再実行）に対応するモデル
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

_NULLABLE_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["LONG", "SHORT", "WAIT"]},
        "confidence": {"type": "integer", "description": "0-100"},
        "entry": _NULLABLE_NUM,
        "stop": _NULLABLE_NUM,
        "target1": _NULLABLE_NUM,
        "target2": _NULLABLE_NUM,
        "risk_pct": {"type": "number", "description": "口座に対する許容損失率 (%)。WAIT なら 0"},
        "failure_probability": {"type": "integer", "description": "0-100"},
        "failure_scenario": {"type": "string"},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": [
        "decision", "confidence", "entry", "stop", "target1", "target2",
        "risk_pct", "failure_probability", "failure_scenario", "key_factors", "reasoning",
    ],
    "additionalProperties": False,
}


@dataclass
class Decision:
    decision: str                      # LONG / SHORT / WAIT
    confidence: int = 0
    entry: Optional[float] = None
    stop: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    risk_pct: float = 0.0
    failure_probability: int = 0
    failure_scenario: str = ""
    key_factors: list = field(default_factory=list)
    reasoning: str = ""
    model: str = ""
    note: str = ""                     # コード側での補正・エラー

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def wait(cls, note: str, model: str = "") -> "Decision":
        return cls(decision="WAIT", note=note, model=model)


def levels_are_valid(side: str, entry, stop, t1, t2) -> bool:
    if None in (entry, stop, t1, t2):
        return False
    if side == "LONG":
        return stop < entry < t1 <= t2
    if side == "SHORT":
        return stop > entry > t1 >= t2
    return False


def sanitize(d: Decision, packet: dict, cfg: AgentConfig) -> Decision:
    """モデル出力をそのまま信用せず、価格・リスクの整合性を確認する。"""
    d.confidence = max(0, min(100, int(d.confidence)))
    d.failure_probability = max(0, min(100, int(d.failure_probability)))
    if d.decision == "WAIT":
        d.entry = d.stop = d.target1 = d.target2 = None
        d.risk_pct = 0.0
        return d

    close = float(packet["bar"]["c"])
    # ペーパートレードは最新の終値で成行約定とみなす
    d.entry = close
    if not levels_are_valid(d.decision, d.entry, d.stop, d.target1, d.target2):
        setup = packet.get("setup") or {}
        same_side = setup.get("side", "").upper() == d.decision
        if same_side and levels_are_valid(d.decision, close, setup.get("stop"), setup.get("target1"), setup.get("target2")):
            d.stop, d.target1, d.target2 = setup["stop"], setup["target1"], setup["target2"]
            d.note = "Claude の価格水準が不整合だったためインジケーターの水準を使用"
        else:
            return Decision.wait(f"価格水準が不整合のため見送り（{d.decision}）", d.model)

    d.risk_pct = max(0.0, min(cfg.max_risk_pct, float(d.risk_pct)))
    if d.confidence < cfg.min_confidence:
        d.note = (d.note + " / " if d.note else "") + f"確信度 {d.confidence} < {cfg.min_confidence} のため見送り"
        d.decision = "WAIT"
        d.risk_pct = 0.0
    return d


class TradeJudge:
    def __init__(self, cfg: Optional[AgentConfig] = None, client: Optional[anthropic.Anthropic] = None):
        self.cfg = cfg or AgentConfig()
        self.client = client or anthropic.Anthropic()

    def build_user_message(self, packet: dict, account: dict, recent_decisions: list) -> str:
        public_packet = {k: v for k, v in packet.items() if k != "secret"}
        return json.dumps(
            {"packet": public_packet, "account": account, "recent_decisions": recent_decisions},
            ensure_ascii=False,
            sort_keys=True,
        )

    def decide(self, packet: dict, account: dict, recent_decisions: list) -> Decision:
        cfg = self.cfg
        kwargs = dict(
            model=cfg.model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
            messages=[{"role": "user", "content": self.build_user_message(packet, account, recent_decisions)}],
        )
        if cfg.model in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        try:
            resp = self.client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            logger.warning("Claude レート制限: %s", e)
            return Decision.wait("Claude API レート制限", cfg.model)
        except anthropic.APIStatusError as e:
            logger.error("Claude API エラー %s: %s", e.status_code, e.message)
            return Decision.wait(f"Claude API エラー {e.status_code}", cfg.model)
        except anthropic.APIConnectionError as e:
            logger.error("Claude 接続エラー: %s", e)
            return Decision.wait("Claude API 接続エラー", cfg.model)

        served_by = getattr(resp, "model", cfg.model)
        if resp.stop_reason == "refusal":
            return Decision.wait("Claude が回答を拒否", served_by)
        if resp.stop_reason == "max_tokens":
            return Decision.wait("出力が max_tokens で途切れた", served_by)

        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            raw = json.loads(text)
            d = Decision(**{k: raw[k] for k in DECISION_SCHEMA["required"]}, model=served_by)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Claude 出力の解析に失敗: %s / %s", e, text[:300])
            return Decision.wait("Claude 出力の解析に失敗", served_by)

        return sanitize(d, packet, cfg)
