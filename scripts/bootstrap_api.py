#!/usr/bin/env python3
"""One-time, hash-locked setup for the optional API/backup verification tools."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".private"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    owner = ROOT.parents[1]
    gate = owner / "Scripts/xxv_context_gate.py"
    env = {"PATH": os.defpath, "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    if gate.is_file() and (owner / "config/xxv/context-manifest.json").is_file():
        subprocess.run([sys.executable, str(gate)], cwd=ROOT, env=env, check=True)
    PRIVATE.mkdir(exist_ok=True, mode=0o700)
    target = PRIVATE / "api-venv"
    if target.is_symlink(): raise SystemExit("Unsafe API environment path")
    if not target.exists():
        subprocess.run([sys.executable, "-m", "venv", "--copies", str(target)], env=env, check=True, timeout=60)
    python = target / "bin/python3"
    if python.is_symlink() or not python.is_file() or python.stat().st_uid != os.getuid():
        raise SystemExit("Unsafe API interpreter")
    subprocess.run([str(python), "-m", "pip", "install", "--no-cache-dir", "--only-binary=:all:",
                    "--require-hashes", "--index-url", "https://pypi.org/simple", "-r", str(ROOT / "requirements-api.txt")],
                   cwd=ROOT, env=env, check=True, timeout=180)
    expected = json.loads((ROOT / "API-DEPENDENCIES.json").read_text())["packages"]
    probe = "import importlib.metadata,json;print(json.dumps({n:importlib.metadata.version(n) for n in " + repr([p["name"] for p in expected]) + "}))"
    actual = json.loads(subprocess.check_output([str(python), "-c", probe], env=env, text=True, timeout=15))
    if any(actual[p["name"]] != p["version"] for p in expected):
        raise SystemExit("API dependency versions do not match the lock")
    config_path = PRIVATE / "toolchain.json"
    if config_path.exists() and (config_path.is_symlink() or config_path.stat().st_uid != os.getuid() or
                                 config_path.stat().st_mode & 0o077):
        raise SystemExit("Unsafe toolchain enrollment")
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config["apiPython"] = {"path": str(python), "sha256": sha(python)}
    config["apiRequirementsSha256"] = sha(ROOT / "requirements-api.txt")
    config["apiDependenciesSha256"] = sha(ROOT / "API-DEPENDENCIES.json")
    temp = PRIVATE / "toolchain-api.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
    temp.replace(config_path)
    print(json.dumps({"ok": True, "optionalApiPackages": len(actual), "newPaidServices": 0}))

if __name__ == "__main__": main()
