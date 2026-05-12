#!/usr/bin/env python3
"""Compile and smoke-test local imports for Home Assistant AI Companion."""
import os
import py_compile
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cache_dir = ROOT / "__pycache__"
if cache_dir.exists():
    shutil.rmtree(cache_dir)
    print(f"Cleared {cache_dir}")

for path in sorted(ROOT.glob("*.py")):
    print(f"  {path.name}: {path.stat().st_size:,} bytes")

for name in ["config.py", "shared.py", "skills.py", "assistant.py"]:
    try:
        py_compile.compile(str(ROOT / name), doraise=True)
        print(f"✅ {name} syntax OK")
    except py_compile.PyCompileError as exc:
        print(f"❌ {name}: {exc}")

for module in ["config", "shared", "skills"]:
    try:
        result = subprocess.run(
            ["python3", "-c", f"import {module}; print('{module} OK')"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        print(f"{module} import: {result.stdout.strip()} {result.stderr.strip()[:200]}")
    except Exception as exc:
        print(f"{module} import error: {exc}")
