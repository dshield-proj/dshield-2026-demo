#!/usr/bin/env python3
"""Compatibility entry point for users who run `python sync_rw.sh`."""

from pathlib import Path
import os
import runpy


script = Path(__file__).resolve()
os.chdir(script.parent)
runpy.run_path(str(script.with_suffix(".py")), run_name="__main__")
