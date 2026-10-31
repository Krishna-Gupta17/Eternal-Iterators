from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / "CrunchWeek_StarterPack"
DATASET = STARTER / "data" / "cit_public.json"
SAMPLE_DISRUPTION = STARTER / "examples" / "sample_disruption.json"
VALIDATOR = STARTER / "validator.py"
EXPORTS = ROOT / "exports"
STATE_DIR = ROOT / "backend" / "state"
PROMPTS = ROOT / "backend" / "prompts"
