# config/settings.py
# 環境変数または直接書き換えて使用
import os
from pathlib import Path

# .env ファイルがあれば読み込む
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Telegram API 設定 ──────────────────────────────────────
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "+818066784609")

# 監視するチャンネル（@username または https://t.me/xxx 形式でOK）
TARGET_CHANNELS = [
    "https://t.me/REXTradingSignal",
]

# ── Anthropic API ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── 価格データ取得 ────────────────────────────────────────
# ccxt で使う取引所（FXならOANDA, FXCM等; バックテスト用ならYahoo Finance）
PRICE_EXCHANGE = "binance"  # デモ用。本番はOANDA等に変更

# ── DB ────────────────────────────────────────────────────
DB_PATH = "data/signals.db"

# ── 収集設定 ──────────────────────────────────────────────
# 過去何日分を取得するか（初回起動時）
HISTORY_DAYS = 30
