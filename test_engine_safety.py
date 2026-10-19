import ast
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import config
import double_bottom as db
import entry_logic as el
import resonance


def evidence(kind="double_bottom", target=120):
    return {"score": 80, "direction": "long" if kind == "double_bottom" else "short",
            "pattern": {"found": True, "type": kind, "L1": {"price": 100}, "L2": {"price": 100},
                        "neck": 110 if kind == "double_bottom" else 90,
                        "confirmations": ["L2反转K线", "放量突破颈位", "回踩不破颈位"], "rsi_divergence": True},
            "levels": {"support": [], "resistance": []}, "momentum": {"resonance_count": 3},
            "volume_oi": {"healthy": True, "direction": "long", "quadrant": "健康多头"},
            "plan": {"target1": target}}


class EngineSafetyTests(unittest.TestCase):
    @staticmethod
    def chart_indicators(bars):
        # 只编译纯计算函数，测试不导入app、不触发账户配置或服务初始化。
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8-sig")
        func = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "_attach_mas")
        namespace = {"math": math, "double_bottom": db, "CHART_MAS": (5, 20, 60), "CHART_BARS": 60}
        exec(compile(ast.Module(body=[func], type_ignores=[]), "app._attach_mas", "exec"), namespace)
        return namespace["_attach_mas"](bars)

    def test_chart_rsi_starts_at_period_and_consumes_next_loss(self):
        bars = [{"open": c, "close": c, "high": c, "low": c, "volume": 0}
                for c in list(range(100, 115)) + [113, 112]]
        result = self.chart_indicators(bars)
        self.assertTrue(all(b["rsi"] is None for b in result[:14]))
        self.assertEqual([b["rsi"] for b in result[14:]], [100, 92.9, 86.2])

    def test_window_vwap_ignores_zero_invalid_and_missing_volume(self):
        bars = [{"open": c, "close": c, "high": c, "low": c, "volume": v}
                for c, v in ((100, 0), (10, 2), (1000, 0), (20, 6), (999, float("nan")), (999, None))]
        result = self.chart_indicators(bars)
        self.assertEqual([b["vwap"] for b in result], [None, 10, 10, 17.5, 17.5, 17.5])

    def test_rsi_initial_value_and_every_subsequent_change(self):
        self.assertEqual(db._rsi_list([100, 101, 102, 103, 102, 101], 3),
                         [None, None, None, 100, 66.67, 44.44])
        self.assertEqual(db._rsi_list([100] * 5, 3), [None, None, None, 50, 50])

    def test_broken_structure_and_target_behind_entry_are_rejected(self):
        for price in (95, 120, 130):
            d = el.decide(evidence(), price, atr=1, small_tf_ok=True, tick=1)
            self.assertEqual(d["quadrant"], "avoid")
            self.assertEqual(d["position_pct"], 0)
            self.assertIsNone(d["stop"])
        d = el.decide(evidence("double_top", target=80), 105, atr=1, small_tf_ok=True, tick=1)
        self.assertEqual(d["quadrant"], "avoid")

    def test_confirmation_false_or_missing_cannot_produce_main(self):
        for small in (False, None):
            d = el.decide(evidence(), 100, atr=1, small_tf_ok=small, tick=1)
            self.assertNotEqual(d["quadrant"], "main")
        self.assertEqual(el.decide(evidence(), 100, atr=1, small_tf_ok=True, tick=1)["quadrant"], "main")

    def test_confirm_stop_is_revalidated_after_replacement(self):
        # 价格仍在L2之上，但颈位下1ATR=109高于入场108，不能给出错误方向止损。
        ev = evidence()
        ev["levels"] = {"support": [], "resistance": []}
        with patch.object(config, "AX_LOCATION_HIGH", 101):
            d = el.decide(ev, 108, atr=1, small_tf_ok=True, tick=1)
        self.assertEqual(d["quadrant"], "avoid")
        self.assertIn("止损", d["wait_for"])

    def test_tick_alignment_and_signed_rr(self):
        d = el.decide(evidence(), 101, atr=1.1, small_tf_ok=True, tick=0.5)
        self.assertEqual(d["stop"]["price"], 98)
        _, items, _ = el._location_score(evidence(), "bottom", 130, 1, 1, tick=1)
        self.assertEqual(items[-1]["value"], 0)

    def test_warehouse_direction_is_not_awarded_to_opposite_trade(self):
        ev = evidence("double_top", 80)
        _, items = el._certainty_score(ev, "short", 100, False)
        self.assertFalse(any("仓量方向" in x["item"] for x in items))
        ev["volume_oi"] = {"healthy": False, "direction": "short", "quadrant": "空头增仓"}
        _, items = el._certainty_score(ev, "short", 100, False)
        self.assertTrue(any("仓量方向" in x["item"] for x in items))

    def test_flat_price_with_rising_oi_is_not_short_direction(self):
        b = [{"close": 100, "volume": 100 if i < 15 else 300, "oi": 10} for i in range(20)]
        d = db._volume_oi(b)
        self.assertNotEqual(d.get("direction"), "short")
        self.assertFalse(d["healthy"])

    def test_track_b_plan_is_tick_aligned_and_checks_target_direction(self):
        b = [{"open": 102.2, "close": 102.2, "high": 103, "low": 101, "volume": 100, "oi": 1} for _ in range(40)]
        p = evidence()["pattern"]
        levels = {"support": [{"price": 100, "score": 100, "label": "强"}], "resistance": []}
        with patch.object(db, "_levels", return_value=levels), patch.object(db, "_momentum", return_value={"resonance_count": 3, "macd": True, "rsi": True, "ma_slope": True}), patch.object(db, "_volume_oi", return_value={"quadrant": "健康多头", "healthy": True, "direction": "long"}):
            d = db._build_candidate(b, p, "bottom", p["confirmations"], True, tick=5)
            self.assertEqual(d["direction"], "long")
            for key in ("entry", "stop", "target1"):
                self.assertEqual(d["plan"][key] % 5, 0)
            b[-1]["close"] = 125
            d = db._build_candidate(b, p, "bottom", p["confirmations"], True, tick=5)
            self.assertIsNone(d["plan"])
            self.assertEqual(d["direction"], "wait")

    def test_explicit_trigger_tick_is_isolated_from_global_config(self):
        bars = [{"close": 100 + i * 0.2} for i in range(10)]
        with patch.object(config, "PRICE_TICK", 100):
            self.assertTrue(resonance.trigger_ok(bars, "long", tick=0.2))
            self.assertFalse(resonance.trigger_ok(bars, "long", tick=1))

    def test_direct_probe_requires_actual_historical_target(self):
        bars = [{"open": 100, "close": 100, "high": 101, "low": 99} for _ in range(40)]
        res = {"resonance": True, "direction": "long", "legs": {}}
        with patch.object(config, "DIRECT_PROBE_ENABLED", True):
            self.assertIsNone(el.direct_probe(102, 2, res, bars, tick=1))
            bars[10]["high"] = 120
            d = el.direct_probe(102, 2, res, bars, tick=1)
            self.assertIsNotNone(d)
            self.assertEqual(d["target"], 120)
            self.assertEqual(d["target_basis"], "已确认历史摆动障碍")


if __name__ == "__main__":
    unittest.main()
