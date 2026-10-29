# AMAgentFleet GitOps

This directory is the GitOps source of truth for the `angrymiao-test` Agent
Fleet deployment. `deploy/deploy.sh` and the manifests in `deploy/k8s/` remain
as a legacy fallback and must not run against the same resources while Argo CD
is managing them.

## Layout

- `base/` — gateway/control-plane, console backend, console web, and Ingress.
- `overlays/test/` — `angrymiao-test` namespace, image registry, and image tags.
- `bootstrap/` — StorageClass and PVC, applied once and outside Argo CD.

The initial overlay uses the live public-ACR tags so the first Argo CD sync is a
no-op. The release workflow replaces all three images with
`registry-vpc.cn-shenzhen.aliyuncs.com/angrymiao/am-agent-fleet` and
`gateway-test-sha-<commit>`, `backend-test-sha-<commit>`,
`frontend-test-sha-<commit>`.

## Release

1. GitHub Actions -> `ci` -> Run workflow.
2. Leave `image_tag` empty, set `runner` to `github-hosted`, enable
   `push_image` and `release`.
3. The workflow builds the pinned DeerFlow gateway base, the gateway overlay
   image, the backend image, and the console frontend image, then pushes all
   three and updates `overlays/test/kustomization.yaml`.
4. Review `argocd app diff am-agent-fleet-test`, then
   `argocd app sync am-agent-fleet-test`.
5. Verify `https://fleet.angrymiao.com/` and
   `curl -fsS https://fleet.angrymiao.com/api/console/health`.

The first gateway build downloads and compiles
`https://github.com/khonsou/deer-flow.git#refs/tags/am-fleet-base-v2` and can
take 10-20 minutes. A self-hosted runner must have adequate disk, memory, and
network access to GitHub and PyPI.

## Rollback

Run the workflow again with the previous `image_tag` (the common suffix, for
example `sha-2ed48b9c1a2b`), or revert the overlay commit, then sync
`am-agent-fleet-test` in Argo CD.

## Secrets and bootstrap

These are out of band and must exist before the first sync:

- `am-agent-fleet-secrets` — `DEEPSEEK_API_KEY`, `DATABASE_URL`
- `am-agent-fleet-auth` — DAO JWT public key and issuer
- `aliyun-reg-secret` — ACR pull credentials
- `am-tls` — wildcard certificate for `fleet.angrymiao.com`

The PVC and StorageClass are in `bootstrap/` and are not managed by Argo CD.

## Notes

- This is the `angrymiao-test` environment. A production overlay would use
  `angrymiao-prod` and a separate Application.
- The gateway and control plane share one Pod and one `am-agent-fleet-state`
  PVC. The gateway runs with `DEER_FLOW_AUTH_DISABLED=1` and is only exposed
  through the console web Service; do not add a public gateway Ingress.
- The Deployment is single-replica; a rollout briefly interrupts the console.
- Do not run `deploy/deploy.sh` and Argo CD against the same resources at the
  same time.