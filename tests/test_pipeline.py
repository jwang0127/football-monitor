import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("radar", ROOT / "yuanbao_python_20260820_ZhZRAn.py")
radar = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = radar
SPEC.loader.exec_module(radar)


class PipelineTests(unittest.TestCase):
    def test_secret_quotes_are_removed(self):
        self.assertEqual(radar.clean_secret("  'abc'  "), "abc")
        self.assertEqual(radar.clean_secret('"abc"'), "abc")

    def test_implied_probabilities_are_normalized(self):
        probs = radar.implied([2.0, 3.0, 4.0])
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertGreater(probs[0], probs[1])

    def test_blank_audit_finds_nested_values(self):
        self.assertEqual(radar.no_blank_paths({"a": [1, {"b": ""}]}), ["$.a[1].b"])

    def test_crs_can_supply_missing_had(self):
        match = {"matchNumStr": "周四009", "pools": {
            "had": {"status": "not_offered_by_official_api"},
            "crs": {"status": "offered", "values": {
                "s01s00": 2.0, "s00s00": 5.0, "s00s01": 10.0,
                "s1sh": 15.0, "s1sd": 30.0, "s1sa": 40.0,
            }},
        }}
        probs, source = radar.market_probabilities(match)
        self.assertEqual(source, "CRS-outcome-marginal")
        self.assertAlmostEqual(sum(probs.values()), 1.0)
        self.assertGreater(probs["home"], probs["away"])


if __name__ == "__main__":
    unittest.main()
