# main.py
"""
FXシグナルアナライザー メインエントリーポイント
"""
import asyncio
import argparse
import logging
import config.settings  # noqa: F401 — .env を読み込む副作用のため

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def cmd_collect(args):
    """シグナルを収集する"""
    from collectors.telegram_collector import run_collector
    asyncio.run(run_collector())

def cmd_stats(args):
    """統計レポートを表示する"""
    from storage.database import SignalDatabase
    from analyzers.stats_analyzer import StatsAnalyzer
    db = SignalDatabase()
    analyzer = StatsAnalyzer(db)
    analyzer.print_report()

def cmd_analyze(args):
    """パターン分析レポートを表示する"""
    from analyzers.pattern_analyzer import PatternAnalyzer
    analyzer = PatternAnalyzer()
    analyzer.print_report()

def cmd_chart_analyze(args):
    """過去シグナルのエントリー根拠をチャートデータと照合してClaudeで分析する"""
    import os
    from analyzers.entry_reason_analyzer import EntryReasonAnalyzer
    analyzer = EntryReasonAnalyzer(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    analyzer.run(force=getattr(args, "force", False))

def cmd_webhook(args):
    """TradingView (AI Agent Lens) の Webhook を受信して Claude に判断させる"""
    from ai_agent.server import serve
    serve()


def cmd_ai_replay(args):
    """保存した JSON パケットをパイプラインに流す（TradingView なしで動作確認）"""
    import json
    from ai_agent.pipeline import Pipeline
    with open(args.file, encoding="utf-8") as f:
        packet = json.load(f)
    packet.pop("secret", None)
    result = Pipeline(use_ai=not args.no_ai).process(packet)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_paper(args):
    """ペーパートレードの成績を表示する"""
    from ai_agent.config import AgentConfig
    from ai_agent.store import AgentStore
    from ai_agent.paper_trader import PaperTrader
    cfg = AgentConfig()
    trader = PaperTrader(AgentStore(cfg.db_path), cfg.initial_equity)
    s = trader.stats()
    print("\n=== AI Agent Lens ペーパートレード ===")
    print(f"口座残高     : {s['equity']:,.2f}  ({s['return_pct']:+.2f}%)")
    print(f"決済済み     : {s['closed_trades']} 件 / 保有中 {s['open_trades']} 件")
    print(f"勝率         : {s['win_rate'] if s['win_rate'] is not None else '-'} %")
    print(f"PF           : {s['profit_factor'] if s['profit_factor'] is not None else '-'}")
    print(f"平均 R       : {s['avg_r'] if s['avg_r'] is not None else '-'}")
    for t in trader.open_trades():
        print(f"  保有中 {t['tickerid']} {t['tf']} {t['side']} @ {t['entry']} SL {t['stop']} "
              f"T1 {t['target1']}{' ✓' if t['target1_hit'] else ''} T2 {t['target2']}")


def cmd_test_parse(args):
    """パーサーをテストする（Telegram不要）"""
    from datetime import datetime, timezone
    from parsers.signal_parser import SignalParser

    test_messages = [
        """🔥 EURUSD BUY
Entry: 1.0845
SL: 1.0810
TP1: 1.0880
TP2: 1.0920
Good luck! 🎯""",
        """SELL USDJPY
@ 149.50
Stop 150.20
Target 148.50 / 147.80""",
        """ドル円　売り
エントリー: 149.80
損切り: 150.30
利確: 148.50""",
    ]

    parser = SignalParser(use_claude=True)
    for i, msg in enumerate(test_messages, 1):
        print(f"\n--- テスト {i} ---")
        print(f"元メッセージ:\n{msg}\n")
        signal = parser.parse(
            message_id=i,
            channel="test",
            text=msg,
            timestamp=datetime.now(timezone.utc)
        )
        print(f"パース結果:")
        print(f"  ペア: {signal.pair}")
        print(f"  方向: {signal.direction}")
        print(f"  エントリー: {signal.entry_price}")
        print(f"  SL: {signal.stop_loss}")
        print(f"  TP: {signal.take_profits}")
        print(f"  RR: {signal.risk_reward}")
        print(f"  有効: {signal.is_valid}")
        if signal.parse_error:
            print(f"  エラー: {signal.parse_error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FXシグナルアナライザー")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("collect",       help="Telegramからシグナルを収集")
    sub.add_parser("stats",         help="統計レポートを表示")
    sub.add_parser("analyze",       help="パターン分析レポートを表示")
    p_chart = sub.add_parser("chart-analyze", help="チャートデータを使ったエントリー根拠分析")
    p_chart.add_argument("--force", action="store_true", help="分析済み件数も再実行する")
    sub.add_parser("test",          help="パーサーをテスト（Telegram不要）")
    sub.add_parser("webhook",       help="TradingView Webhook 受信 → Claude 判断 → ペーパートレード")
    p_replay = sub.add_parser("ai-replay", help="JSON パケットを手動でパイプラインに流す")
    p_replay.add_argument("file", help="例: examples/sample_setup_packet.json")
    p_replay.add_argument("--no-ai", action="store_true", help="Claude を呼ばずに保存・決済判定だけ行う")
    sub.add_parser("paper",         help="ペーパートレード成績を表示")

    args = parser.parse_args()

    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "chart-analyze":
        cmd_chart_analyze(args)
    elif args.command == "test":
        cmd_test_parse(args)
    elif args.command == "webhook":
        cmd_webhook(args)
    elif args.command == "ai-replay":
        cmd_ai_replay(args)
    elif args.command == "paper":
        cmd_paper(args)
    else:
        parser.print_help()
