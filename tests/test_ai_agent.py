"""AI Agent Lens のテスト（Claude API・ネットワーク不要）。  python -m unittest discover tests"""
import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from ai_agent.config import AgentConfig
from ai_agent.judge import Decision, sanitize
from ai_agent.pipeline import Pipeline, PacketError, validate_packet
from ai_agent.server import make_handler
from ai_agent.store import AgentStore

SAMPLE = json.loads((Path(__file__).resolve().parent.parent / "examples" / "sample_setup_packet.json").read_text())
H4 = 4 * 3600 * 1000


def cfg(**kw):
    c = AgentConfig()
    c.db_path = ":memory:"
    c.line_token = c.telegram_token = ""
    c.initial_equity = 10000.0
    c.max_risk_pct = 1.0
    c.min_confidence = 60
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class FakeJudge:
    def __init__(self, decision: Decision):
        self.decision = decision
        self.calls = 0

    def decide(self, packet, account, recent):
        self.calls += 1
        return sanitize(copy.deepcopy(self.decision), packet, cfg())


class SilentNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def short_decision(**kw):
    base = dict(decision="SHORT", confidence=78, entry=72600, stop=74400, target1=69900, target2=67200,
                risk_pct=0.5, failure_probability=35, failure_scenario="x", key_factors=["a"], reasoning="r")
    base.update(kw)
    return Decision(**base)


def bar_packet(t, h, l, c):
    p = copy.deepcopy(SAMPLE)
    p.pop("secret")
    p.update(kind="BAR", event="", time=t, setup=None)
    p["bar"].update(h=h, l=l, c=c)
    p["recent"] = p["recent"][1:] + [{"t": t, "o": c, "h": h, "l": l, "c": c, "v": 1}]
    return p


class TestSanitize(unittest.TestCase):
    def test_valid_short_kept(self):
        d = sanitize(short_decision(), SAMPLE, cfg())
        self.assertEqual(d.decision, "SHORT")
        self.assertEqual(d.entry, 72600)

    def test_inconsistent_levels_fall_back_to_indicator(self):
        d = sanitize(short_decision(stop=70000), SAMPLE, cfg())
        self.assertEqual(d.decision, "SHORT")
        self.assertEqual(d.stop, 74400)
        self.assertIn("インジケーター", d.note)

    def test_inconsistent_levels_opposite_side_becomes_wait(self):
        d = sanitize(short_decision(decision="LONG"), SAMPLE, cfg())
        self.assertEqual(d.decision, "WAIT")

    def test_low_confidence_becomes_wait(self):
        d = sanitize(short_decision(confidence=40), SAMPLE, cfg())
        self.assertEqual(d.decision, "WAIT")
        self.assertEqual(d.risk_pct, 0.0)

    def test_risk_is_capped(self):
        d = sanitize(short_decision(risk_pct=5), SAMPLE, cfg(max_risk_pct=1.0))
        self.assertEqual(d.risk_pct, 1.0)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.notifier = SilentNotifier()
        self.judge = FakeJudge(short_decision())
        self.p = Pipeline(self.cfg, judge=self.judge, notifier=self.notifier, store=AgentStore(":memory:"))
        setup = copy.deepcopy(SAMPLE)
        setup.pop("secret")
        self.r = self.p.process(setup)

    def test_setup_opens_trade_with_risk_based_size(self):
        self.assertEqual(self.r["decision"]["decision"], "SHORT")
        trade = self.p.trader.open_trades()[0]
        # 10000 * 0.5% / 1800 = 0.02777...
        self.assertAlmostEqual(trade["size"], 50 / 1800)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_duplicate_packet_ignored(self):
        setup = copy.deepcopy(SAMPLE)
        setup.pop("secret")
        self.assertEqual(self.p.process(setup)["status"], "duplicate")
        self.assertEqual(self.judge.calls, 1)

    def test_target1_then_breakeven(self):
        t0 = SAMPLE["time"]
        r1 = self.p.process(bar_packet(t0 + H4, h=72000, l=69800, c=70100))
        self.assertEqual([e["kind"] for e in r1["trade_events"]], ["target1_hit"])
        r2 = self.p.process(bar_packet(t0 + 2 * H4, h=72700, l=70000, c=72500))
        self.assertEqual([e["kind"] for e in r2["trade_events"]], ["breakeven_stop"])
        s = self.p.trader.stats()
        # 半分を 2700 幅で利確: 0.5 * (50/1800) * 2700 = 37.5
        self.assertAlmostEqual(s["equity"], 10037.5, places=2)
        self.assertEqual(s["closed_trades"], 1)

    def test_stop_hit_loses_one_r(self):
        r = self.p.process(bar_packet(SAMPLE["time"] + H4, h=74500, l=72000, c=74450))
        self.assertEqual([e["kind"] for e in r["trade_events"]], ["stop_hit"])
        self.assertAlmostEqual(self.p.trader.equity(), 9950.0)
        self.assertAlmostEqual(self.p.trader.stats()["avg_r"], -1.0)

    def test_stop_wins_when_bar_hits_both(self):
        r = self.p.process(bar_packet(SAMPLE["time"] + H4, h=74500, l=69000, c=72000))
        self.assertEqual([e["kind"] for e in r["trade_events"]], ["stop_hit"])

    def test_target2_in_one_bar(self):
        r = self.p.process(bar_packet(SAMPLE["time"] + H4, h=72700, l=67000, c=67100))
        self.assertEqual([e["kind"] for e in r["trade_events"]], ["target1_hit", "target2_hit"])
        self.assertAlmostEqual(self.p.trader.stats()["avg_r"], 2.25)

    def test_no_second_trade_while_open(self):
        setup = copy.deepcopy(SAMPLE)
        setup.pop("secret")
        setup["time"] += H4
        r = self.p.process(setup)
        self.assertNotIn("trade_opened", r)
        self.assertEqual(len(self.p.trader.open_trades()), 1)


class TestValidation(unittest.TestCase):
    def test_rejects_missing_keys(self):
        with self.assertRaises(PacketError):
            validate_packet({"kind": "BAR"})

    def test_rejects_bad_ohlc(self):
        p = copy.deepcopy(SAMPLE)
        p["bar"]["c"] = None
        with self.assertRaises(PacketError):
            validate_packet(p)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.received = []
        c = cfg(webhook_secret="s3cret", path="/webhook")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(c, self.received.append))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/webhook"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, body: dict):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_wrong_secret_rejected(self):
        self.assertEqual(self.post({**SAMPLE, "secret": "nope"}), 401)
        self.assertEqual(self.received, [])

    def test_valid_secret_enqueued_without_secret(self):
        self.assertEqual(self.post({**SAMPLE, "secret": "s3cret"}), 200)
        self.assertEqual(len(self.received), 1)
        self.assertNotIn("secret", self.received[0])


if __name__ == "__main__":
    unittest.main()
