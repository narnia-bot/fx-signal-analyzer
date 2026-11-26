# AI Agent Lens — TradingView × Claude 自動判断システム

```
TradingView (ai_agent_lens.pine)
  │ 確定足ごとにローソク足を JSON 化
  ▼ Webhook
python main.py webhook  (ai_agent/server.py)
  ├─ BAR / TRADE_UPDATE … ペーパートレードの損切り・利確判定
  └─ SETUP（スコア ≥ 閾値）… Claude が LONG / SHORT / WAIT を判断
        ▼
  ペーパートレード（data/ai_agent.db） → LINE / Telegram 通知
```

**実資金の発注機能はありません。** すべてペーパートレード（仮想口座）です。

---

## 1. インジケーターを TradingView に追加

1. TradingView でチャートを開き、下部の **Pine エディタ** を開く
2. `tradingview/ai_agent_lens.pine` の中身を全部貼り付けて **「チャートに追加」**
3. インジケーターの設定 → **Webhook シークレット** に推測されにくい文字列を入れる（後で `WEBHOOK_SECRET` に同じ値を設定）

チャート上には次が表示されます：

- EMA 20 / 50 / 200、前日高値・安値
- 右上のシグナルカード（Long/Short スコア、合致条件数、地合い、エントリー・損切り・利確、RSI/ADX、ローソク足パターン、構造）
- セットアップ発生時のラベル、RISK / REWARD ボックス、T1 / T2 ライン

### スコアの内訳（ロング / ショートそれぞれ 0〜100）

| 条件 | 点 |
|---|---|
| 地合い（EMA20/50 と終値の位置） | 20 |
| 上位足の方向（初期値: 日足 EMA50） | 20 |
| 相場構造（直近 BOS / 高値安値の切り上げ・切り下げ） | 15 |
| モメンタム（RSI 50-70 / 30-50 と MACD ヒストグラム） | 15 |
| ローソク足パターン（包み足・ピンバー・大陽線/大陰線） | 15 |
| 出来高（20本平均の 1.2 倍以上。FX など出来高なしの銘柄は 0 点） | 10 |
| トレンドの強さ（ADX ≥ 20） | 5 |

スコアが閾値（初期値 70）以上になった **確定足** でだけセットアップを出します。
損切りは ATR × 1.5、利確① 1.5R、利確② 3R（すべて設定で変更可）。

## 2. 受信サーバーを動かす

```bash
pip install -r requirements.txt

# config/.env か環境変数に設定
export ANTHROPIC_API_KEY="sk-ant-..."
export WEBHOOK_SECRET="インジケーターと同じ値"
python main.py webhook          # http://0.0.0.0:8080/webhook で待ち受け
```

TradingView の Webhook は **インターネットから届く HTTPS の 443 番（または 80 番）ポート** にしか送れません。

- ローカル PC で試す: `cloudflared tunnel --url http://localhost:8080` や `ngrok http 8080` で公開 URL を作る
- 常時稼働: Railway / Render / Heroku などに置く（`Procfile` の `web` が `python main.py webhook` を起動。`PORT` は自動で読みます）

## 3. TradingView でアラートを 1 つ作る

1. アラート作成（時計アイコン）→ 条件: **AI Agent Lens** → **「alert() 関数の呼び出しのみ」**
2. 通知タブ → **Webhook URL** に `https://<公開URL>/webhook`
3. メッセージ欄は空のままで OK（インジケーターが JSON を本文として送ります）

> Webhook 通知は TradingView の有料プランの機能です（無料プランはチャート表示まで）。
> アラートは銘柄・時間足ごとに 1 つ必要です。

## 4. 動作確認（TradingView なしで）

```bash
python -m unittest discover tests                                 # Claude を呼ばないテスト
python main.py ai-replay examples/sample_setup_packet.json --no-ai # 保存・決済判定のみ
python main.py ai-replay examples/sample_setup_packet.json         # Claude に実際に判断させる（API 料金がかかります）
python main.py paper                                              # ペーパートレード成績
```

## 5. Claude の判断

`SETUP` パケットが届いたときだけ Claude を呼びます（毎足呼ばないのでコストを抑えられます）。
プロンプトは `ai_agent/prompts/trader_system.md`。Claude には次を渡します：

- インジケーターのパケット（直近5本、地合い、上位足、モメンタム、出来高、構造、スコア、提案水準）
- ペーパー口座の状態（残高・保有ポジション）
- 同じ銘柄・時間足での直近の判断

返ってくるもの（JSON スキーマで固定）：
`decision`(LONG/SHORT/WAIT)・`confidence`・`entry`/`stop`/`target1`/`target2`・`risk_pct`・
`failure_probability`・`failure_scenario`・`key_factors`・`reasoning`

コード側の安全装置：

- 価格の並びが矛盾していたらインジケーターの水準に置き換え、それも使えなければ WAIT
- `risk_pct` は `MAX_RISK_PCT`（初期 1%）で上限
- 確信度が `MIN_CONFIDENCE`（初期 60）未満は WAIT
- 同じ銘柄・時間足で保有中なら新規建てしない
- ポジションサイズは Claude ではなくコードで計算（残高 × risk_pct ÷ 損切り幅）

## 6. 通知（LINE / Telegram）

LINE Notify は終了しているため、**LINE Messaging API**（公式アカウント）を使います。

1. [LINE Developers](https://developers.line.biz/) でプロバイダー → Messaging API チャネルを作成
2. チャネルアクセストークン（長期）を発行 → `LINE_CHANNEL_ACCESS_TOKEN`
3. 「あなたのユーザーID」（チャネル基本設定）→ `LINE_USER_ID`
4. 作った公式アカウントを自分の LINE で友だち追加

Telegram を使う場合は既存の `TELEGRAM_BOT_TOKEN` / `TELEGRAM_USER_ID` をそのまま使います（両方設定すれば両方に届きます）。

## 環境変数一覧

| 変数 | 初期値 | 説明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Claude API キー |
| `AI_AGENT_MODEL` | `claude-fable-5-2` | 判断に使うモデル。安くしたいなら `claude-opus-5-5` など |
| `AI_AGENT_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max` |
| `WEBHOOK_SECRET` | – | **必須**。インジケーターの設定と同じ値 |
| `PORT` / `WEBHOOK_HOST` / `WEBHOOK_PATH` | `8080` / `0.0.0.0` / `/webhook` | 待ち受け |
| `AI_AGENT_DB` | `data/ai_agent.db` | 保存先 |
| `PAPER_INITIAL_EQUITY` | `10000` | ペーパー口座の初期残高 |
| `MAX_RISK_PCT` | `1.0` | 1 トレードの最大リスク (%) |
| `MIN_CONFIDENCE` | `60` | これ未満の確信度は見送り |
| `NOTIFY_WAIT` | `0` | `1` なら WAIT（静観）判断も通知 |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` | – | LINE 通知 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_USER_ID` | – | Telegram 通知 |

## 注意

- バックテスト・ペーパートレードで十分に検証するまで実資金では使わないでください。
- 判定は確定足のみ、上位足・前日高安は確定済みの値だけ参照するのでリペイントしません。
  ただし過去チャート上のシグナルは「後から見た結果」なので、成績の評価はペーパートレードの記録で行ってください。
