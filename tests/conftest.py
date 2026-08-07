import sys
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGFORGE_ROOT / "service"))
sys.path.insert(0, str(AGFORGE_ROOT / "scripts"))
