"""The reviewed public file list is the entire source-export boundary."""
import json
from pathlib import Path,PurePosixPath

ROOT_FILES={"README.md","LICENSE","NOTICE","SECURITY.md","PUBLIC_FILES.json","package.json",
            "package-lock.json","DEPENDENCIES.json",".gitignore","requirements-api.txt","API-DEPENDENCIES.json"}
FOLDERS={"hellabit","web","sdk","contracts","tests","scripts","docs","examples","licenses",".github"}
RUNTIME={"hellabit","web","sdk","contracts"}

def source_names(root):
    root=Path(root).resolve();file=root/"PUBLIC_FILES.json"
    if file.is_symlink() or not file.is_file() or file.stat().st_size>65536:raise ValueError("public_file_list_required")
    manifest=json.loads(file.read_text())
    if not isinstance(manifest,dict) or set(manifest)!={"schema","files"} or manifest["schema"]!="hellabit.public-files.v1":
        raise ValueError("invalid_public_file_list")
    rows=manifest["files"]
    if not isinstance(rows,list) or not 3<=len(rows)<=500:raise ValueError("invalid_public_file_list")
    names=set()
    for name in rows:
        if not isinstance(name,str) or "\\" in name:raise ValueError("unsafe_public_path")
        p=PurePosixPath(name);parts=p.parts
        if (not parts or p.is_absolute() or p.as_posix()!=name or any(x in ("..",".",".private","evidence","release","node_modules","__pycache__") for x in parts)
                or any(x.casefold()=="agents.md" for x in parts) or name in names
                or not (name in ROOT_FILES or len(parts)>1 and parts[0] in FOLDERS)):
            raise ValueError("unsafe_public_path")
        current=root/name
        if (not current.is_file() or any(parent.is_symlink() for parent in [current,*current.parents] if parent!=root.parent)
                or not current.resolve().is_relative_to(root)):
            raise ValueError("public_file_missing_or_unsafe")
        names.add(name)
    if not {"PUBLIC_FILES.json","README.md","LICENSE"}<=names or len({n.casefold() for n in names})!=len(names):
        raise ValueError("invalid_public_file_list")
    # Unlisted notes are excluded. Unlisted executable product files invalidate
    # integrity, because an import could otherwise bypass the content proof.
    for folder in RUNTIME:
        for p in (root/folder).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc" and p.relative_to(root).as_posix() not in names:
                raise ValueError("unlisted_runtime_file")
    return names
