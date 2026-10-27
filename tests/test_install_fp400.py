from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

CHANGED = Path(__file__).resolve().parents[1]
SOURCE_MOUNT = CHANGED / "apps" / "homelab" / "home-assistant" / "fp400"
TEST_TEMP = CHANGED / ".tmp"
sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("install_fp400", SOURCE_MOUNT / "install_fp400.py")
installer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(installer)


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.original_baseline_hashes = installer.BASELINE_SHA256
        self.root = Path(self.temp.name) / "config"
        self.components = self.root / "custom_components"
        self.target = self.components / "aqara_fp400"
        self.components.mkdir(parents=True)
        self.target.mkdir()
        self.mount = SOURCE_MOUNT

    def tearDown(self) -> None:
        installer.BASELINE_SHA256 = self.original_baseline_hashes
        self.temp.cleanup()
        try:
            TEST_TEMP.rmdir()
        except OSError:
            pass

    def make_baseline(self, with_extra: bool = True) -> None:
        baseline_hashes = {}
        for name in ("__init__.py", "entity.py", "manifest.json", "node.py", "sensor.py"):
            data = ("synthetic captured baseline: " + name + "\n").encode("utf-8")
            (self.target / name).write_bytes(data)
            baseline_hashes[name] = hashlib.sha256(data).hexdigest()
        installer.BASELINE_SHA256 = baseline_hashes
        # The captured files are a subset of the whole v1.1 tree; preserve the
        # companion module, runtime bytecode, and extra file in the full backup.
        (self.target / "const.py").write_text("synthetic companion module\n", encoding="utf-8")
        if with_extra:
            (self.target / "extra-preserved.yaml").write_text("preserve me\n", encoding="utf-8")
            cache = self.target / "__pycache__"
            cache.mkdir()
            (cache / "node.cpython-314.pyc").write_bytes(b"old bytecode")

    def make_candidate(self, target: Path | None = None) -> None:
        expected, files = installer.read_candidate(self.mount)
        output = target or self.target
        for name, data in files.items():
            path = output.joinpath(*Path(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertTrue(installer.candidate_matches(output, expected))

    def run_install(self) -> str:
        return installer.run("install", self.root, self.mount)

    def run_rollback(self) -> str:
        return installer.run("restore-original", self.root, self.mount)

    def test_install_recovers_interrupted_original_to_missing_target_then_installs(self) -> None:
        self.make_baseline(with_extra=True)
        original = tree_bytes(self.target)
        backup_root, _, _ = installer.backup_paths(self.root)
        installer.create_or_validate_backup(self.target, self.root)
        reservation = installer.reserve_retained_directory(backup_root, "original-before-swap")
        held = reservation / "integration"
        installer.rename_noreplace(self.target, held)  # Simulate interruption immediately after live -> held.
        self.assertFalse(self.target.exists())

        self.run_install()

        expected = installer.expected_files(self.mount / "candidate-sha256sums.txt")
        self.assertTrue(installer.candidate_matches(self.target, expected))
        self.assertEqual(tree_bytes(held), original)
        self.assertEqual(tree_bytes(backup_root / "original"), original)

    def test_rollback_recovers_interrupted_candidate_to_missing_target(self) -> None:
        self.make_baseline(with_extra=True)
        self.run_install()
        backup_root, original, _ = installer.backup_paths(self.root)
        candidate = tree_bytes(self.target)
        reservation = installer.reserve_retained_directory(backup_root, "candidate-before-rollback")
        held = reservation / "integration"
        installer.rename_noreplace(self.target, held)  # Simulate interruption after candidate -> held.
        self.assertFalse(self.target.exists())

        result = self.run_rollback()

        self.assertIn("already matches the full captured baseline backup", result)
        self.assertEqual(tree_bytes(self.target), tree_bytes(original))
        self.assertEqual(tree_bytes(held), candidate)

    def test_missing_target_without_valid_backup_fails_closed(self) -> None:
        self.make_baseline()
        backup_root, _, _ = installer.backup_paths(self.root)
        backup_root.mkdir(parents=True)
        reservation = installer.reserve_retained_directory(backup_root, "original-before-swap")
        held = reservation / "integration"
        installer.rename_noreplace(self.target, held)
        held_before = tree_bytes(held)

        with self.assertRaisesRegex(installer.InstallError, "backup integrity manifest is missing|pinned original backup is missing"):
            self.run_install()

        self.assertFalse(self.target.exists())
        self.assertEqual(tree_bytes(held), held_before)

    def test_missing_target_with_corrupt_backup_fails_closed_and_preserves_hold(self) -> None:
        self.make_baseline()
        self.run_install()
        backup_root, original, _ = installer.backup_paths(self.root)
        (original / "const.py").write_text("corrupt backup\n", encoding="utf-8")
        reservation = installer.reserve_retained_directory(backup_root, "candidate-before-rollback")
        held = reservation / "integration"
        installer.rename_noreplace(self.target, held)
        held_before = tree_bytes(held)

        with self.assertRaisesRegex(installer.InstallError, "full original backup differs"):
            self.run_rollback()

        self.assertFalse(self.target.exists())
        self.assertEqual(tree_bytes(held), held_before)

    def test_recovery_destination_race_is_no_replace_and_preserves_competitor(self) -> None:
        self.make_baseline(with_extra=True)
        backup_root, _, _ = installer.backup_paths(self.root)
        installer.create_or_validate_backup(self.target, self.root)
        reservation = installer.reserve_retained_directory(backup_root, "original-before-swap")
        held = reservation / "integration"
        installer.rename_noreplace(self.target, held)
        held_before = tree_bytes(held)
        real_rename = installer.rename_noreplace

        def race_destination(src: Path, dst: Path) -> None:
            if Path(src).name == "aqara_fp400" and Path(src).parent.name.startswith(".aqara_fp400.recovery-"):
                dst.mkdir()
                (dst / "concurrent-owner.txt").write_text("do not replace\n", encoding="utf-8")
            real_rename(src, dst)

        with mock.patch.object(installer, "rename_noreplace", side_effect=race_destination):
            with self.assertRaises(FileExistsError):
                self.run_install()

        self.assertEqual((self.target / "concurrent-owner.txt").read_text(encoding="utf-8"), "do not replace\n")
        self.assertEqual(tree_bytes(held), held_before)
        self.assertFalse(list(self.components.glob(".aqara_fp400.recovery-*")))

    def test_missing_target_with_bad_manifest_recovers_original_before_install_fails(self) -> None:
        self.make_baseline(with_extra=True)
        original = tree_bytes(self.target)
        backup_root, _, _ = installer.backup_paths(self.root)
        installer.create_or_validate_backup(self.target, self.root)
        reservation = installer.reserve_retained_directory(backup_root, "original-before-swap")
        held = reservation / "integration"
        installer.rename_noreplace(self.target, held)
        checksum_path = self.mount / "candidate-sha256sums.txt"
        manifest = checksum_path.read_bytes()
        try:
            checksum_path.write_bytes(manifest + b"# injected corruption\n")
            with self.assertRaisesRegex(installer.InstallError, "manifest is not the pinned"):
                self.run_install()
        finally:
            checksum_path.write_bytes(manifest)

        # Recovery is independent of the candidate bundle, while install still
        # fails closed on the bad manifest and never proceeds to the swap.
        self.assertEqual(tree_bytes(self.target), original)
        self.assertEqual(tree_bytes(held), original)

    def test_linux_noreplace_syscall_numbers_are_explicit_and_unknown_arch_fails_closed(self) -> None:
        self.assertEqual(installer.linux_renameat2_syscall_number("x86_64"), 316)
        self.assertEqual(installer.linux_renameat2_syscall_number("aarch64"), 276)
        with self.assertRaisesRegex(installer.InstallError, "unsupported on Linux architecture"):
            installer.linux_renameat2_syscall_number("mips64")

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires a Linux kernel")
    def test_linux_noreplace_rename_syscall_promotes_and_refuses_overwrite(self) -> None:
        root = Path(tempfile.mkdtemp(dir=TEST_TEMP))
        try:
            source = root / "source"
            source.mkdir()
            (source / "data").write_text("source\n", encoding="utf-8")
            destination = root / "destination"
            destination.mkdir()
            (destination / "existing").write_text("keep\n", encoding="utf-8")
            real_exists = Path.exists
            hide_destination_once = True

            def hide_precheck_for_destination(path: Path) -> bool:
                nonlocal hide_destination_once
                if path == destination and hide_destination_once:
                    hide_destination_once = False
                    return False
                return real_exists(path)

            # Simulate the directory appearing after the user-space check; the
            # occupied destination remains real so the kernel must return EEXIST.
            with mock.patch.object(Path, "exists", autospec=True, side_effect=hide_precheck_for_destination):
                with self.assertRaises(FileExistsError):
                    installer.rename_noreplace(source, destination)
            self.assertFalse(hide_destination_once)
            self.assertTrue(source.is_dir())
            self.assertEqual((destination / "existing").read_text(encoding="utf-8"), "keep\n")
            (destination / "existing").unlink()
            destination.rmdir()
            installer.rename_noreplace(source, destination)
            self.assertFalse(source.exists())
            self.assertEqual((destination / "data").read_text(encoding="utf-8"), "source\n")
        finally:
            shutil.rmtree(root)

    def test_values_do_not_pin_the_home_assistant_image(self) -> None:
        import re
        values = (CHANGED / "apps" / "homelab" / "home-assistant" / "values.yaml").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?m)^image:\s*$", values))
        self.assertIn("image: ghcr.io/home-assistant/home-assistant:2026.10.4", values)

    def test_pod_rollout_annotation_matches_installer_payload(self) -> None:
        import re
        payload = SOURCE_MOUNT
        digest = hashlib.sha256()
        for name in ("install_fp400.py", "source.tar.gz.b64", "candidate-sha256sums.txt", "LICENSE"):
            digest.update(name.encode("ascii") + b"\0" + (payload / name).read_bytes() + b"\0")
        values = (CHANGED / "apps" / "homelab" / "home-assistant" / "values.yaml").read_text(encoding="utf-8")
        match = re.search(r'fp400-installer-revision: "sha256:([0-9a-f]{64})"', values)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), digest.hexdigest())

    def test_first_install_backs_up_full_tree_and_swaps_candidate(self) -> None:
        self.make_baseline(with_extra=True)
        before = tree_bytes(self.target)
        result = self.run_install()
        backup_root, original, manifest_path = installer.backup_paths(self.root)
        self.assertIn(installer.COMMIT, result)
        self.assertEqual(tree_bytes(original), before)
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["tree"],
                         installer.digest_tree(original))
        self.assertEqual(tree_bytes(backup_root / "retained" / "original-before-swap" / "integration"), before)
        self.assertEqual(len(installer.digest_tree(self.target)), 15)
        self.assertFalse((self.target / "extra-preserved.yaml").exists())

    def test_candidate_is_noop_with_runtime_cache_and_does_not_read_bundle(self) -> None:
        self.make_candidate()
        cache = self.target / "__pycache__"
        cache.mkdir()
        (cache / "sensor.cpython-314.pyc").write_bytes(b"runtime cache")
        # A known candidate does not need to reread or unpack the source bundle.
        archive = self.mount / "source.tar.gz.b64"
        original = archive.read_bytes()
        try:
            archive.write_bytes(b"not-an-archive\n")
            before = tree_bytes(self.root)
            result = self.run_install()
            self.assertIn("no config files changed", result)
            self.assertEqual(tree_bytes(self.root), before)
            self.assertFalse((self.root / "aqara_fp400_backups").exists())
        finally:
            archive.write_bytes(original)

    def test_pinned_manifest_is_checked_before_noop(self) -> None:
        self.make_candidate()
        checksum_path = self.mount / "candidate-sha256sums.txt"
        original = checksum_path.read_bytes()
        try:
            checksum_path.write_bytes(original + b"# altered\n")
            before = tree_bytes(self.root)
            with self.assertRaisesRegex(installer.InstallError, "manifest is not the pinned"):
                self.run_install()
            self.assertEqual(tree_bytes(self.root), before)
        finally:
            checksum_path.write_bytes(original)

    def test_baseline_drift_fails_closed_without_replacement(self) -> None:
        self.make_baseline()
        (self.target / "node.py").write_bytes((self.target / "node.py").read_bytes() + b"\nlocal edit\n")
        before = tree_bytes(self.root)
        with self.assertRaisesRegex(installer.InstallError, "captured v1.1.0 baseline"):
            self.run_install()
        self.assertEqual(tree_bytes(self.root), before)
        self.assertFalse((self.root / "aqara_fp400_backups").exists())

    def test_bad_archive_hash_fails_before_backup_or_live_change(self) -> None:
        self.make_baseline()
        archive = self.mount / "source.tar.gz.b64"
        original = archive.read_bytes()
        try:
            archive.write_bytes(b"ZmFrZQ==\n")
            before = tree_bytes(self.root)
            with self.assertRaisesRegex(installer.InstallError, "archive checksum"):
                self.run_install()
            self.assertEqual(tree_bytes(self.root), before)
            self.assertFalse((self.root / "aqara_fp400_backups").exists())
        finally:
            archive.write_bytes(original)

    def test_backup_failure_stops_before_live_swap(self) -> None:
        self.make_baseline()
        before = tree_bytes(self.target)
        with mock.patch.object(installer.shutil, "copytree", side_effect=OSError("injected copy failure")):
            with self.assertRaisesRegex(OSError, "injected copy failure"):
                self.run_install()
        self.assertEqual(tree_bytes(self.target), before)
        backup_root, _, _ = installer.backup_paths(self.root)
        self.assertFalse(backup_root.exists())
        self.assertFalse(list(self.components.glob(".aqara_fp400.stage-*")))

    def test_rollback_restores_full_backup_and_is_idempotent(self) -> None:
        self.make_baseline()
        original = tree_bytes(self.target)
        self.run_install()
        cache = self.target / "__pycache__"
        cache.mkdir()
        (cache / "sensor.cpython-314.pyc").write_bytes(b"runtime cache")
        result = self.run_rollback()
        backup_root, backup, _ = installer.backup_paths(self.root)
        self.assertIn("restored captured v1.1.0", result)
        self.assertEqual(tree_bytes(self.target), original)
        self.assertEqual(tree_bytes(self.target), tree_bytes(backup))
        self.assertTrue((backup_root / "retained" / "candidate-before-rollback" / "integration").is_dir())
        before = tree_bytes(self.root)
        self.assertIn("already matches", self.run_rollback())
        self.assertEqual(tree_bytes(self.root), before)

    def test_rollback_restores_backup_when_five_baseline_files_match_but_companion_drifts(self) -> None:
        self.make_baseline(with_extra=True)
        self.run_install()
        backup_root, original, _ = installer.backup_paths(self.root)
        copied = Path(self.temp.name) / "baseline-copy"
        shutil.copytree(original, copied, symlinks=True)
        shutil.rmtree(self.target)
        shutil.copytree(copied, self.target, symlinks=True)
        (self.target / "const.py").write_text("companion drift\n", encoding="utf-8")
        self.assertTrue(installer.baseline_matches(self.target))
        changed_tree = tree_bytes(self.target)

        result = self.run_rollback()

        self.assertIn("restored captured v1.1.0", result)
        self.assertEqual(tree_bytes(self.target), tree_bytes(original))
        self.assertEqual(
            tree_bytes(backup_root / "retained" / "baseline-before-restore" / "integration"),
            changed_tree,
        )

    def test_install_rollback_install_keeps_unique_prior_trees(self) -> None:
        self.make_baseline(with_extra=True)
        original_tree = tree_bytes(self.target)
        self.run_install()
        backup_root, original, _ = installer.backup_paths(self.root)
        self.run_rollback()
        self.run_install()
        first_hold = backup_root / "retained" / "original-before-swap" / "integration"
        second_hold = backup_root / "retained" / "original-before-swap-001" / "integration"
        candidate_hold = backup_root / "retained" / "candidate-before-rollback" / "integration"
        self.assertEqual(tree_bytes(original), original_tree)
        self.assertEqual(tree_bytes(first_hold), original_tree)
        self.assertEqual(tree_bytes(second_hold), original_tree)
        self.assertTrue(installer.candidate_matches(candidate_hold,
                         installer.expected_files(self.mount / "candidate-sha256sums.txt")))
        self.assertTrue(installer.candidate_matches(self.target,
                         installer.expected_files(self.mount / "candidate-sha256sums.txt")))

    def test_rollback_refuses_corrupt_backup(self) -> None:
        self.make_baseline()
        self.run_install()
        backup_root, backup, _ = installer.backup_paths(self.root)
        (backup / "node.py").write_bytes((backup / "node.py").read_bytes() + b"corrupt")
        before = tree_bytes(self.target)
        with self.assertRaisesRegex(installer.InstallError, "differs from its saved integrity manifest"):
            self.run_rollback()
        self.assertEqual(tree_bytes(self.target), before)
        self.assertTrue((backup_root / "manifest.json").is_file())

    def test_rollback_refuses_modified_candidate(self) -> None:
        self.make_baseline()
        self.run_install()
        (self.target / "node.py").write_bytes((self.target / "node.py").read_bytes() + b"local edit")
        before = tree_bytes(self.target)
        with self.assertRaisesRegex(installer.InstallError, "neither the captured baseline nor the exact pinned candidate"):
            self.run_rollback()
        self.assertEqual(tree_bytes(self.target), before)

    def test_archive_path_traversal_is_rejected(self) -> None:
        self.make_baseline()
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as tf:
            data = b"outside"
            member = tarfile.TarInfo("custom_components/aqara_fp400/../../escape.py")
            member.size = len(data)
            tf.addfile(member, io.BytesIO(data))
        encoded_path = self.mount / "source.tar.gz.b64"
        original = encoded_path.read_bytes()
        try:
            encoded_path.write_text(base64.b64encode(raw.getvalue()).decode("ascii") + "\n", encoding="ascii")
            with mock.patch.object(installer, "ARCHIVE_SHA256", hashlib.sha256(raw.getvalue()).hexdigest()):
                before = tree_bytes(self.root)
                with self.assertRaisesRegex(installer.InstallError, "unsafe path"):
                    self.run_install()
                self.assertEqual(tree_bytes(self.root), before)
                self.assertFalse((self.root / "aqara_fp400_backups").exists())
        finally:
            encoded_path.write_bytes(original)

    def test_concurrent_edit_after_original_rename_is_preserved_and_aborts(self) -> None:
        self.make_baseline()
        backup_original_rename = installer.rename_noreplace

        def racing_rename(src, dst):
            result = backup_original_rename(src, dst)
            if Path(src) == self.target and Path(dst).parent.name.startswith("original-before-swap"):
                (Path(dst) / "node.py").write_bytes((Path(dst) / "node.py").read_bytes() + b"racer")
            return result

        with mock.patch.object(installer, "rename_noreplace", side_effect=racing_rename):
            with self.assertRaises(installer.InstallError):
                self.run_install()
        self.assertTrue(installer.baseline_matches(self.target) is False)
        self.assertFalse(installer.candidate_matches(
            self.target, installer.expected_files(self.mount / "candidate-sha256sums.txt")))
        backup_root, original, _ = installer.backup_paths(self.root)
        self.assertTrue(installer.baseline_matches(original))
        self.assertTrue((backup_root / "manifest.json").is_file())
        self.assertFalse(list(self.components.glob(".aqara_fp400.stage-*")))

    def test_concurrent_edit_after_candidate_rename_is_preserved_and_aborts_rollback(self) -> None:
        self.make_baseline()
        self.run_install()
        backup_original_rename = installer.rename_noreplace

        def racing_rename(src, dst):
            result = backup_original_rename(src, dst)
            if Path(src) == self.target and Path(dst).parent.name.startswith("candidate-before-rollback"):
                (Path(dst) / "sensor.py").write_bytes((Path(dst) / "sensor.py").read_bytes() + b"racer")
            return result

        with mock.patch.object(installer, "rename_noreplace", side_effect=racing_rename):
            with self.assertRaises(installer.InstallError):
                self.run_rollback()
        self.assertTrue(self.target.is_dir())
        self.assertFalse(installer.baseline_matches(self.target))
        backup_root, original, _ = installer.backup_paths(self.root)
        self.assertTrue(installer.baseline_matches(original))
        self.assertFalse((backup_root / "retained" / "candidate-before-rollback" / "integration").exists())
        self.assertIn(b"racer", (self.target / "sensor.py").read_bytes())
        self.assertFalse(list(self.components.glob(".aqara_fp400.stage-*")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
