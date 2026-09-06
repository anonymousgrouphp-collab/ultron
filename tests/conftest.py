"""pytest bootstrap: make the repo root importable (tests do `import main`, `import ui`, etc.).

P0-E2 contract: characterization tests are READ-ONLY on src — they import, inspect and pin
current behavior. No network, no API keys, no Qt event loop (no QApplication instantiation).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
