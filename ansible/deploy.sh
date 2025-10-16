#!/bin/bash
# A script to configure nodes and deploy the k3s cluster.

set -e

# Check if a username was provided as an argument.
if [ -z "$1" ]; then
    echo "❌ Error: No temporary username provided."
    echo "Usage: ./deploy.sh <your-temp-user>"
    exit 1
fi

TEMP_USER="$1"

echo "🚀 Starting K3s Cluster Deployment..."
echo

# --- STEP 1: Configure Base OS ---
echo "STEP 1: Configuring base OS on nodes as user '$TEMP_USER'..."
echo "You will be prompted for passwords in the following order: Vault, SSH, then SUDO."
ansible-playbook playbooks/configure-nodes.yml --user "$TEMP_USER" -k -K --ask-vault-pass

echo "✅ Base OS configuration complete."
echo

# --- STEP 2: Deploy K3s Cluster ---
echo "STEP 2: Deploying K3s cluster..."
echo "You will be prompted for your Ansible Vault password again."
ansible-playbook ../tools/k3s-ansible/site.yml --ask-vault-pass

echo "✅ K3s deployment complete."
echo

# --- STEP 3: Move the Kubeconfig File ---
echo "STEP 3: Moving kubeconfig to the 'ansible/' directory..."
mv ../tools/k3s-ansible/kubeconfig ./kubeconfig

echo
echo "🎉 Full deployment finished! Your kubeconfig is located at 'ansible/kubeconfig'. 🎉"