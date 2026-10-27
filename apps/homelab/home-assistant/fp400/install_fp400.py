"""Install or restore the pinned Aqara FP400 custom integration at HA startup."""
from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import io
import json
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

COMMIT = "3ca4207bd1e133542f8f822c030334412a13b56d"
ARCHIVE_SHA256 = "d7a0388eef48e60641745ac62b95c06fc03ed50b0a7f0d3c70b06c201dd497ca"
MANIFEST_SHA256 = "5904f5ae4438217b63c82edf1d874d6e791a1bcc3f7a5b5786f0f538d90058c8"
SOURCE_PREFIX = "custom_components/aqara_fp400/"
BASELINE_SHA256 = {
    "__init__.py": "d3ed499f571122b5812af7e6ec5619a144c562caf247311f061879b7acd51db3",
    "entity.py": "eb809c255a53a99cc91b414ba8e0355a389c806f1f9fa1441c5a0ff452367fae",
    "manifest.json": "4d0b94387a684e242e008b6b5ca0832383664967231b377fe76b949ba636d2e6",
    "node.py": "39f677f61347bb9e2491776871299589b07d07c90181fbffcd65ed29e878103b",
    "sensor.py": "6218457110728ac6a75007fdbe99bedcbac2449e251cb55f05675a8604c14b55",
}
BACKUP_NAME = COMMIT


class InstallError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InstallError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def linux_renameat2_syscall_number(machine: str) -> int:
    numbers = {"x86_64": 316, "amd64": 316, "aarch64": 276, "arm64": 276}
    try:
        return numbers[machine.lower()]
    except KeyError as err:
        raise InstallError(f"atomic no-replace rename is unsupported on Linux architecture {machine}") from err


def rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename without replacing an occupied destination."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, "destination already exists", str(destination))
    if os.name == "nt":
        # Windows os.rename itself fails if the destination appeared after the check.
        os.rename(source, destination)
        return
    if sys.platform.startswith("linux"):
        # Use the kernel syscall directly; musl may not export a renameat2 libc symbol.
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
        source_bytes = os.fsencode(source)
        destination_bytes = os.fsencode(destination)
        result = syscall(
            ctypes.c_long(linux_renameat2_syscall_number(platform.machine())),
            ctypes.c_long(-100), ctypes.c_char_p(source_bytes),
            ctypes.c_long(-100), ctypes.c_char_p(destination_bytes), ctypes.c_long(1),
        )
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
        return
    raise InstallError(f"atomic no-replace rename is unsupported on {sys.platform}")


def digest_tree(root: Path) -> dict[str, str]:
    require(root.is_dir() and not root.is_symlink(), f"not a real directory: {root}")
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[rel] = "symlink:" + os.readlink(path)
        elif path.is_file():
            result[rel] = "sha256:" + sha256(path.read_bytes())
        elif not path.is_dir():
            raise InstallError(f"unsupported file type in integration tree: {rel}")
    return result


def without_runtime_cache(tree: dict[str, str]) -> dict[str, str]:
    return {
        path: value
        for path, value in tree.items()
        if not ("__pycache__" in PurePosixPath(path).parts and path.endswith(".pyc"))
    }


def expected_files(checksums_path: Path) -> dict[str, str]:
    manifest_bytes = checksums_path.read_bytes()
    require(sha256(manifest_bytes) == MANIFEST_SHA256, "candidate checksum manifest is not the pinned release manifest")
    result: dict[str, str] = {}
    for line in manifest_bytes.decode("ascii").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip()
        parsed = PurePosixPath(name)
        require(len(digest) == 64 and name not in result, "candidate checksum list is malformed")
        require(not parsed.is_absolute() and ".." not in parsed.parts and "\\" not in name,
                "candidate checksum path is invalid")
        result[name] = "sha256:" + digest
    require(len(result) == 15, "candidate checksum list does not contain the pinned 15 files")
    return result


def candidate_matches(path: Path, expected: dict[str, str]) -> bool:
    try:
        return without_runtime_cache(digest_tree(path)) == expected
    except (OSError, InstallError):
        return False


def baseline_matches(path: Path) -> bool:
    try:
        actual = without_runtime_cache(digest_tree(path))
    except (OSError, InstallError):
        return False
    expected = {name: "sha256:" + digest for name, digest in BASELINE_SHA256.items()}
    return all(actual.get(name) == digest for name, digest in expected.items())


def check_source_layout(custom_components: Path) -> Path:
    require(custom_components.is_dir() and not custom_components.is_symlink(),
            f"custom_components is missing or is not a directory: {custom_components}")
    target = custom_components / "aqara_fp400"
    require(target.is_dir() and not target.is_symlink(), f"installed integration is missing: {target}")
    return target


def read_candidate(source_mount: Path) -> tuple[dict[str, str], dict[str, bytes]]:
    checksums = expected_files(source_mount / "candidate-sha256sums.txt")
    encoded = (source_mount / "source.tar.gz.b64").read_bytes().strip()
    try:
        archive = base64.b64decode(encoded, validate=True)
    except Exception as err:
        raise InstallError(f"candidate archive is not valid base64: {err}") from err
    require(sha256(archive) == ARCHIVE_SHA256, "candidate archive checksum does not match the pinned release")
    require(len(archive) <= 256 * 1024, "candidate archive exceeds the expected size limit")

    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            for member in tf.getmembers():
                require(member.isfile(), f"candidate archive contains a non-file entry: {member.name}")
                parsed = PurePosixPath(member.name)
                require(not parsed.is_absolute() and ".." not in parsed.parts,
                        f"candidate archive contains an unsafe path: {member.name}")
                require(member.name.startswith(SOURCE_PREFIX),
                        f"candidate archive path is outside the integration: {member.name}")
                rel = member.name[len(SOURCE_PREFIX):]
                require(rel and rel not in files, f"candidate archive contains a duplicate or empty path: {member.name}")
                require(member.size <= 256 * 1024, f"candidate file is too large: {rel}")
                stream = tf.extractfile(member)
                require(stream is not None, f"candidate file cannot be read: {rel}")
                data = stream.read()
                require(len(data) == member.size, f"candidate file size changed while reading: {rel}")
                files[rel] = data
    except (tarfile.TarError, OSError) as err:
        raise InstallError(f"candidate archive could not be read: {err}") from err

    require(set(files) == set(checksums), "candidate archive file list differs from its pinned manifest")
    for name, data in files.items():
        require("sha256:" + sha256(data) == checksums[name], f"candidate file hash mismatch: {name}")
    return checksums, files


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def backup_paths(config_root: Path) -> tuple[Path, Path, Path]:
    backup_root = config_root / "aqara_fp400_backups" / BACKUP_NAME
    return backup_root, backup_root / "original", backup_root / "manifest.json"


def verify_backup(config_root: Path) -> dict[str, str]:
    backup_root, original, manifest_path = backup_paths(config_root)
    require(backup_root.is_dir() and not backup_root.is_symlink(),
            f"pinned original backup is missing: {backup_root}")
    require(manifest_path.is_file(), f"backup integrity manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise InstallError(f"backup integrity manifest cannot be read: {err}") from err
    require(manifest.get("commit") == COMMIT and isinstance(manifest.get("tree"), dict),
            "backup integrity manifest is malformed or belongs to another revision")
    expected = manifest["tree"]
    actual = digest_tree(original)
    require(actual == expected, "full original backup differs from its saved integrity manifest")
    require(baseline_matches(original), "full original backup does not match the captured installed baseline")
    return expected


def has_retained_tree(backup_root: Path) -> bool:
    retained_root = backup_root / "retained"
    require(retained_root.is_dir() and not retained_root.is_symlink(),
            "cannot recover a missing integration without a retained pre-swap tree")
    labels = ("original-before-swap", "candidate-before-rollback", "baseline-before-restore")
    for reservation in sorted(retained_root.iterdir()):
        if not any(reservation.name == label or reservation.name.startswith(label + "-") for label in labels):
            continue
        require(reservation.is_dir() and not reservation.is_symlink(),
                f"retained recovery path is not a directory: {reservation}")
        held = reservation / "integration"
        if held.is_symlink():
            raise InstallError(f"retained recovery tree is a symlink: {held}")
        if held.is_dir():
            return True
    return False


def recover_missing_target(config_root: Path, custom_components: Path) -> bool:
    """Restore the verified baseline if a prior init was interrupted during a swap."""
    require(custom_components.is_dir() and not custom_components.is_symlink(),
            f"custom_components is missing or is not a directory: {custom_components}")
    live = custom_components / "aqara_fp400"
    if live.exists() or live.is_symlink():
        return False

    backup_root, original, _ = backup_paths(config_root)
    backup_tree = verify_backup(config_root)
    require(has_retained_tree(backup_root),
            "integration target is missing and no retained pre-swap tree was found")

    stage_root = Path(tempfile.mkdtemp(prefix=".aqara_fp400.recovery-", dir=custom_components))
    staged = stage_root / "aqara_fp400"
    try:
        shutil.copytree(original, staged, symlinks=True)
        require(digest_tree(staged) == backup_tree, "missing-target recovery copy failed full-tree validation")
        require(not live.exists() and not live.is_symlink(),
                f"recovery destination became occupied; preserved it at {live}")
        rename_noreplace(staged, live)
        require(digest_tree(live) == backup_tree,
                f"recovered integration failed full-tree validation: {live}")
        return True
    finally:
        if stage_root.exists():
            require(stage_root.parent == custom_components and stage_root.name.startswith(".aqara_fp400.recovery-"),
                    "refusing to clean recovery staging outside custom_components")
            shutil.rmtree(stage_root)


def create_or_validate_backup(live: Path, config_root: Path) -> dict[str, str]:
    backup_root, original, manifest_path = backup_paths(config_root)
    if backup_root.exists() or backup_root.is_symlink():
        expected = verify_backup(config_root)
        return expected

    backup_parent = backup_root.parent
    backup_parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".aqara-fp400-backup-", dir=backup_parent))
    try:
        before = digest_tree(live)
        require(baseline_matches(live), "installed baseline changed before backup")
        shutil.copytree(live, temporary / "original", symlinks=True)
        copied = digest_tree(temporary / "original")
        require(copied == before, "full integration backup failed readback validation")
        require(baseline_matches(temporary / "original"), "backup does not match the captured baseline")
        require(digest_tree(live) == before, "installed integration changed while backup was copied")
        (temporary / "manifest.json").write_text(
            json.dumps({"commit": COMMIT, "tree": copied}, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        rename_noreplace(temporary, backup_root)
    except Exception:
        if temporary.exists() and temporary.parent == backup_parent:
            shutil.rmtree(temporary)
        raise
    return verify_backup(config_root)


def new_stage(custom_components: Path, files: dict[str, bytes]) -> tuple[Path, Path]:
    stage_root = Path(tempfile.mkdtemp(prefix=".aqara_fp400.stage-", dir=custom_components))
    component = stage_root / "aqara_fp400"
    try:
        for name, data in files.items():
            destination = component.joinpath(*PurePosixPath(name).parts)
            require(destination.resolve().is_relative_to(component.resolve()),
                    f"candidate file escapes staging directory: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        return stage_root, component
    except Exception:
        if stage_root.exists() and stage_root.parent == custom_components:
            shutil.rmtree(stage_root)
        raise


def remove_empty_stage(stage_root: Path, custom_components: Path) -> None:
    if stage_root.exists():
        require(stage_root.parent == custom_components and stage_root.name.startswith(".aqara_fp400.stage-"),
                "refusing to clean a staging path outside custom_components")
        shutil.rmtree(stage_root)


def reserve_retained_directory(backup_root: Path, label: str) -> Path:
    retained_root = backup_root / "retained"
    require(not retained_root.is_symlink(), "retained-tree root must not be a symlink")
    retained_root.mkdir(parents=True, exist_ok=True)
    for index in range(1000):
        suffix = "" if index == 0 else f"-{index:03d}"
        reservation = retained_root / f"{label}{suffix}"
        try:
            reservation.mkdir()
            return reservation
        except FileExistsError:
            continue
    raise InstallError(f"could not reserve a unique retained-tree path for {label}")


def restore_hold_if_free(live: Path, hold: Path, expected: dict[str, str], context: str) -> None:
    if live.exists() or live.is_symlink():
        raise InstallError(f"{context}; preserved original at {hold} because the live path is occupied")
    try:
        rename_noreplace(hold, live)
    except Exception as err:
        raise InstallError(f"{context}; preserved original at {hold}; restore failed: {err}") from err
    require(without_runtime_cache(digest_tree(live)) == without_runtime_cache(expected),
            f"{context}; original was restored but readback differs at {live}")


def install(config_root: Path, source_mount: Path) -> str:
    custom_components = config_root / "custom_components"
    # Repair an interrupted prior swap from the independently validated full
    # backup before inspecting the candidate payload. If the payload is bad,
    # the original integration is still restored before startup fails closed.
    recover_missing_target(config_root, custom_components)
    checksum_path = source_mount / "candidate-sha256sums.txt"
    expected = expected_files(checksum_path)
    live = check_source_layout(custom_components)

    # This check deliberately precedes reading the archive and creating any PVC files.
    if candidate_matches(live, expected):
        return f"{live} already matches pinned {COMMIT}; no config files changed"

    require(baseline_matches(live),
            "installed integration differs from the captured v1.1.0 baseline; refusing automatic replacement")
    files_expected, files = read_candidate(source_mount)
    require(files_expected == expected, "candidate checksums changed while loading the bundle")

    backup_root, _, _ = backup_paths(config_root)
    backup_tree = create_or_validate_backup(live, config_root)
    require(without_runtime_cache(digest_tree(live)) == without_runtime_cache(backup_tree),
            "installed integration changed after its full backup")

    stage_root, staged = new_stage(custom_components, files)
    try:
        require(candidate_matches(staged, expected), "staged candidate failed checksum verification")
        require(baseline_matches(live), "installed baseline changed while staging the candidate")
        require(without_runtime_cache(digest_tree(live)) == without_runtime_cache(backup_tree),
                "installed integration changed immediately before replacement")

        reservation = reserve_retained_directory(backup_root, "original-before-swap")
        held = reservation / "integration"
        held_manifest = reservation / "manifest.json"
        require(not held.exists() and not held.is_symlink(), "reserved original path is unexpectedly occupied")
        rename_noreplace(live, held)
        if without_runtime_cache(digest_tree(held)) != without_runtime_cache(backup_tree):
            restore_hold_if_free(live, held, backup_tree, "original changed during replacement rename; install aborted")
            raise InstallError("original changed during replacement rename; install aborted")
        try:
            write_json(held_manifest, {"commit": COMMIT, "tree": digest_tree(held)})
            rename_noreplace(staged, live)
        except Exception as err:
            restore_hold_if_free(live, held, backup_tree,
                                 f"could not complete candidate rename ({err}); install aborted")
            raise
        require(candidate_matches(live, expected),
                f"candidate readback failed; preserved full backup at {backup_root} and moved original at {held}")
        return f"installed pinned {COMMIT}; full backup: {backup_root}"
    finally:
        remove_empty_stage(stage_root, custom_components)


def restore_original(config_root: Path, source_mount: Path) -> str:
    custom_components = config_root / "custom_components"
    recover_missing_target(config_root, custom_components)
    live = check_source_layout(custom_components)
    backup_root, original, _ = backup_paths(config_root)
    backup_tree = verify_backup(config_root)
    current_before = digest_tree(live)
    is_baseline = baseline_matches(live)
    if is_baseline and without_runtime_cache(current_before) == without_runtime_cache(backup_tree):
        return f"{live} already matches the full captured baseline backup; no config files changed"
    expected_candidate = expected_files(source_mount / "candidate-sha256sums.txt")
    is_candidate = candidate_matches(live, expected_candidate)
    require(is_candidate or is_baseline,
            "current integration is neither the captured baseline nor the exact pinned candidate; refusing rollback")

    stage_root = Path(tempfile.mkdtemp(prefix=".aqara_fp400.stage-", dir=custom_components))
    staged = stage_root / "aqara_fp400"
    try:
        shutil.copytree(original, staged, symlinks=True)
        require(digest_tree(staged) == backup_tree, "rollback staging copy failed full-tree validation")
        require(without_runtime_cache(digest_tree(live)) == without_runtime_cache(current_before),
                "current integration changed while staging rollback")

        label = "candidate-before-rollback" if is_candidate else "baseline-before-restore"
        reservation = reserve_retained_directory(backup_root, label)
        held = reservation / "integration"
        held_manifest = reservation / "manifest.json"
        require(not held.exists() and not held.is_symlink(), "reserved rollback path is unexpectedly occupied")
        rename_noreplace(live, held)
        if without_runtime_cache(digest_tree(held)) != without_runtime_cache(current_before):
            restore_hold_if_free(live, held, current_before, "integration changed during rollback rename; rollback aborted")
            raise InstallError("integration changed during rollback rename; rollback aborted")
        try:
            write_json(held_manifest, {"commit": COMMIT, "tree": digest_tree(held)})
            rename_noreplace(staged, live)
        except Exception as err:
            restore_hold_if_free(live, held, current_before,
                                 f"could not complete restore rename ({err}); rollback aborted")
            raise
        require(digest_tree(live) == backup_tree,
                f"restored original failed full-tree validation; prior integration preserved at {held}")
        return f"restored captured v1.1.0 integration from {original}"
    finally:
        remove_empty_stage(stage_root, custom_components)


def run(mode: str, config_root: Path = Path("/config"), source_mount: Path = Path("/run/fp400-installer")) -> str:
    config_root = config_root.resolve()
    if mode == "install":
        return install(config_root, source_mount)
    if mode == "restore-original":
        return restore_original(config_root, source_mount)
    raise InstallError("mode must be 'install' or 'restore-original'")


if __name__ == "__main__":
    try:
        operation = sys.argv[1] if len(sys.argv) > 1 else "install"
        print(run(operation))
    except Exception as err:
        print(f"aqara-fp400-init: {type(err).__name__}: {err}", file=sys.stderr)
        raise SystemExit(1)
