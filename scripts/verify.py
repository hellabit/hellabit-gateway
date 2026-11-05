#!/usr/bin/env python3
"""One local verification rail, one source checkpoint, one terminal receipt."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PRIVATE, EVIDENCE, RELEASE = ROOT / ".private", ROOT / "evidence", ROOT / "release"
parser = argparse.ArgumentParser(); parser.add_argument("--skip-browser", action="store_true"); args = parser.parse_args()


def sha(data): return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
    temporary.chmod(0o600); temporary.replace(path)


def enrolled_tool(name, fallback, config):
    record = config.get(name)
    if record:
        candidate = Path(record["path"])
        if candidate.is_symlink() or not candidate.is_file() or sha(candidate.read_bytes()) != record["sha256"]:
            raise RuntimeError(name + " toolchain binding changed")
        return str(candidate)
    candidate = shutil.which(fallback)
    if not candidate: raise RuntimeError(fallback + " is required")
    return str(Path(candidate).resolve())


def empty_npm_config(name):
    path = PRIVATE / name
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_size:
        raise RuntimeError("Unsafe npm config boundary")
    return str(path)


def run(label, command, env, timeout=120):
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    (EVIDENCE / (label + ".log")).write_text(result.stdout + result.stderr)
    (EVIDENCE / (label + ".log")).chmod(0o600)
    if result.returncode: raise RuntimeError(label + " failed; see evidence/" + label + ".log")
    return result.stdout + result.stderr


def dependency_inventory():
    lock = json.loads((ROOT / "package-lock.json").read_text())
    packages = []
    for name, record in sorted(lock["packages"].items()):
        if not name: continue
        packages.append({"path": name, "version": record.get("version"), "license": record.get("license"),
                         "integrity": record.get("integrity"), "source": record.get("resolved")})
    write_json(ROOT / "DEPENDENCIES.json", {"schema": "hellabit.dependencies.v1", "pythonMinimum": "3.11",
        "pythonRuntimePackages": [], "pythonRuntimeScope": "standard-library core console",
        "optionalPythonApi": {"inventory": "API-DEPENDENCIES.json", "requirements": "requirements-api.txt",
            "lockSha256": sha((ROOT / "requirements-api.txt").read_bytes())},
        "nodeDevelopmentVersion": "24.22.1", "packageLockSha256": sha((ROOT / "package-lock.json").read_bytes()),
        "packages": packages})


def source_files():
    sys.path.insert(0,str(ROOT))
    from hellabit.readiness import source_names
    files={}
    for name in sorted(source_names(ROOT)):
        p=ROOT/name
        if p.is_symlink() or not p.resolve().is_relative_to(ROOT.resolve()):raise RuntimeError("Source symlink is not portable")
        files[name]=p.read_bytes()
    for name, content in files.items():
        if len(content) > 2_000_000: raise RuntimeError("Unexpected source file size: " + name)
        if (str(Path.home()).encode() + b"/") in content or re.search(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", content):
            raise RuntimeError("Private or machine-bound source surface: " + name)
    if "licenses/OpenZeppelin-MIT.txt" not in files:
        raise RuntimeError("Required OpenZeppelin redistribution notice is missing")
    if len({n.casefold() for n in files}) != len(files): raise RuntimeError("Case-insensitive source collision")
    return files


def zip_bytes(files):
    entries = [{"path": n, "sha256": sha(b), "bytes": len(b)} for n, b in sorted(files.items())]
    content = {**files, "SOURCE-MANIFEST.json": (json.dumps({"schema": "hellabit.source-manifest.v1", "files": entries}, indent=2, sort_keys=True) + "\n").encode()}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3; info.external_attr = (stat.S_IFREG | 0o644) << 16; info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return stream.getvalue()


def inspect_archive(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
        expected = {x["path"] for x in manifest["files"]} | {"SOURCE-MANIFEST.json"}
        if set(archive.namelist()) != expected: raise RuntimeError("Archive file set mismatch")
        for item in manifest["files"]:
            name = item["path"]
            if name.startswith("/") or ".." in Path(name).parts or any(x in Path(name).parts for x in [".private", "node_modules", "evidence", "release"]):
                raise RuntimeError("Unsafe archive member")
            body = archive.read(name)
            if sha(body) != item["sha256"] or len(body) != item["bytes"]: raise RuntimeError("Archive content mismatch")
    return len(expected)


def main():
    started = time.monotonic()
    for p in [PRIVATE, EVIDENCE, RELEASE]: p.mkdir(exist_ok=True, mode=0o700)
    lock = open(PRIVATE / "verify.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    receipt = {"schema": "hellabit.terminal-result.v1", "mode": "local-foundation", "ok": False,
        "version": json.loads((ROOT / "package.json").read_text())["version"],
        "productionReady": False, "livePaymentsEnabled": False, "externalTransactions": 0, "realStablecoinsIssued": False}
    env = {"PATH": os.defpath, "LANG": "en_US.UTF-8", "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        config_path = PRIVATE / "toolchain.json"
        if config_path.exists():
            if config_path.is_symlink() or config_path.stat().st_uid != os.getuid() or stat.S_IMODE(config_path.stat().st_mode) != 0o600:
                raise RuntimeError("Unsafe local toolchain enrollment")
            config = json.loads(config_path.read_text())
        else: config = {}
        if not config.get("apiPython"):
            raise RuntimeError("Optional API verification tools missing; run python3 scripts/bootstrap_api.py once")
        if (config.get("apiRequirementsSha256") != sha((ROOT / "requirements-api.txt").read_bytes()) or
                config.get("apiDependenciesSha256") != sha((ROOT / "API-DEPENDENCIES.json").read_bytes())):
            raise RuntimeError("Optional API dependency lock changed; rerun its bounded bootstrap")
        api_python = enrolled_tool("apiPython", sys.executable, config)
        api_packages = json.loads((ROOT / "API-DEPENDENCIES.json").read_text())["packages"]
        probe = "import importlib.metadata,json;print(json.dumps({n:importlib.metadata.version(n) for n in " + repr([p["name"] for p in api_packages]) + "}))"
        installed = json.loads(subprocess.check_output([api_python, "-c", probe], cwd=ROOT, env=env, text=True, timeout=15))
        if any(installed[p["name"]] != p["version"] for p in api_packages):
            raise RuntimeError("Optional API installed versions do not match the lock")
        query = {"queries": [{"package": {"name": p["name"], "ecosystem": "PyPI"}, "version": p["version"]} for p in api_packages]}
        request = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=json.dumps(query).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            advisory = json.load(response)
        if len(advisory.get("results", [])) != len(api_packages):
            raise RuntimeError("Python dependency advisory response could not be verified")
        write_json(EVIDENCE / "python-dependency-audit.json", {"source": "https://api.osv.dev/v1/querybatch",
            "lockSha256": sha((ROOT / "requirements-api.txt").read_bytes()), "report": advisory})
        if any(item.get("vulns") for item in advisory["results"]):
            raise RuntimeError("Python dependency advisory blocks this source checkpoint")
        receipt["pythonApiPackages"], receipt["pythonDependencyAdvisories"] = len(api_packages), 0
        node = enrolled_tool("node", "node", config)
        openssl = enrolled_tool("openssl", "openssl", config)
        npm = enrolled_tool("npm", "npm", config)
        env["PATH"] = str(Path(node).parent) + os.pathsep + os.defpath
        version = subprocess.check_output([node, "--version"], env=env, text=True).strip()
        if int(version[1:].split(".")[0]) < 22: raise RuntimeError("Node 22+ is required for the private EVM")
        if not (ROOT / "node_modules/solc").is_dir(): raise RuntimeError("Development dependencies missing; run npm ci once")
        print(json.dumps({"stage": "public-dependency-advisories", "financialNetworkAccess": False}), flush=True)
        audit_env = {**env, "npm_config_cache": str(ROOT / ".cache/npm"), "npm_config_update_notifier": "false"}
        audited = subprocess.run([node, npm, "audit", "--json", "--registry=https://registry.npmjs.org", "--userconfig=" + empty_npm_config("npm-user-empty.conf"), "--globalconfig=" + empty_npm_config("npm-global-empty.conf")], cwd=ROOT, env=audit_env, capture_output=True, text=True, timeout=45)
        audit_report = json.loads(audited.stdout)
        vulnerabilities = audit_report.get("metadata", {}).get("vulnerabilities")
        if vulnerabilities is None: raise RuntimeError("Dependency advisory response was not verifiable")
        write_json(EVIDENCE / "dependency-audit.json", {"lockSha256": sha((ROOT / "package-lock.json").read_bytes()), "report": audit_report})
        if vulnerabilities.get("high", 0) or vulnerabilities.get("critical", 0): raise RuntimeError("High or critical dependency advisory blocks this source checkpoint")
        receipt["dependencyAdvisories"] = vulnerabilities
        receipt["dependencyLockSha256"] = sha((ROOT / "package-lock.json").read_bytes())
        print(json.dumps({"stage": "core-and-api", "mode": "offline"}), flush=True)
        output = run("python-tests", [api_python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"], env)
        receipt["pythonTests"] = int(re.search(r"Ran (\d+) tests?", output).group(1))
        print(json.dumps({"stage": "contracts-and-sdk"}), flush=True)
        run("contracts", [node, "tests/contracts.mjs"], env)
        contracts = json.loads((ROOT / "artifacts/contract-proof.json").read_text())
        receipt["contractChecks"] = len(contracts["checks"])
        output = run("sdk", [node, "--test", "tests/sdk.test.mjs"], env)
        receipt["sdkTests"] = int(re.search(r"\btests (\d+)", output).group(1))
        if not args.skip_browser:
            print(json.dumps({"stage": "desktop-mobile-journeys"}), flush=True)
            browser_env = dict(env)
            browser_env['HELLABIT_TEST_PYTHON']=api_python
            if config.get("playwright"):
                record = config["playwright"]; library = Path(record["path"])
                if sha((library / "package.json").read_bytes()) != record["package_sha256"]: raise RuntimeError("Browser library binding changed")
                browser_env["HELLABIT_PLAYWRIGHT"] = str(library)
            with tempfile.TemporaryDirectory(prefix="hellabit-browser-") as tmp:
                child = subprocess.Popen([api_python, str(ROOT / "scripts/run.py"), "--demo", "--state", str(Path(tmp) / "ledger.sqlite3")], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    selector = selectors.DefaultSelector(); selector.register(child.stdout, selectors.EVENT_READ)
                    if not selector.select(10): raise RuntimeError("Browser proof server did not start")
                    startup = json.loads(child.stdout.readline()); selector.close()
                    run("browser", [node, "scripts/browser-proof.mjs", startup["url"], str(EVIDENCE / "browser"),str(Path(tmp)/'ledger.sqlite3')], browser_env, 150)
                finally:
                    child.terminate()
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
                    child.stdout.close(); child.stderr.close()
            browser = json.loads((EVIDENCE / "browser/browser-proof.json").read_text())
            receipt["browserChecks"] = len(browser["checks"])
            receipt["physicalIPhoneTested"] = False
        else: receipt["browserChecks"] = 0
        baseline = json.loads(run("baseline", [sys.executable, "scripts/benchmark.py", "--operations", "500"], env, 90))
        receipt["localBaseline"] = baseline
        print(json.dumps({"stage": "portable-source-checkpoint"}), flush=True)
        dependency_inventory(); files = source_files()
        source_hash = sha(json.dumps({n: sha(b) for n, b in sorted(files.items())}, sort_keys=True).encode())
        for name in ["AttestedStablecoin", "PaymentRouter"]:
            files["compiled/" + name + ".json"] = (ROOT / "artifacts" / (name + ".json")).read_bytes()
        files["VERIFIED.json"] = (json.dumps({"schema": "hellabit.build-proof.v2", "sourceTreeSha256": source_hash,
            "version":receipt["version"],"productionReady":False,"externalTransactions":0},indent=2,sort_keys=True)+"\n").encode()
        archive_data = zip_bytes(files)
        if archive_data != zip_bytes(files): raise RuntimeError("Source package is not reproducible")
        member_count = inspect_archive(archive_data)
        with tempfile.TemporaryDirectory(prefix="hellabit-portable-") as tmp:
            unpacked = Path(tmp)
            with zipfile.ZipFile(io.BytesIO(archive_data)) as capsule: capsule.extractall(unpacked)
            portable_env = {**env, "PYTHONPATH": str(unpacked)}
            smoke = """from pathlib import Path
import tempfile
from hellabit.ledger import Ledger
from hellabit.rails import SandboxBank
from hellabit.readiness import local_build
assert local_build()['status']=='packaged-source-matches-proof'
with tempfile.TemporaryDirectory() as state:
 book=Ledger(Path(state)/'ledger.sqlite3')
 tenant=book.create_tenant('Portable Merchant')['tenant']
 SandboxBank(book).fund(tenant,'agent-01','CAD',100,'source-bank-1')
 quote=book.create_quote(tenant,'CAD',100,1)['quote']
 op=book.reserve(tenant,'agent-01',quote,'portable-job')['id']
 book.run_report(tenant,op)
 assert book.delivery(tenant,op)['operation']==op
 assert Ledger(book.path).reconcile(tenant)['ok']
print('portable paid journey and reopen verified')
"""
            portable = subprocess.run([sys.executable, "-c", smoke], cwd=unpacked, env=portable_env, capture_output=True, text=True, timeout=20)
            if portable.returncode: raise RuntimeError("Extracted source journey failed")
            receipt["portableSourceJourneyVerified"] = True
        artifact_version = json.loads((ROOT / 'package.json').read_text())['version']
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', artifact_version): raise RuntimeError('Invalid release version')
        if '__version__ = "' + artifact_version + '"' not in (ROOT / 'hellabit/__init__.py').read_text(): raise RuntimeError('Source version mismatch')
        archive = RELEASE / ("hellabit-gateway-" + artifact_version + '-' + source_hash[:12] + ".zip")
        if archive.exists() and archive.read_bytes() != archive_data: raise RuntimeError("Existing immutable archive differs")
        if not archive.exists(): archive.write_bytes(archive_data); archive.chmod(0o600)
        if sha(archive.read_bytes()) != sha(archive_data): raise RuntimeError("Package readback differs")
        key = PRIVATE / "source-checkpoint-ed25519.pem"
        if not key.exists():
            run("checkpoint-key", [openssl, "genpkey", "-algorithm", "ED25519", "-out", str(key)], env, 15); key.chmod(0o600)
        if key.is_symlink() or stat.S_IMODE(key.stat().st_mode) != 0o600 or key.stat().st_uid != os.getuid(): raise RuntimeError("Unsafe checkpoint key")
        public_key, signature = archive.with_suffix(".zip.pub.pem"), archive.with_suffix(".zip.sig")
        public_bytes = run("checkpoint-public", [openssl, "pkey", "-in", str(key), "-pubout"], env, 15).encode()
        if public_key.exists() and public_key.read_bytes() != public_bytes: raise RuntimeError("Immutable checkpoint public key differs")
        if not public_key.exists(): public_key.write_bytes(public_bytes)
        run("checkpoint-sign", [openssl, "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(archive), "-out", str(signature)], env, 15)
        run("checkpoint-verify", [openssl, "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public_key), "-in", str(archive), "-sigfile", str(signature)], env, 15)
        readiness = {"schema": "hellabit.readiness.v1", "localCoreVerified": True, "browserVerified": not args.skip_browser,
            "privateEvmVerified": True, "productionReady": False, "liveConnections": [], "realIssuer": None,
            "nextExternalFoundation": "named-operating-issuer-and-bank-or-reserve-relationship",
            "sourceArchive": archive.name, "sourceSha256": sha(archive_data)}
        write_json(RELEASE / "readiness.json", readiness)
        receipt.update({"ok": not args.skip_browser, "status": "local-verified" if not args.skip_browser else "verified-with-browser-limit",
            "sourceTreeSha256": source_hash, "archive": str(archive.relative_to(ROOT)), "archiveSha256": sha(archive_data),
            "archiveMembers": member_count, "archiveReproducible": True, "sourceCheckpointSignatureVerified": True,
            "checkpointPublicKeySha256": sha(public_key.read_bytes()), "runtime": {"python": sys.version.split()[0], "node": version, "sqlite": sqlite3.sqlite_version},
            "nextExternalFoundation": readiness["nextExternalFoundation"]})
    except Exception as error:
        receipt.update({"status": "failed", "error": str(error)[:350]})
    finally:
        receipt["elapsedSeconds"] = round(time.monotonic() - started, 3)
        write_json(EVIDENCE / "terminal-result.json", receipt)
        print(json.dumps(receipt, sort_keys=True), flush=True)
        lock.close()
    return 0 if receipt["status"] in ("local-verified", "verified-with-browser-limit") else 1


if __name__ == "__main__": raise SystemExit(main())
