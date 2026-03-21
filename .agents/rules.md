# Homelab Repository Rules & Conventions

When generating or modifying Kubernetes manifests and Flux GitOps resources in this repository, strictly adhere to the following rules based on the repository's established patterns:

### Directory Structure & Layout
1. **Base vs. Environment:** 
   - Define all raw resources (e.g., `helmrelease.yaml`, `namespace.yaml`, `deployment.yaml`) in `base/<app>/`.
   - Provide environment-specific overrides, patches, and `*.sops.yaml` secrets in `homelab/<app>/` (or the respective environment folder), referencing the base Kustomization.
2. **Top-Level Includes:**
   - Link any new applications or infrastructure components by appending them to the respective `apps/homelab/kustomization.yaml` or `infrastructure/homelab/kustomization.yaml`.

### Namespaces
3. **Namespace Naming Convention:** All infrastructure components must be placed in their own dedicated namespace following the `<name>-system` pattern (e.g., `mqtt-broker-system`, `nginx-system`). 
4. **Declarative Namespace Creation:** Do **NOT** use `createNamespace: true` inside `HelmRelease` configurations. Instead, you must manually create a standard `namespace.yaml` file declaring the namespace, and include it in the application's base `kustomization.yaml`.
5. **App Placement:** Unless otherwise specified, Home Assistant apps and integrations (e.g., govee2mqtt, matter-server) belong in the `hass-system` namespace.

### HelmReleases
6. **API Version:** Always use `apiVersion: helm.toolkit.fluxcd.io/v2`.
7. **Metadata:** Set `metadata.namespace: flux-system` for the HelmRelease resource itself, but explicitly deploy the intended application into its proper target namespace using `spec.targetNamespace: <name>-system`.
8. **CRDs:** Always include standard CRD behavior in the `spec`:
   ```yaml
   install:
     crds: Create
   upgrade:
     crds: CreateReplace
   ```

### Secrets & Encryption (SOPS)
9. **SOPS Naming:** Any secret storing sensitive data must be named matching the `*.sops.yaml` pattern (e.g., `secret.sops.yaml`, `govee-credentials.sops.yaml`).
10. **Flux Integration:** Flux handles SOPS decryption natively at the top-level `Kustomization` tier (`clusters/homelab/flux-system/infra-kustomization.yaml`). You do not need to annotate the SOPS files manually for Flux, just ensure they are validly encrypted via `sops` before git commits.
