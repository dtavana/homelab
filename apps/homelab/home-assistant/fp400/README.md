# Aqara FP400 Flux-managed update

This change installs the pinned Aqara FP400 v1.2.0 integration from the Home Assistant pod's existing Flux-managed init-container path. It has not been merged or reconciled, and it has made no live Home Assistant or PVC changes.

## Proposed repository changes

- `apps/homelab/home-assistant/values.yaml` keeps the existing HACS init container and adds the FP400 installer after it. The installer uses `ghcr.io/home-assistant/home-assistant:2026.10.4`, the same image tag selected by Helm chart 0.3.89's `appVersion`. This pins only the init-container image; the Home Assistant container keeps the chart's existing appVersion-based default. Update the init-container image tag when the chart appVersion changes.
- `apps/homelab/home-assistant/kustomization.yaml` generates a ConfigMap in `hass-system` with the installer, source archive, checksum list and upstream MIT license. The existing values ConfigMap in `flux-system` remains unchanged.
- `fp400/install_fp400.py`, `source.tar.gz.b64`, `candidate-sha256sums.txt`, and `LICENSE` pin the installer payload to upstream commit `3ca4207bd1e133542f8f822c030334412a13b56d` (v1.2.0). The candidate files are checked against their pinned per-file SHA-256 values and the archive SHA-256.
- The `fp400-installer-revision` pod annotation is a digest of the script and mounted payload. Update it whenever any of those files change so a future change triggers a pod rollout.

Chart 0.3.89 renders top-level `initContainers` directly, supports `additionalVolumes`, and mounts the same `/config` PVC used by the Home Assistant container. The FP400 init container explicitly mounts that volume. The existing `setup-config` init container and HACS installer remain in the pod.

## Install and safety behavior

The init container runs before Home Assistant starts. If a prior process was interrupted after moving the live directory aside, it first attempts the backup recovery described below; on an ordinary run it proceeds as follows:

1. It checks the pinned checksum manifest. If all 15 candidate files are already present with the expected hashes, it exits without reading or unpacking the source archive and without writing to `/config`. Runtime `__pycache__/*.pyc` files are ignored for this check.
2. On the first install, it verifies the five captured baseline file hashes. Other files may exist; they are included in the backup and preserved for rollback.
3. It copies the entire current integration directory to `/config/aqara_fp400_backups/3ca4207bd1e133542f8f822c030334412a13b56d/original`, records a full-tree integrity manifest, and validates the copy before staging the candidate.
4. It verifies the full live tree has not changed, stages the 15 pinned candidate files, renames the old directory into a unique `retained/original-before-swap[-NNN]/integration` path, checks that moved tree against the full backup, then moves the candidate into place. Repeated apply/rollback cycles use new retained paths and do not overwrite earlier trees.

If the integration target is missing after an interrupted swap, either mode validates the full backup and requires a recognized retained pre-swap tree. It copies the backup to a fresh staging directory, verifies the full tree, and promotes it with an atomic no-replace rename; the retained tree is left untouched. Install then validates the candidate manifest and continues, while `restore-original` recognizes the restored full baseline and exits. Missing or corrupt backup data, absent retained evidence, or a destination that becomes occupied stops recovery without overwriting existing data. Linux promotion calls the kernel `renameat2` syscall directly, so it does not depend on a glibc-only symbol; the current eligible k3s nodes are amd64, aarch64 is also mapped, and unknown Linux architectures fail closed. The harness includes a Linux-only real syscall check for occupied and free destinations.

The five baseline hashes come from a read-only snapshot dated 2026-10-26. Before merging or reconciling this change, re-read the five live files and confirm they still match; otherwise refresh the snapshot and review the resulting diff before proceeding. A baseline mismatch fails the init container, so Home Assistant remains blocked from starting until the source state is corrected or the installer is rolled back through Flux. When installation or restoration is needed, a baseline hash mismatch, corrupt checksum manifest, invalid archive, incomplete backup, or concurrent source change stops initialization before the candidate replaces the live directory. An already-installed exact candidate intentionally skips archive validation and does not write to `/config`. The separate full backup remains authoritative if a process holding an already-open file changes the moved directory after its rename. Backups live on the same persistent `/config` volume, so they provide rollback protection rather than separate-volume disaster recovery.

## Roll back through Flux

Reverting the install PR alone does not restore files already written to the persistent PVC. To roll back:

1. In a follow-up PR, change the installer `args` in `values.yaml` from `["install"]` to `["restore-original"]`.
2. After Flux reconciles that change, the init container restores the validated full backup. Rollback is a no-op only when the entire current tree matches the validated full backup (ignoring runtime bytecode). If the five captured hashes match but any companion file is missing or differs from that backup, rollback restores the full backup.
3. Review the init-container logs and verify the restored integration. A later PR can remove the installer ConfigMap, volume, and init container; removing those resources does not change the restored integration.

Rollback refuses to replace a directory that matches neither the exact pinned candidate nor the captured baseline, and it refuses a corrupt backup. It retains the moved candidate or drifted baseline in a unique directory under `retained/`, without overwriting evidence from earlier cycles.

The local harness in `tests/test_install_fp400.py` covers first install, candidate no-op with runtime bytecode, manifest/archive corruption, baseline drift, backup failure, full rollback, modified/corrupt trees, path traversal, rollout annotation changes, interrupted-swap recovery for install and rollback, missing/corrupt backup refusal, occupied-destination races, and writes injected between hash checks and renames. It uses a synthetic installed tree and replaces the five expected hashes only inside each test. A Linux-only case exercises the actual kernel no-replace syscall; it is skipped on Windows. The values check also ensures this change does not add a top-level Home Assistant image override. The installer’s production constants remain the five captured hashes. These are filesystem simulations, not a live cluster or Home Assistant test.

At runtime, changes are limited to the Home Assistant init-container pod template and `/config/custom_components/aqara_fp400`, plus its full backup under `/config/aqara_fp400_backups`. The runbook, tests and pinned source payload are repository artifacts; the ConfigMap mounts the installer payload read-only. No helper, automation, Matter database, `.storage`, or HA registry data is changed.
