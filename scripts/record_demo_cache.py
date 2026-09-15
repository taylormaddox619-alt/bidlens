"""Record real Claude extractions of the sample quotes so demo mode replays genuine model output.

Usage:
  python scripts/record_demo_cache.py
  (reads ANTHROPIC_API_KEY from the environment or .streamlit/secrets.toml)

Costs a few cents. Overwrites the hand-labeled fixtures in data/demo_cache/.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bidlens import config  # noqa: E402
from bidlens.extract import routed_extraction  # noqa: E402
from bidlens.ingest import extract_text, text_sha  # noqa: E402
from bidlens.credentials import get_secret  # noqa: E402
from bidlens.schemas import RFQ  # noqa: E402


def main() -> None:
    api_key = get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("No API key found. Add ANTHROPIC_API_KEY to .streamlit/secrets.toml or the environment.")
    rfq = RFQ.model_validate(json.loads((config.SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))
    total = 0.0
    for path in sorted(config.SAMPLES_DIR.iterdir()):
        if path.suffix not in (".pdf", ".xlsx"):
            continue
        text = extract_text(path.name, path.read_bytes())
        quote, meta = routed_extraction(text, rfq, api_key)
        if not quote:
            print(f"FAILED {path.name}: {meta.get('error')}")
            continue
        meta.update(source="claude", recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        record = {"filename": path.name, "quote": quote, "meta": meta}
        (config.DEMO_CACHE_DIR / f"{text_sha(text)}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        total += meta["cost_usd"]
        route = (f"escalated {meta['first_pass_model']} -> {meta['model']} ({'; '.join(meta['escalation_reasons'])})"
                 if meta.get("escalated") else meta["model"])
        print(f"recorded {path.name}: {route}, {meta['input_tokens']} in / {meta['output_tokens']} out, "
              f"${meta['cost_usd']:.4f}, {meta['latency_s']:.1f}s")
    print(f"total ${total:.4f}")


if __name__ == "__main__":
    main()
