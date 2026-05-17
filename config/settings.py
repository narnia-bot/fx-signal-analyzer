# config/settings.py
# 環境変数または直接書き換えて使用
import os
from pathlib import Path
from dotenv import load_dotenv

# デバッグ: .env の読み込みを確認
_env_path = Path(__file__).parent / ".env"
print(f"DEBUG: Looking for .env at {_env_path}")
print(f"DEBUG: .env exists: {_env_path.exists()}")
load_dotenv(_env_path)
print(f"DEBUG: TELEGRAM_API_ID = {os.getenv('TELEGRAM_API_ID')}")
print(f"DEBUG: TELEGRAM_API_HASH = {os.getenv('TELEGRAM_API_HASH')}")

# ── Telegram API 設定 ──────────────────────────────────────
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "+818066784609")

# 監視するチャンネル（@username または https://t.me/xxx 形式でOK）
TARGET_CHANNELS = [
    "https://t.me/REXTradingSignal",
]

# ── Anthropic API ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── 価格データ取得 ────────────────────────────────────────
PRICE_EXCHANGE = "binance"

# ── DB ────────────────────────────────────────────────────
DB_PATH = "data/signals.db"

# ── 収集設定 ──────────────────────────────────────────────
HISTORY_DAYS = 30
