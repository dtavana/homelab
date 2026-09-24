FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

USER root

ARG TARGETARCH
ARG CODEX_VERSION=0.154.0-alpha.6.2
# renovate: datasource=github-releases depName=kubernetes/kubernetes extractVersion=^v(?<version>.*)$
ARG KUBECTL_VERSION=1.37.1
# renovate: datasource=github-releases depName=helm/helm extractVersion=^v(?<version>.*)$
ARG HELM_VERSION=3.17.3
# renovate: datasource=github-releases depName=fluxcd/flux2 extractVersion=^v(?<version>.*)$
ARG FLUX_VERSION=2.5.1
# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize extractVersion=^kustomize/v(?<version>.*)$
ARG KUSTOMIZE_VERSION=5.6.0
# renovate: datasource=github-releases depName=cli/cli extractVersion=^v(?<version>.*)$
ARG GH_VERSION=2.67.0
# renovate: datasource=github-releases depName=getsops/sops extractVersion=^v(?<version>.*)$
ARG SOPS_VERSION=3.9.4
# renovate: datasource=github-releases depName=FiloSottile/age extractVersion=^v(?<version>.*)$
ARG AGE_VERSION=1.2.0
# renovate: datasource=github-releases depName=mikefarah/yq extractVersion=^v(?<version>.*)$
ARG YQ_VERSION=4.45.1
# renovate: datasource=github-releases depName=jj-vcs/jj extractVersion=^v(?<version>.*)$
ARG JJ_VERSION=0.45.1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        build-essential \
        ca-certificates \
        curl \
        fd-find \
        golang-go \
        jq \
        nano \
        nodejs \
        npm \
        openssh-server \
        bubblewrap \
        python3-pip \
        python3-venv \
        ripgrep \
        tmux \
        unzip \
        wget \
        zip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) \
            helm_arch=amd64; \
            flux_arch=amd64; \
            kustomize_arch=amd64; \
            gh_arch=amd64; \
            sops_arch=amd64; \
            age_arch=amd64; \
            yq_arch=amd64; \
            jj_arch=x86_64; \
            ;; \
        arm64) \
            helm_arch=arm64; \
            flux_arch=arm64; \
            kustomize_arch=arm64; \
            gh_arch=arm64; \
            sops_arch=arm64; \
            age_arch=arm64; \
            yq_arch=arm64; \
            jj_arch=aarch64; \
            ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    install -d -m 0755 /usr/local/bin; \
    curl --fail --silent --show-error --location \
        "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
        --output /usr/local/bin/kubectl; \
    curl --fail --silent --show-error --location \
        "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${helm_arch}.tar.gz" \
        | tar -xz -C /tmp; \
    install -m 0755 "/tmp/linux-${helm_arch}/helm" /usr/local/bin/helm; \
    rm -rf "/tmp/linux-${helm_arch}"; \
    curl --fail --silent --show-error --location \
        "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_linux_${flux_arch}.tar.gz" \
        | tar -xz -C /usr/local/bin flux; \
    curl --fail --silent --show-error --location \
        "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_${kustomize_arch}.tar.gz" \
        | tar -xz -C /usr/local/bin kustomize; \
    curl --fail --silent --show-error --location \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${gh_arch}.tar.gz" \
        | tar -xz -C /tmp; \
    install -m 0755 "/tmp/gh_${GH_VERSION}_linux_${gh_arch}/bin/gh" /usr/local/bin/gh; \
    rm -rf "/tmp/gh_${GH_VERSION}_linux_${gh_arch}"; \
    curl --fail --silent --show-error --location \
        "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${sops_arch}" \
        --output /usr/local/bin/sops; \
    curl --fail --silent --show-error --location \
        "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-${age_arch}.tar.gz" \
        | tar -xz -C /tmp; \
    install -m 0755 "/tmp/age/age" "/tmp/age/age-keygen" /usr/local/bin/; \
    rm -rf /tmp/age; \
    curl --fail --silent --show-error --location \
        "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${yq_arch}" \
        --output /usr/local/bin/yq; \
    mkdir -p /tmp/jj-release; \
    curl --fail --silent --show-error --location \
        "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-${jj_arch}-unknown-linux-musl.tar.gz" \
        | tar -xz -C /tmp/jj-release; \
    install -m 0755 /tmp/jj-release/jj /usr/local/bin/jj; \
    rm -rf /tmp/jj-release; \
    chmod 0755 /usr/local/bin/kubectl /usr/local/bin/sops /usr/local/bin/yq

RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && command -v codex \
    && codex --version \
    && codex app-server --help >/dev/null \
    && command -v bwrap \
    && tmux -V \
    && kubectl version --client \
    && helm version --short \
    && flux --version \
    && kustomize version \
    && gh --version \
    && sops --version \
    && age --version \
    && yq --version \
    && jj --version

# Keep the existing SSH username and UID/GID used by the devpod deployment.
RUN usermod --login dev --home /home/dev --move-home vscode \
    && groupmod --new-name dev vscode \
    && printf 'dev ALL=(root) NOPASSWD:ALL\n' > /etc/sudoers.d/dev \
    && chmod 0440 /etc/sudoers.d/dev

COPY entrypoint.sh /usr/local/bin/devpod-entrypoint
RUN chmod 0755 /usr/local/bin/devpod-entrypoint \
    && ln -s /usr/local/bin/devpod-entrypoint /usr/local/bin/devpod-bootstrap

ENV PUBLIC_KEY_URL=https://github.com/dtavana.keys

EXPOSE 2222

ENTRYPOINT ["/usr/local/bin/devpod-entrypoint"]
