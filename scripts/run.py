#!/usr/bin/env python3
"""Start only this product's loopback sandbox with a small clean environment."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
owner = ROOT.parents[1]
gate = owner / "Scripts" / "xxv_context_gate.py"
env = {"PATH": os.defpath, "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)}
os.chdir(ROOT)
if gate.is_file() and (owner / "config/xxv/context-manifest.json").is_file():
    result = subprocess.run([sys.executable, str(gate)], cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("Canonical context gate failed; sandbox was not started.")
os.execve(sys.executable, [sys.executable, "-m", "hellabit.server", *sys.argv[1:]], env)
