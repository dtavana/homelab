#!/bin/bash

# This script safely merges the newly generated kubeconfig into the default location.
# It handles cases where the default kubeconfig doesn't exist or is empty.
# Run this from the 'ansible/' directory after a successful deployment.

set -e

NEW_KUBECONFIG="kubeconfig"
DEFAULT_KUBECONFIG_DIR="$HOME/.kube"
DEFAULT_KUBECONFIG_PATH="$DEFAULT_KUBECONFIG_DIR/config"

# --- Main Logic ---

# 1. Check if the new kubeconfig file exists.
if [ ! -f "$NEW_KUBECONFIG" ]; then
    echo "❌ Error: New kubeconfig file not found at '$NEW_KUBECONFIG'."
    echo "Please run the main deployment playbook first."
    exit 1
fi

# 2. Ensure the .kube directory exists.
mkdir -p "$DEFAULT_KUBECONFIG_DIR"

# 3. Check if a valid default kubeconfig already exists.
# The '-s' flag checks if the file exists AND is not empty.
if [ ! -s "$DEFAULT_KUBECONFIG_PATH" ]; then
    echo "✅ Default kubeconfig not found or is empty. Copying new config directly."
    cp "$NEW_KUBECONFIG" "$DEFAULT_KUBECONFIG_PATH"
else
    echo "Merging new kubeconfig into existing configuration..."
    # Backup the old config before merging.
    cp "$DEFAULT_KUBECONFIG_PATH" "$DEFAULT_KUBECONFIG_PATH.bak"
    echo "-> Backup of existing config saved to $DEFAULT_KUBECONFIG_PATH.bak"

    # Safely merge by creating a new file and then replacing the old one.
    KUBECONFIG="$DEFAULT_KUBECONFIG_PATH:$NEW_KUBECONFIG" kubectl config view --flatten > "$DEFAULT_KUBECONFIG_PATH.new"
    mv "$DEFAULT_KUBECONFIG_PATH.new" "$DEFAULT_KUBECONFIG_PATH"
    echo "✅ Kubeconfig merged successfully."
fi

# 4. Set secure file permissions.
chmod 600 "$DEFAULT_KUBECONFIG_PATH"

echo
echo "🎉 Operation complete."
echo "You can now switch to the new context with: kubectl config use-context <context_name>"