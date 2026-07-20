#!/usr/bin/env python3
"""Zero-argument entrypoint for the forced SSH health status command."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.health_status_export import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
