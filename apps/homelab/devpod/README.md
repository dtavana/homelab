# Development pod

`devpod` is a persistent SSH-accessible development environment based on
Microsoft's Ubuntu Dev Container image. The container's `/home/dev` directory is
backed by Longhorn, so development files and SSH host keys survive pod restarts
and image updates.

The image is built from `mcr.microsoft.com/devcontainers/base:ubuntu-24.04` by
the `Build devpod image` GitHub Actions workflow and published as
`ghcr.io/dtavana/homelab-devpod`. The image includes Git, Go, Node.js, Python,
Nano, ripgrep, fd, tmux, and common build tools. Packages are installed while
the image is built instead of on every pod startup.

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
ssh -p 2222 dev@<EXTERNAL-IP>
```

SSH is limited to `10.5.5.0/24` and `192.168.0.0/24`, password authentication
is disabled, and the container loads public keys from
`https://github.com/dtavana.keys`. Change `PUBLIC_KEY_URL` in `values.yaml` if
another GitHub account should be trusted.

The existing 30Gi Longhorn claim is mounted at `/home/dev`. This keeps the
existing devpod data on the same claim while moving away from the LinuxServer
`/config` layout; inspect the claim before deleting any old files.
