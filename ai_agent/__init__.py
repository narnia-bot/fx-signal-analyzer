"""
AI Agent Lens — TradingView のローソク足パケットを受け取り、Claude に
ロング / ショート / 静観 を判断させ、ペーパートレードと通知まで行うパッケージ。

流れ:
    TradingView (tradingview/ai_agent_lens.pine)
      → Webhook (ai_agent/server.py)
      → パイプライン (ai_agent/pipeline.py)
          ├ ペーパートレードの決済判定 (ai_agent/paper_trader.py)
          ├ SETUP パケットなら Claude が判断 (ai_agent/judge.py)
          └ 通知 (ai_agent/notify.py — LINE / Telegram)
"""
