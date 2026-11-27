#!/usr/bin/env python3
"""Thin wrapper: works from a fresh checkout, no install (Windows, macOS, Linux, Raspberry Pi)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench.fetch import main  # noqa: E402

sys.exit(main())
