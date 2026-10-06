"""Read the maintained catalog input; tests do not duplicate its mode or command."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "integrations/eu_mfa/release.json").read_text())
MODE = next(iter(CONFIG["execution_definition"]["modes"]))
PARAMETERS = {"mode": MODE}
