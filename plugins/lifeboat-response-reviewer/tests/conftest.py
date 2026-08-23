"""Let the plugin's tests import it now that it lives inside the repo.

They were written when this plugin sat alone in its own directory and could
import `reviewer` directly. Moving it under Life-Boat kept the code and broke
that assumption, so the plugin's own directory goes on the path here rather
than rewriting every import.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
