import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bidlens.config import GROUND_TRUTH_DIR, SAMPLES_DIR  # noqa: E402
from bidlens.ingest import extract_text  # noqa: E402
from bidlens.schemas import RFQ  # noqa: E402


@pytest.fixture
def rfq() -> RFQ:
    return RFQ.model_validate(json.loads((SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))


@pytest.fixture
def samples() -> dict[str, dict]:
    """Ground-truth quote + extracted document text, keyed by short supplier name."""
    out = {}
    for path in sorted(GROUND_TRUTH_DIR.glob("*.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        truth["text"] = extract_text(truth["filename"], (SAMPLES_DIR / truth["filename"]).read_bytes())
        out[path.stem.split("_")[1].lower()] = truth
    return out
