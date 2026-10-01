# Home Assistant deployment notes

This release is managed by Flux. The Flux `GitRepository` tracks `main`, and the Home Assistant values are rendered into the `home-assistant-values` ConfigMap by `apps/homelab/home-assistant/kustomization.yaml`.

## Operational lessons

- Pin init-container images and downloaded integrations. `alpine:latest` and HACS's `releases/latest` URL made the pod non-reproducible; `values.yaml` now pins Alpine and HACS 2.0.5.
- A values change is not live until it is merged into `main` and Flux reconciles the HelmRelease. Verify both the HelmRelease revision and the StatefulSet after reconciliation.
- Home Assistant automations live on the persistent `/config` volume. A YAML automation can reappear after a UI deletion, so remove the source entry from `/config/automations.yaml`, reload automations, and remove any stale restored entity.
- LG webOS reports `unavailable` when the TV is powered off because its network service is unavailable. Automations that interpret the TV's power state should handle `unavailable` alongside `off` and `standby`.
- This chart uses `hostNetwork: true`, but that is not enough for direct Bluetooth adapter management. Full local-adapter support also needs `NET_ADMIN`, `NET_RAW`, and a read-only `/run/dbus` mount. The current deployment relies on network Bluetooth proxies and does not add those host-level permissions.
- HASS.Agent 2.1.2 is the current HACS release. Its device-registry deprecation warning requires an upstream integration release; replacing it with an unrelated fork would require a client migration.
