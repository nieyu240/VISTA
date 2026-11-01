#!/usr/bin/env python3
"""Score exported building state against a versioned public target manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
source_root = project_root / "src"
sys.path.insert(0, str(source_root))

from vista.benchmarks.gameworld.building import load_building_score  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    score = load_building_score(args.target, args.actual).as_dict()
    result = {
        "protocol": "building-score-v1",
        "target": str(args.target.resolve()),
        "actual": str(args.actual.resolve()),
        "score": score,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
