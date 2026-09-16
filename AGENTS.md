# Homelab Repository Agent Guide

## Role and operating model

This repository is the declarative source of truth for a single `homelab`
Kubernetes cluster managed by Flux. Treat the live cluster as the observed
state and this Git repository as the desired state.

Make the smallest targeted change that solves the request. Inspect the
relevant object and its consumers before editing it, and verify the rendered
or live result after a change. Do not make unrelated cleanup changes.

The repository also contains Home Assistant infrastructure. For live Home
Assistant state and configuration, use the Home Assistant MCP server first.
For Kubernetes and Flux state, use the Kubernetes MCP server. MCP access is an
operational interface; it does not replace making persistent configuration
changes in Git.

The supplementary rules in `.agents/rules.md` apply as well. This file records
the repository topology and workflow that agents need in addition to those
rules.

## MCP servers

### Home Assistant MCP (`ha-mcp`)

Use the Home Assistant MCP server (`mcp__home_assistant__...`) for live Home
Assistant entities, devices, areas, floors, helpers, automations, scripts,
scenes, dashboards, services, history, traces, logs, and configuration.

Use narrow reads: prefer an exact entity ID, automation ID, helper, area, or
device over a broad overview or search. Bound history, trace, and log queries
to the shortest useful time window, normally 15–30 minutes. Read first and
remain read-only unless the user explicitly requests a live change.

For HA changes, prefer HA-managed API/configuration tools over editing files in
the Home Assistant PVC. Do not edit `.storage` files directly. Do not use
Kubernetes pod exec/copy or PVC-level edits for HA unless the MCP server is
unavailable or insufficient, or the user explicitly asks for that fallback;
state the reason before using the fallback.

The deployed server is defined by:

- HelmRelease: `apps/base/ha-mcp/helmrelease.yaml`
- Cluster overlay: `apps/homelab/ha-mcp/`
- Namespace: `hass-system`
- Internal endpoint: `https://ha-mcp.internal.dtavana.dev`
- Token secret: `apps/homelab/ha-mcp/ha-mcp-token.sops.yaml`

The server is configured with WebSockets and tool security policies enabled.
Its deployment values currently set `READ_ONLY_MODE: "false"`, so agent
behavior must provide the safety boundary: inspect first, make bounded writes,
and verify the result.

When changing an HA object, check downstream references before renaming or
removing it. Report exact entity IDs, automation IDs, helper names, and final
states. Follow the HA MCP server's best-practice guidance when it is exposed
in the current session.

### Kubernetes MCP (`kubernetes-mcp-server`)

Use the Kubernetes MCP server (`mcp__kubernetes__...`) for live Kubernetes
resources, namespaces, pods, logs, events, metrics, and Flux custom resources.
Prefer the narrow generic resource tools for Flux objects:

- `resources_get` for one known object
- `resources_list` with an exact namespace, kind, and selector when listing is
  necessary
- `pods_get`, `pods_list_in_namespace`, and `pods_log` for one workload
- `events_list` scoped to the affected namespace when diagnosing failures

Flux API groups used here include:

- `source.toolkit.fluxcd.io/v1` (`GitRepository`, `HelmRepository`,
  `OCIRepository`)
- `kustomize.toolkit.fluxcd.io/v1` (`Kustomization`)
- `helm.toolkit.fluxcd.io/v2` (`HelmRelease`)

The deployed server is defined by:

- HelmRelease: `apps/base/kubernetes-mcp-server/helmrelease.yaml`
- Cluster overlay: `apps/homelab/kubernetes-mcp-server/`
- Namespace: `kubernetes-mcp-system`
- Internal endpoint: `https://kubernetes-mcp.internal.dtavana.dev`

The server has read access to Flux resources and a confirmation-gated write
policy for Kubernetes resources. It is configured with
`confirmation_fallback: deny`. Use it for observation and diagnosis by
default. Do not directly mutate GitOps-managed workloads to make a persistent
configuration change; edit the corresponding manifest and let Flux reconcile
it. Direct live writes are reserved for an explicitly requested operational
action or a bounded recovery step.

Do not request broad all-namespace listings or unbounded logs unless the issue
genuinely requires them. Before any live write, identify the exact resource,
namespace, and intended effect; after it, fetch the object again and verify it.

### MCP fallback and credentials

Never print, copy, or commit MCP tokens, kubeconfigs, `.codex` credentials, or
decrypted secret contents. If the appropriate MCP server is unavailable,
explain the fallback and use the least-broad available read path. The devpod
documentation in `apps/homelab/devpod/README.md` describes the administrative
shell and kubeconfig, but shell access is not a reason to bypass the MCP
workflow.

## Flux topology

### Bootstrap and reconciliation graph

Flux bootstrap is under `clusters/homelab/flux-system/`:

- `gotk-sync.yaml` defines the `flux-system` `GitRepository` and root Flux
  `Kustomization`.
- The Git source tracks the `main` branch at
  `ssh://git@github.com/dtavana/homelab` and polls every minute.
- The root Kustomization reads `./clusters/homelab`, prunes removed objects,
  and reconciles every 10 minutes.
- `kustomization.yaml` includes the generated Flux components, the Git source,
  `apps-kustomization.yaml`, and `infra-kustomization.yaml`.

The two child Kustomizations are the important deployment boundaries:

| Flux object | Source path | Interval | Ordering | SOPS |
| --- | --- | --- | --- | --- |
| `Kustomization/infrastructure` in `flux-system` | `./infrastructure/homelab` | 10m | first | enabled with `sops-pgp` |
| `Kustomization/apps` in `flux-system` | `./apps/homelab` | 10m | depends on `infrastructure` | enabled with `sops-pgp` |

Both child Kustomizations prune and have a 10-minute timeout. Their
`healthChecks` list the HelmReleases whose readiness is used for reconciliation
health. If adding a new critical HelmRelease, update the appropriate child
Kustomization's health checks deliberately.

### Base and cluster overlay layout

The repository uses a base/overlay pattern:

- `apps/base/<component>/` contains shared application resources such as
  `namespace.yaml`, `helmrelease.yaml`, and the base `kustomization.yaml`.
- `apps/homelab/<component>/` contains cluster-specific values, patches, and
  encrypted secrets, and references the base with a relative resource path.
- `infrastructure/base/<component>/` and
  `infrastructure/homelab/<component>/` follow the same pattern for cluster
  infrastructure.
- `apps/homelab/kustomization.yaml` is the allow-list of deployed apps.
- `infrastructure/homelab/kustomization.yaml` is the allow-list of deployed
  infrastructure and also includes `infrastructure/base/helm-sources`.

Most components use this flow:

```text
apps|infrastructure/homelab/<component>/values.yaml
  -> ConfigMap <helmrelease>-values in flux-system
  -> apps|infrastructure/base/<component>/helmrelease.yaml
  -> Helm chart in targetNamespace
```

The overlay ConfigMap generators set `disableNameSuffixHash: true`, so the
`valuesFrom` name in the base HelmRelease remains stable. HelmReleases are
normally created in `flux-system` and deploy into an explicit
`<component>-system` target namespace. Home Assistant-related components such
as `home-assistant`, `ha-mcp`, `govee2mqtt`, `matter-server`, and
`zigbee2mqtt` use the shared `hass-system` namespace.

## Targeted change procedure

1. Identify whether the request concerns live HA state, live Kubernetes/Flux
   state, or persistent Git configuration.
2. Inspect the exact live object with the appropriate MCP server when runtime
   state matters.
3. Locate the owning overlay and base resources. Follow the component's
   `kustomization.yaml` and `valuesFrom` reference before editing.
4. For a chart value or environment-specific setting, edit only
   `apps/homelab/<component>/values.yaml` or
   `infrastructure/homelab/<component>/values.yaml`.
5. For shared release metadata, chart version, source reference, remediation,
   or target namespace, edit the corresponding base `helmrelease.yaml`.
6. For a new component, add its base resources, overlay resources, and one
   entry in the appropriate top-level homelab Kustomization. Add a namespace
   manifest rather than relying on `createNamespace: true`.
7. For sensitive values, use an appropriately named `*.sops.yaml` secret in the
   homelab overlay and include it in that overlay's Kustomization. Encrypt it
   with SOPS before committing; never add plaintext credentials.
8. Render or lint the affected path, inspect the diff, and verify Flux or the
   live workload after reconciliation when the task includes deployment.

Do not hand-edit generated or bootstrap output:

- `clusters/homelab/flux-system/gotk-components.yaml` is generated by Flux.
- `clusters/homelab/flux-system/gotk-sync.yaml` is bootstrap output; change it
  only when intentionally changing bootstrap settings.
- `apps/homelab/homepage/generated/` is generated by
  `scripts/generate-homepage-config.rb`; edit the source config or input
  manifests and regenerate it.
- `infrastructure/homelab/pangolin/blueprint.yaml` is generated by
  `scripts/generate-pangolin-blueprint.rb`; edit its source inputs and
  regenerate it.

## Secrets and persistence

SOPS decryption is configured on both child Flux Kustomizations with the
`flux-system/sops-pgp` secret. `.sops.yaml` encrypts sensitive `data`,
`stringData`, and `email` fields in YAML. Keep encrypted secrets in the
homelab overlay nearest to the component that consumes them.

Do not edit Kubernetes `.storage` files, Home Assistant PVC contents, or live
generated ConfigMaps to make a persistent change. Update the owning source in
Git or use the HA-managed MCP/API path as appropriate.

## Validation and handoff

Use the repository's existing checks after changes:

```sh
pre-commit run --all-files --show-diff-on-failure
kustomize build apps/homelab
kustomize build infrastructure/homelab
```

The pre-commit hooks run both generators for relevant homelab YAML changes,
then `yamlfmt` and `yamllint`. CI runs the same pre-commit checks and a YAML
format check. If a local binary or cluster is unavailable, say which check was
not run rather than implying success.

For every completed change, report:

- the exact files and Kubernetes/HA objects changed;
- the relevant namespace, HelmRelease, Kustomization, entity, or ID;
- validation performed and its result;
- live reconciliation or runtime verification status; and
- any remaining risk or follow-up.

## Working note

Maintain a compact working set while investigating:

- Goal
- Known facts
- Last verified state and timestamp
- Change being made
- Verification needed
- Remaining risk

After each meaningful MCP call, retain only the output needed for the next
step and refresh facts that may have become stale.
