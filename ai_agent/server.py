"""
TradingView の Webhook 受信サーバー（標準ライブラリのみ）。

TradingView は約 3 秒で Webhook をタイムアウトさせるため、受信したら即 200 を返し、
Claude の判断などの重い処理はバックグラウンドのワーカースレッドで順番に行う。
"""
import hmac
import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from ai_agent.config import AgentConfig

logger = logging.getLogger(__name__)

MAX_BODY = 64 * 1024


def make_handler(cfg: AgentConfig, enqueue: Callable[[dict], None]):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: str):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/health"):
                self._reply(200, "ok")
            else:
                self._reply(404, "not found")

        def do_POST(self):
            if self.path.split("?")[0] != cfg.path:
                return self._reply(404, "not found")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._reply(413, "bad size")
            try:
                packet = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._reply(400, "invalid json")
            if not isinstance(packet, dict) or not hmac.compare_digest(
                str(packet.get("secret", "")).encode(), cfg.webhook_secret.encode()
            ):
                logger.warning("シークレット不一致のリクエストを拒否 (%s)", self.client_address[0])
                return self._reply(401, "unauthorized")
            packet.pop("secret", None)
            enqueue(packet)
            self._reply(200, "ok")

        def log_message(self, fmt, *args):
            logger.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(cfg: AgentConfig | None = None) -> None:
    from ai_agent.pipeline import Pipeline, PacketError

    cfg = cfg or AgentConfig()
    if not cfg.webhook_secret or cfg.webhook_secret == "change-me":
        raise SystemExit("WEBHOOK_SECRET を設定してください（インジケーターの「Webhook シークレット」と同じ値）")

    q: "queue.Queue[dict]" = queue.Queue()

    def worker():
        pipeline = Pipeline(cfg)  # SQLite 接続はこのスレッド専用
        while True:
            packet = q.get()
            try:
                pipeline.process(packet)
            except PacketError as e:
                logger.warning("不正なパケット: %s", e)
            except Exception:
                logger.exception("パケット処理中のエラー")
            finally:
                q.task_done()

    threading.Thread(target=worker, daemon=True, name="ai-agent-worker").start()
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg, q.put))
    logger.info("Webhook 受信開始: http://%s:%s%s  (model=%s, effort=%s)",
                cfg.host, cfg.port, cfg.path, cfg.model, cfg.effort)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("停止します")
    finally:
        httpd.server_close()
