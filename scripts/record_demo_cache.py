"""Record real Claude extractions of the sample quotes so demo mode replays genuine model output.

Usage (PowerShell):
  $env:ANTHROPIC_API_KEY = "sk-ant-..."
  python scripts/record_demo_cache.py

Costs a few cents. Overwrites the hand-labeled fixtures in data/demo_cache/.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bidlens import config  # noqa: E402
from bidlens.extract import live_extraction  # noqa: E402
from bidlens.ingest import extract_text, text_sha  # noqa: E402
from bidlens.schemas import RFQ  # noqa: E402


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Set ANTHROPIC_API_KEY first.")
    rfq = RFQ.model_validate(json.loads((config.SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))
    total = 0.0
    for path in sorted(config.SAMPLES_DIR.iterdir()):
        if path.suffix not in (".pdf", ".xlsx"):
            continue
        text = extract_text(path.name, path.read_bytes())
        quote, meta = live_extraction(text, rfq, api_key)
        if not quote:
            print(f"FAILED {path.name}: {meta.get('error')}")
            continue
        meta.update(source="claude", recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        record = {"filename": path.name, "quote": quote, "meta": meta}
        (config.DEMO_CACHE_DIR / f"{text_sha(text)}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        total += meta["cost_usd"]
        print(f"recorded {path.name}: {meta['input_tokens']} in / {meta['output_tokens']} out, "
              f"${meta['cost_usd']:.4f}, {meta['latency_s']:.1f}s")
    print(f"total ${total:.4f}")


if __name__ == "__main__":
    main()
