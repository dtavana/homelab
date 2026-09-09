#!/usr/bin/env bash
set -euo pipefail

user_name="${USER_NAME:-dev}"
if ! user_home="$(getent passwd "$user_name" | cut -d: -f6)"; then
    echo "Unable to determine the home directory for ${user_name}" >&2
    exit 1
fi
ssh_dir="$user_home/.ssh"
authorized_keys="$ssh_dir/authorized_keys"

if [[ -z "$user_home" ]]; then
    echo "Unable to determine the home directory for ${user_name}" >&2
    exit 1
fi

chown "$user_name:$user_name" "$user_home"
install -d -o "$user_name" -g "$user_name" -m 0700 "$ssh_dir"

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

install -d -m 0755 /etc/ssh/sshd_config.d /run/sshd
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
HostKey ${ssh_dir}/sshd_host_ed25519_key
HostKey ${ssh_dir}/sshd_host_rsa_key
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

sshd -t
exec /usr/sbin/sshd -D -e
