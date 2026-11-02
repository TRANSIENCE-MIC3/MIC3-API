"""Read the maintained catalog input; tests do not duplicate its mode or command."""

import json
from pathlib import Path
from unittest.mock import Mock

from mic3_api.application.models.catalog import RegisterRelease, ReleaseDefinition

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "integrations/eu_mfa/release.json").read_text())
MODE = next(iter(CONFIG["execution_definition"]["modes"]))
PARAMETERS = {"mode": MODE}


def release_definition(config: dict = CONFIG) -> ReleaseDefinition:
    """Validate a release config the way registration does, without a database."""
    catalog = Mock()
    RegisterRelease(catalog).execute(config)
    return catalog.register.call_args.args[0]
