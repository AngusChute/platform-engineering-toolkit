# terraform-gke

A self-contained Terraform module that provisions a production-ready GKE cluster on Google Cloud, designed to hand off cleanly to Flux for GitOps-driven deployments (see [`../gitops-flux`](../gitops-flux)).

## What this builds

- A dedicated VPC and subnet, with secondary ranges for pods and services (VPC-native networking)
- A **private** GKE cluster — nodes have no public IPs, reducing attack surface
- **Workload Identity** enabled, so workloads authenticate to GCP APIs without long-lived service account keys
- An autoscaling node pool, separated from the cluster resource so it can be resized or replaced independently
- Managed Prometheus enabled out of the box for cluster metrics
- A pinned release channel (`REGULAR` by default) so node and control-plane upgrades are predictable rather than ad hoc

## Design decisions worth calling out

**Private nodes by default.** Production clusters shouldn't expose node IPs publicly. This adds a small amount of setup complexity (you'll need a bastion host, Cloud NAT, or `gcloud` SSH tunneling to reach nodes directly) but removes an entire class of exposure.

**Node pool separation.** Keeping the node pool as its own resource (rather than letting GKE manage a default pool) means node pool changes — machine type swaps, scaling policy changes — don't risk touching the cluster control plane.

**No client certificates.** `issue_client_certificate = false` forces authentication through IAM/Workload Identity rather than long-lived certs, which is both more secure and easier to audit.

## Usage

```bash
cd terraform-gke
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your project ID and desired settings

terraform init
terraform plan
terraform apply
```

Once applied, configure `kubectl`:

```bash
terraform output get_credentials_command
# copy and run the output, e.g.:
gcloud container clusters get-credentials platform-cluster --region us-central1 --project your-project-id
```

From there, bootstrap Flux against the cluster using the structure in [`../gitops-flux`](../gitops-flux).

## Requirements

| Name | Version |
|---|---|
| terraform | >= 1.5.0 |
| google provider | ~> 5.0 |

You'll also need a GCP project with the Kubernetes Engine API enabled, and credentials available to Terraform (typically via `gcloud auth application-default login`).
