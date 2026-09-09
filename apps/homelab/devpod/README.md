# Development pod

`devpod` is a persistent SSH-accessible development environment. The container's
`/config` directory is backed by Longhorn, so the home directory, SSH host keys,
and LinuxServer configuration survive pod restarts and image updates.

## Connect

After Flux reconciles the HelmRelease, retrieve the MetalLB address:

```sh
kubectl -n devpod-system get service devpod
```

Then connect from a host on the WireGuard network:

```sh
ssh -p 2222 dev@<EXTERNAL-IP>
```

SSH is limited to `10.5.5.0/24`, password authentication is disabled, and the
container loads the public keys from `https://github.com/dtavana.keys`. Change
`PUBLIC_KEY_URL` in `values.yaml` if another GitHub account should be trusted.

The package list is installed by LinuxServer's universal package-install mod on
container startup. It is intentionally kept in Git so the toolchain is
reproducible; installed packages are recreated when the pod itself is recreated,
while files and development data under `/config` remain persistent.
