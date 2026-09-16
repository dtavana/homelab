# Development pod

`devpod` is a persistent SSH-accessible development environment based on
Microsoft's Ubuntu Dev Container image. The container's `/home/dev` directory is
backed by Longhorn, so development files and SSH host keys survive pod restarts
and image updates.

The image is built from `mcr.microsoft.com/devcontainers/base:ubuntu-24.04` by
the `Build devpod image` GitHub Actions workflow and published as
`ghcr.io/dtavana/homelab-devpod`. The image includes Git, Go, Node.js, Python,
Nano, ripgrep, fd, tmux, common build tools, Codex CLI, kubectl, Helm, Flux,
Kustomize, GitHub CLI, SOPS, age, and yq. Packages are installed while the
image is built instead of on every pod startup.

Pull requests that change the image build it without publishing it. Image
releases use `ubuntu-24.04-<version>`, where the version is stored in
`images/devpod/VERSION`; the workflow verifies that `values.yaml` selects the
same tag and rejects image changes that do not bump the version. After the
change reaches `main`, the workflow publishes that tag and Flux rolls the pod.
The versioned tag keeps image deployment compatible with branch protection:
the manifest change goes through the same reviewed PR as the image change.

Renovate tracks the pinned Kubernetes and utility CLI releases in the
Dockerfile. Codex is intentionally updated manually to the version used by
Codex Desktop; check the desktop machine with `codex --version`, update
`CODEX_VERSION`, bump `images/devpod/VERSION`, update the tag in `values.yaml`,
and let the pull-request image build verify `codex app-server` before merging.

Before the HelmRelease can pull the image, make the GitHub Container Registry
package public, or add an image pull secret to the deployment. The first image
is published after the workflow runs on `main`.

## Connect

After Flux reconciles the HelmRelease, retrieve the MetalLB address:

```sh
kubectl -n devpod-system get service devpod
```

Then connect from a host on the WireGuard or home LAN network:

```sh
ssh -p 2222 dev@devpod.internal.dtavana.dev
```

SSH is limited to `10.5.5.0/24` and `192.168.0.0/24`, password authentication
is disabled, and the container loads public keys from
`https://github.com/dtavana.keys`. Change `PUBLIC_KEY_URL` in `values.yaml` if
another GitHub account should be trusted.

The existing 30Gi Longhorn claim is mounted at `/home/dev`. This keeps the
existing devpod data on the same claim while moving away from the LinuxServer
`/config` layout; inspect the claim before deleting any old files.

## Outbound SSH authentication

On container startup, the entrypoint automatically starts a user-owned
`ssh-agent` on the persistent socket `/home/dev/.ssh/agent.sock`. If
`/home/dev/.ssh/id_ed25519_devpod` exists, it loads that identity and exposes
the agent to SSH login sessions. This identity is intended for outbound Git
and SSH connections and is separate from `authorized_keys` and the SSH host
keys used by the devpod server.

The default identity is currently unencrypted so it can be loaded without an
interactive prompt after a pod restart. A passphrase-protected identity will
leave the agent running but requires `ssh-add` manually or SSH agent
forwarding. To use another identity path, set `SSH_IDENTITY_FILE` in the
devpod values. The key is stored on the persistent Longhorn volume; limit
access to the devpod and use a least-privileged key where possible.

## Default repositories

The pod reads `DEFAULT_REPOSITORIES_JSON` at startup. The checked-in default
clones the homelab repository into `/home/dev/src/homelab` only when that path
does not already contain a Git checkout. Existing checkouts are never pulled,
reset, or overwritten. Configure additional repositories in
`apps/homelab/devpod/values.yaml` using objects with `url`, `path`, and optional
`ref` fields.

If a private repository cannot be cloned during startup, SSH remains available.
Authenticate GitHub and retry manually:

```sh
gh auth login
devpod-bootstrap
```

## Codex Desktop remote project

Codex Desktop starts the remote Codex app server through SSH, so `codex` is
installed globally and available to the login shell. Add this to the client
machine's `~/.ssh/config`:

```sshconfig
Host homelab-devpod
    HostName devpod.internal.dtavana.dev
    Port 2222
    User dev
    IdentityFile ~/.ssh/id_ed25519
```

Verify the connection:

```sh
ssh homelab-devpod 'codex --version && kubectl get nodes'
```

For first-time ChatGPT authentication on the headless pod, use device-code
login. The Codex auth cache lives under `/home/dev/.codex` on the persistent
volume; treat it as a credential and never commit it:

```sh
ssh homelab-devpod
codex login --device-auth
```

In Codex Desktop, open Settings → Connections → SSH, add `homelab-devpod`, and
select `/home/dev/src/homelab` as the project folder.

## Kubernetes access

The pod uses the `devpod-admin` service account, which is intentionally bound
to `cluster-admin` for manual administration from the persistent SSH shell.
Codex runs as the same `dev` user and can technically use that access through
direct shell commands; the existing Kubernetes MCP server's confirmation rules
remain a soft safety boundary rather than a Kubernetes permission boundary.

Pod restarts preserve repositories, Codex credentials, SSH keys, and the
kubeconfig configuration, but terminate any in-flight process.
