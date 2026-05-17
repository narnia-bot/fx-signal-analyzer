# CLAUDE.md
# FXシグナルアナライザー — Claude Code ガイド

## プロジェクト概要
Telegramの公開FXシグナルチャンネルを収集・分析し、同様のエントリーロジックを自動発信するシステム。

## セットアップ手順

```bash
# 1. 依存ライブラリのインストール
pip install telethon anthropic ccxt pandas

# 2. 環境変数を設定（.envファイルを作るか直接export）
export TELEGRAM_API_ID="あなたのAPI_ID"       # https://my.telegram.org で取得
export TELEGRAM_API_HASH="あなたのAPI_HASH"
export TELEGRAM_PHONE="+819012345678"
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. config/settings.py の TARGET_CHANNELS を編集
```

## 使い方

```bash
# パーサーのテスト（Telegram不要）
python main.py test

# シグナル収集開始（初回は過去90日分を取得）
python main.py collect

# 統計レポート表示
python main.py stats
```

## ディレクトリ構成
```
fx-signal-analyzer/
├── CLAUDE.md              ← このファイル
├── main.py                ← エントリーポイント
├── config/
│   └── settings.py        ← 設定（APIキー等）
├── collectors/
│   └── telegram_collector.py  ← Telegramメッセージ収集
├── parsers/
│   └── signal_parser.py   ← シグナルメッセージ → 構造化データ
├── storage/
│   └── database.py        ← SQLite永続化
├── analyzers/
│   └── stats_analyzer.py  ← 統計分析
├── data/
│   └── signals.db         ← データベース（自動生成）
└── session/               ← Telegramセッション（自動生成）
```

## フェーズ2への拡張

次のステップでやること：
1. `analyzers/chart_analyzer.py` — ccxtでOHLCVデータ取得
2. `analyzers/pattern_extractor.py` — Claudeでエントリー根拠を逆算
3. `analyzers/backtester.py` — パターンのバックテスト

## よくある問題

- **Telegramログイン**: 初回起動時に電話番号認証が必要。`session/`フォルダに保存される。
- **パース精度が低い**: `config/settings.py` の `use_claude=True` を確認。
- **過去履歴が取れない**: 公開チャンネルは誰でも取得可能。プライベートは招待が必要。
