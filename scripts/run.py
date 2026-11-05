#!/usr/bin/env python3
"""Start this product with a clean environment; API mode needs a separate binding."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
env = {"PATH": os.defpath, "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)}
os.chdir(ROOT)
python=sys.executable
config=ROOT/".private/toolchain.json"
if config.exists():
    if config.is_symlink() or config.stat().st_uid!=os.getuid() or config.stat().st_mode & 0o077:
        raise SystemExit("Unsafe toolchain configuration.")
    enrolled=json.loads(config.read_text());record=enrolled.get("apiPython")
    if record:
        candidate=Path(record["path"])
        if (candidate.is_symlink() or not candidate.is_file() or
                hashlib.sha256(candidate.read_bytes()).hexdigest()!=record["sha256"] or
                hashlib.sha256((ROOT/"requirements-api.txt").read_bytes()).hexdigest()!=enrolled.get("apiRequirementsSha256")):
            raise SystemExit("Optional API toolchain changed; rerun its bounded bootstrap.")
        python=str(candidate)
arguments=sys.argv[1:];module="hellabit.server"
if arguments and arguments[0]=="--api-config":
    if len(arguments)!=2:raise SystemExit("Use --api-config with exactly one owner-only configuration file.")
    module="hellabit.api";arguments=["--config",arguments[1]]
os.execve(python, [python, "-m", module, *arguments], env)
