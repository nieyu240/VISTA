"""Blind control policies must depend only on the public action schema."""

import unittest

from vista.benchmarks.gameworld.controls import BlindPolicy

SCHEMA = {
    "oneOf": [
        {
            "properties": {
                "action": {"const": "wait"},
                "duration": {"minimum": 0, "maximum": 2},
            }
        },
        {
            "properties": {
                "action": {"const": "press_key"},
                "key": {"enum": ["ArrowUp", "ArrowDown"]},
                "duration": {"minimum": 0, "maximum": 2},
            }
        },
    ]
}


class BlindPolicyTests(unittest.TestCase):
    def test_repeat_is_fixed_and_blind_sequence_is_seeded(self):
        left = BlindPolicy(SCHEMA, seed=17)
        right = BlindPolicy(SCHEMA, seed=17)
        self.assertEqual(
            left.next("repeat"),
            {"action": "press_key", "key": "ArrowUp", "duration": 0.2},
        )
        self.assertEqual(
            [left.next("blind") for _ in range(10)],
            [right.next("blind") for _ in range(10)],
        )


if __name__ == "__main__":
    unittest.main()
