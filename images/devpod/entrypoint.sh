#!/usr/bin/env bash
set -euo pipefail

user_name="${USER_NAME:-dev}"
if ! user_home="$(getent passwd "$user_name" | cut -d: -f6)"; then
    echo "Unable to determine the home directory for ${user_name}" >&2
    exit 1
fi

if [[ -z "$user_home" ]]; then
    echo "Unable to determine the home directory for ${user_name}" >&2
    exit 1
fi

ssh_dir="$user_home/.ssh"
authorized_keys="$ssh_dir/authorized_keys"
codex_home="${CODEX_HOME:-${user_home}/.codex}"

ssh_identity_file="${SSH_IDENTITY_FILE:-${ssh_dir}/id_ed25519_devpod}"
ssh_agent_socket="${SSH_AGENT_SOCKET:-${ssh_dir}/agent.sock}"
ssh_agent_started=false

start_ssh_agent() {
    if [[ ! -f "$ssh_identity_file" ]]; then
        return 0
    fi

    rm -f "$ssh_agent_socket"
    runuser -u "$user_name" -- env HOME="$user_home" \
        ssh-agent -a "$ssh_agent_socket" -s >/dev/null

    if ! runuser -u "$user_name" -- env HOME="$user_home" \
        SSH_AUTH_SOCK="$ssh_agent_socket" ssh-add "$ssh_identity_file" </dev/null; then
        echo "Unable to load SSH identity ${ssh_identity_file}; the agent is running without it" >&2
    fi

    ssh_agent_started=true
}

bootstrap_repositories() {
    local repositories_json="${DEFAULT_REPOSITORIES_JSON:-[]}"
    local repository_url repository_path repository_ref parent_dir temp_dir clone_path

    if [[ -z "$repositories_json" ]]; then
        return 0
    fi

    if ! jq -e 'type == "array"' >/dev/null <<<"$repositories_json"; then
        echo "DEFAULT_REPOSITORIES_JSON must be a JSON array; skipping repository bootstrap" >&2
        return 1
    fi

    while IFS=$'\t' read -r repository_url repository_path repository_ref; do
        if [[ -z "$repository_url" || -z "$repository_path" ]]; then
            echo "Skipping repository entry without a URL and path" >&2
            continue
        fi

        if [[ "$repository_path" != "$user_home" && "$repository_path" != "$user_home/"* ]]; then
            echo "Skipping repository outside ${user_home}: ${repository_path}" >&2
            continue
        fi

        if [[ -e "$repository_path/.git" ]]; then
            echo "Repository already exists; leaving it unchanged: ${repository_path}"
            continue
        fi

        if [[ -e "$repository_path" ]] && [[ -n "$(find "$repository_path" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
            echo "Skipping non-empty path that is not a Git checkout: ${repository_path}" >&2
            continue
        fi

        parent_dir="$(dirname "$repository_path")"
        mkdir -p "$parent_dir"
        temp_dir="$(mktemp -d "${parent_dir}/.devpod-bootstrap.XXXXXX")"
        clone_path="${temp_dir}/repository"

        echo "Cloning ${repository_url} into ${repository_path}"
        if [[ -n "$repository_ref" ]]; then
            if ! git clone --branch "$repository_ref" --single-branch "$repository_url" "$clone_path"; then
                echo "Unable to clone ${repository_url}; leaving SSH available for a later retry" >&2
                rm -rf "$temp_dir"
                continue
            fi
        elif ! git clone "$repository_url" "$clone_path"; then
            echo "Unable to clone ${repository_url}; leaving SSH available for a later retry" >&2
            rm -rf "$temp_dir"
            continue
        fi

        if [[ -d "$repository_path" ]] && ! rmdir "$repository_path"; then
            echo "Unable to replace the empty repository path: ${repository_path}" >&2
            rm -rf "$temp_dir"
            continue
        fi

        if ! mv "$clone_path" "$repository_path"; then
            echo "Unable to install cloned repository at ${repository_path}" >&2
            rm -rf "$temp_dir"
            continue
        fi
        rmdir "$temp_dir"

        if [[ "$(id -u)" -eq 0 ]]; then
            chown -R "$user_name:$user_name" "$repository_path"
        fi
    done < <(jq -r '.[] | [(.url // ""), (.path // ""), (.ref // "")] | @tsv' <<<"$repositories_json")
}

if [[ "$(basename "$0")" == "devpod-bootstrap" || "${1:-}" == "bootstrap" ]]; then
    bootstrap_repositories
    exit $?
fi

chown "$user_name:$user_name" "$user_home"
install -d -o "$user_name" -g "$user_name" -m 0700 "$ssh_dir"
install -d -o "$user_name" -g "$user_name" -m 0700 "$codex_home"
if [[ ! -e "$authorized_keys" ]]; then
    install -o "$user_name" -g "$user_name" -m 0600 /dev/null "$authorized_keys"
fi

if [[ -n "${PUBLIC_KEY_URL:-}" ]]; then
    key_file="$(mktemp)"
    trap 'rm -f "$key_file"' EXIT

    if curl --fail --location --silent --show-error "$PUBLIC_KEY_URL" >"$key_file" \
        && grep -qE '^(ssh-|ecdsa-)' "$key_file"; then
        install -o "$user_name" -g "$user_name" -m 0600 "$key_file" "$authorized_keys"
    elif [[ ! -s "$authorized_keys" ]]; then
        echo "Unable to download SSH keys and no existing authorized_keys file is available" >&2
        exit 1
    else
        echo "Unable to download SSH keys; keeping the existing authorized_keys file" >&2
    fi
fi

for key_type in ed25519 rsa; do
    host_key="$ssh_dir/sshd_host_${key_type}_key"
    if [[ ! -s "$host_key" ]]; then
        ssh-keygen -q -t "$key_type" -f "$host_key" -N ''
    fi
    chown root:root "$host_key" "${host_key}.pub"
    chmod 0600 "$host_key"
    chmod 0644 "${host_key}.pub"
done

chown "$user_name:$user_name" "$ssh_dir" "$authorized_keys"
chmod 0700 "$ssh_dir"
chmod 0600 "$authorized_keys"

start_ssh_agent

service_account_dir=/var/run/secrets/kubernetes.io/serviceaccount
kubeconfig_path="${KUBECONFIG:-/etc/devpod/kubeconfig}"
if [[ -r "$service_account_dir/token" && -r "$service_account_dir/ca.crt" ]]; then
    install -d -m 0755 "$(dirname "$kubeconfig_path")"
    cat >"$kubeconfig_path" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: in-cluster
  cluster:
    certificate-authority: ${service_account_dir}/ca.crt
    server: https://kubernetes.default.svc.cluster.local:443
users:
- name: ${user_name}
  user:
    tokenFile: ${service_account_dir}/token
contexts:
- name: in-cluster
  context:
    cluster: in-cluster
    namespace: default
    user: ${user_name}
current-context: in-cluster
EOF
    chmod 0644 "$kubeconfig_path"
fi

if [[ -n "${DEFAULT_REPOSITORIES_JSON:-}" ]]; then
    if ! runuser -u "$user_name" -- env HOME="$user_home" USER="$user_name" \
        GIT_TERMINAL_PROMPT=0 \
        DEFAULT_REPOSITORIES_JSON="$DEFAULT_REPOSITORIES_JSON" \
        /usr/local/bin/devpod-bootstrap; then
        echo "Repository bootstrap completed with errors; continuing startup" >&2
    fi
fi

install -d -m 0755 /etc/ssh/sshd_config.d /run/sshd
cat >/etc/profile.d/devpod.sh <<EOF
export HOME=${user_home}
export CODEX_HOME=${codex_home}
export KUBECONFIG=${kubeconfig_path}
EOF
if [[ "$ssh_agent_started" == true ]]; then
    printf 'export SSH_AUTH_SOCK=%s\n' "$ssh_agent_socket" >> /etc/profile.d/devpod.sh
fi
chmod 0644 /etc/profile.d/devpod.sh
sshd_environment="HOME=${user_home} CODEX_HOME=${codex_home} KUBECONFIG=${kubeconfig_path}"
if [[ "$ssh_agent_started" == true ]]; then
    sshd_environment+=" SSH_AUTH_SOCK=${ssh_agent_socket}"
fi
cat > /etc/ssh/sshd_config.d/10-devpod.conf <<EOF
Port 2222
ListenAddress 0.0.0.0
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile %h/.ssh/authorized_keys
AllowUsers ${user_name}
UsePAM yes
X11Forwarding no
UseDNS no
SetEnv ${sshd_environment}
HostKey ${ssh_dir}/sshd_host_ed25519_key
HostKey ${ssh_dir}/sshd_host_rsa_key
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

sshd -t
exec /usr/sbin/sshd -D -e
