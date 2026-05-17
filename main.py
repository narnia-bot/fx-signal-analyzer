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
    else:
        parser.print_help()
