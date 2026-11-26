"""AI Agent Lens の設定。すべて環境変数（config/.env も可）から読む。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class AgentConfig:
    # Claude
    model: str = field(default_factory=lambda: os.getenv("AI_AGENT_MODEL", "claude-fable-5-2"))
    effort: str = field(default_factory=lambda: os.getenv("AI_AGENT_EFFORT", "high"))

    # Webhook
    webhook_secret: str = field(default_factory=lambda: os.getenv("WEBHOOK_SECRET", ""))
    host: str = field(default_factory=lambda: os.getenv("WEBHOOK_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8080))
    path: str = field(default_factory=lambda: os.getenv("WEBHOOK_PATH", "/webhook"))

    # ペーパートレード
    db_path: str = field(default_factory=lambda: os.getenv("AI_AGENT_DB", "data/ai_agent.db"))
    initial_equity: float = field(default_factory=lambda: _float("PAPER_INITIAL_EQUITY", 10000.0))
    max_risk_pct: float = field(default_factory=lambda: _float("MAX_RISK_PCT", 1.0))
    min_confidence: int = field(default_factory=lambda: _int("MIN_CONFIDENCE", 60))

    # 通知
    line_token: str = field(default_factory=lambda: os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    line_user_id: str = field(default_factory=lambda: os.getenv("LINE_USER_ID", ""))
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_user_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_USER_ID", ""))
    notify_wait: bool = field(default_factory=lambda: os.getenv("NOTIFY_WAIT", "0") == "1")
