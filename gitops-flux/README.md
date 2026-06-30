# gitops-flux

A Flux-based GitOps structure for continuously reconciling the GKE cluster built in [`../terraform-gke`](../terraform-gke) against the state declared in this repo.

## Why Flux

Flux watches this Git repository and continuously reconciles the live cluster state to match what's declared here — no manual `kubectl apply`, no drift between "what's running" and "what's in Git." If someone makes a manual change directly against the cluster, Flux reverts it on the next reconciliation pass. That's the GitOps guarantee: Git is the only source of truth.

## Structure

```
gitops-flux/
├── clusters/
│   └── production/
│       └── flux-system.yaml   # GitRepository + Kustomization — what Flux watches and applies
└── apps/
    ├── base/                  # Environment-agnostic manifests
    │   ├── deployment.yaml
    │   └── kustomization.yaml
    └── production/            # Production-specific overlay (replica count, resource limits)
        ├── deployment-patch.yaml
        └── kustomization.yaml
```

This follows the standard **base + overlay** Kustomize pattern: `apps/base` defines the generic shape of a deployment, and `apps/production` patches it with environment-specific values (here, higher replica counts and resource limits than you'd want in a dev/staging environment). Adding a `staging` overlay later is a matter of adding `apps/staging/` with its own patches — no duplication of the base manifest.

## Bootstrapping Flux against the cluster

Assuming you've already provisioned the cluster via `terraform-gke` and configured `kubectl`:

```bash
# Install the Flux CLI if you don't have it
brew install fluxcd/tap/flux

# Check the cluster is ready for Flux
flux check --pre

# Bootstrap Flux, pointing it at this repo
flux bootstrap github \
  --owner=AngusChute \
  --repository=platform-engineering-toolkit \
  --branch=main \
  --path=gitops-flux/clusters/production \
  --personal
```

This installs the Flux controllers into the `flux-system` namespace and applies the `GitRepository`/`Kustomization` resources in `clusters/production/flux-system.yaml`, which in turn tells Flux to start reconciling everything under `apps/production`.

## How reconciliation works here

1. `GitRepository` polls this repo every minute for changes
2. `Kustomization` re-applies `apps/production` every 5 minutes (or immediately on a new commit, via webhook if configured)
3. `prune: true` means resources removed from Git are also removed from the cluster — no orphaned objects
4. `healthChecks` block Flux from marking a reconciliation successful until the deployment is actually healthy, which is what makes Flux suitable for gating progressive rollouts

## Extending this

A few natural next steps for a real production setup:
- Add `apps/staging/` as a second overlay sharing the same base
- Add a Flux `Notification` controller wired to Slack/PagerDuty so reconciliation failures alert immediately
- Add `ImageRepository`/`ImagePolicy` resources for automated image updates instead of manual tag bumps
