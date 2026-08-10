#!/usr/bin/env python3
"""Split the companies still missing a debate into shards for a matrix build.

The local runner needed the laptop switched on. This lets the same work happen
in Actions instead, spread across parallel jobs: each shard gets a disjoint
slice of the remaining universe, bakes it into a scratch directory, and uploads
it. Nothing is shared between shards, so there is no push race and a shard that
dies costs only its own slice.

Emits a GitHub matrix on stdout:

    {"include": [{"shard": 0, "codes": "RELIANCE,TCS,..."}, ...]}

Sharding is ROUND ROBIN over a market-cap-ordered list, not contiguous blocks.
Contiguous blocks would hand shard 0 every megacap and shard 7 every microcap,
and the packs are not the same size -- a large company carries more evidence,
more documents and a longer debate. Round robin gives every shard the same mix,
so they finish together instead of one running four hours past the others.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_freshness():
    """Load earnings_intel/data/freshness.py WITHOUT importing the package.

    `from earnings_intel.data.freshness import ...` executes
    earnings_intel/__init__.py, which pulls in the pipeline, the data package
    and finally numpy. This planner runs in a job that installs nothing -- that
    is the point of it, it reads JSON and prints a matrix in ten seconds -- so
    the package import killed every scheduled run at the first step with
    ModuleNotFoundError: numpy, and the whole workflow was skipped behind it.

    freshness.py itself is pure stdlib, so loading the file directly gets the
    tested logic with none of the package's dependencies.
    """
    import importlib.util

    path = ROOT / "earnings_intel" / "data" / "freshness.py"
    spec = importlib.util.spec_from_file_location("_scanx_freshness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


is_stale = _load_freshness().is_stale


def _num(value) -> float:
    if value is None:
        return 0.0
    m = re.search(r"-?\d+\.?\d*", str(value).replace(",", ""))
    return float(m.group()) if m else 0.0


def remaining(fundamental_dir: Path, debate_dir: Path) -> list[str]:
    """Codes due for a debate, largest company first. PURE-ish (reads disk).

    Due means either NEVER argued, or argued against filings that have since
    moved -- a new quarter, or restated numbers. Without the second test a
    company could publish a result that halved its margin and its bull/bear
    case would sit there unchanged and confident until someone noticed.
    """
    rows: list[tuple[float, str]] = []
    for path in fundamental_dir.glob("*.json"):
        code = path.stem
        if code == "index":
            continue

        bundle = None
        mcap = 0.0
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(bundle, dict):
                overview = (bundle.get("fundamental") or {}).get("overview") or {}
                mcap = _num(overview.get("Market Cap"))
        except Exception:  # noqa: BLE001
            # An unreadable bundle still deserves a debate attempt; it just
            # sorts last rather than dropping out of the universe silently.
            bundle = None

        debate_path = debate_dir / f"{code}.json"
        if debate_path.exists():
            try:
                debate = json.loads(debate_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                debate = None
            if not is_stale(debate, bundle):
                continue

        rows.append((mcap, code))

    rows.sort(key=lambda r: (-r[0], r[1]))
    return [code for _, code in rows]


def shard(codes: list[str], count: int, per_shard_cap: int = 0,
          providers: Sequence[str] | None = None) -> list[dict]:
    """Round-robin `codes` into `count` shards, one provider each.

    Each shard is pinned to ONE model provider, assigned round-robin from
    whatever credentials exist. That is what makes extra models worth having:
    a single provider parallelised eight ways just hits its own rate limit
    eight times faster, whereas eight providers running one stream each are
    eight independent quotas and eight independent outages.

    With no providers supplied every shard gets "", meaning "first credentialled
    provider" -- the previous behaviour.
    """
    count = max(1, int(count))
    buckets: list[list[str]] = [[] for _ in range(count)]
    for i, code in enumerate(codes):
        buckets[i % count].append(code)

    pool = [p.strip() for p in (providers or []) if str(p).strip()]
    out = []
    for i, bucket in enumerate(buckets):
        if per_shard_cap > 0:
            bucket = bucket[:per_shard_cap]
        if bucket:
            out.append({"shard": i, "codes": ",".join(bucket), "n": len(bucket),
                        "provider": pool[i % len(pool)] if pool else ""})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shards", type=int, default=8)
    ap.add_argument("--per-shard-cap", type=int, default=0,
                    help="max companies handed to one shard (0 = no cap). A shard "
                         "that cannot finish its list is not a problem -- the "
                         "leftovers are simply still missing on the next run.")
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--debate", default=str(ROOT / "docs" / "data" / "debate"))
    ap.add_argument("--providers", default="",
                    help="comma-separated provider names to spread across shards "
                         "(e.g. gemini,deepseek,mistral,ollama). Each shard is "
                         "pinned to one, so N credentials give N independent "
                         "quotas rather than N ways to hit the same one.")
    ap.add_argument("--github-output", default="",
                    help="also append matrix= and remaining= to this file")
    args = ap.parse_args()

    codes = remaining(Path(args.fundamental), Path(args.debate))
    providers = [x for x in args.providers.split(",") if x.strip()]
    matrix = {"include": shard(codes, args.shards, args.per_shard_cap, providers)}

    print(json.dumps(matrix, separators=(",", ":")))
    used = sorted({e["provider"] for e in matrix["include"] if e.get("provider")})
    print(f"[shards] {len(codes)} companies still need a debate, "
          f"across {len(matrix['include'])} shard(s)"
          f"{' on ' + ', '.join(used) if used else ''}", file=sys.stderr)

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\n")
            fh.write(f"remaining={len(codes)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
