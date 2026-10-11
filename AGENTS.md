# Agent Instructions & Contribution Guidelines

This repository manages the Homelab Kubernetes cluster infrastructure, applications, and supporting images using GitOps and Jujutsu (`jj`).

All autonomous agents and contributors must adhere to the workflows, conventions, and verification steps detailed below.

---

## 1. Flux GitOps Architecture

- **GitOps Engine**: The Kubernetes cluster is continuously reconciled by **Flux v2**, tracking the `main` branch of this repository.
- **Protected Branch Policy**: Direct pushes to `main` are strictly prohibited. All changes must go through a GitHub Pull Request and pass all automated CI checks before merging.
- **Cluster Manifest Conventions**: Detailed architectural rules for manifests, namespaces, HelmReleases, SOPS secrets, and Home Assistant live interactions are codified in [`.agents/rules.md`](file:///Users/dtavana/Projects/homelab/.agents/rules.md). Always consult `.agents/rules.md` when adding or modifying Kubernetes resources.

---

## 2. Pre-PR Local Verification & Quality Gates

Before creating a commit or opening a pull request, run the relevant local quality gates to avoid CI failures.

### A. YAML Formatting & Linting
- **Check formatting**:
  ```bash
  yamlfmt -lint -conf .yamlfmt.yaml
  ```
- **Apply formatting fix**:
  ```bash
  yamlfmt -conf .yamlfmt.yaml
  ```

### B. Pre-commit Hooks
The repository configures local hooks in `.pre-commit-config.yaml`:
- `generate-homepage-config`: Executes `ruby scripts/generate-homepage-config.rb` to regenerate Homepage dashboard configurations when apps/infrastructure manifests change.
- `generate-pangolin-blueprint`: Executes `ruby scripts/generate-pangolin-blueprint.rb` to regenerate Pangolin blueprint values when apps/infrastructure manifests change.
- `yamlfmt`: Runs `yamlfmt -conf .yamlfmt.yaml`.
- `yamllint`: Runs `yamllint --config-file .yamllint.yaml`.

Run against all files or changed files:
```bash
# All files
pre-commit run --all-files

# Specific changed files
pre-commit run --files <changed-files>
```

### C. Devpod Image Checks
When modifying anything within `images/devpod/**`:
1. **Version Format**: `images/devpod/VERSION` must strictly follow the `YYYY.MM.DD.REVISION` format (e.g., `2026.10.10.1`).
2. **Version Bump**: If any files under `images/devpod/` (other than `VERSION`) are modified, `images/devpod/VERSION` must be bumped.
3. **Values Tag Match**: The image tag configured in `apps/homelab/devpod/values.yaml` under `image.tag` must match `ubuntu-24.04-<VERSION>` (e.g., `tag: ubuntu-24.04-2026.10.10.1`).

### D. GitHub Actions Workflow Linting
If modifying `.github/workflows/**`:
```bash
actionlint
```

### E. Flux-local Validation
Validate cluster manifests and Helm chart rendering against Flux v2:
```bash
flux-local test --enable-helm --all-namespaces --path clusters/homelab
```

---

## 3. Jujutsu (jj) VCS & Pull Request Workflow

This repository uses **Jujutsu (`jj`)** co-located with Git. Do not use direct Git commands unless specifically instructed. Use `jj --no-pager` in automated environments.

### Step 1: Ensure Base Revision is Up to Date
Ensure your change is based on latest `main@origin`:
```bash
# If starting fresh:
jj new main@origin

# If rebasing an existing revision:
jj rebase -r @ -d main@origin
```

### Step 2: Set Change Description
Commit descriptions follow Conventional Commits format (`type(scope): description`):
```bash
jj describe -m "feat(devpod): add antigravity daemon and document PR workflow"
```

### Step 3: Create and Push Bookmark
Create a descriptive bookmark (Git branch name) and push it to the remote:
```bash
jj bookmark create <bookmark-name>
jj git push --bookmark <bookmark-name>
```

### Step 4: Open Pull Request
Use the GitHub CLI (`gh`) to open a pull request:
```bash
gh pr create --title "<title>" --body "<body>"
```

### Step 5: Monitor CI & Quality Gates
Monitor pull request checks to ensure all workflows pass:
```bash
gh pr checks --watch
```

### Step 6: Report URL
Always provide the resulting pull request URL and check results to the user.
