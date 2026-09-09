FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

USER root

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
        python3-pip \
        python3-venv \
        ripgrep \
        tmux \
        unzip \
        wget \
        zip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Keep the existing SSH username and UID/GID used by the devpod deployment.
RUN usermod --login dev --home /home/dev --move-home vscode \
    && groupmod --new-name dev vscode \
    && printf 'dev ALL=(root) NOPASSWD:ALL\n' > /etc/sudoers.d/dev \
    && chmod 0440 /etc/sudoers.d/dev

COPY entrypoint.sh /usr/local/bin/devpod-entrypoint
RUN chmod 0755 /usr/local/bin/devpod-entrypoint

ENV PUBLIC_KEY_URL=https://github.com/dtavana.keys

EXPOSE 2222

ENTRYPOINT ["/usr/local/bin/devpod-entrypoint"]
