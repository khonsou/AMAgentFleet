# AMAgentFleet bootstrap

These objects are applied once by a platform administrator and are deliberately
outside Argo CD's Application:

- StorageClass `am-agent-fleet-ssd-retain` (cluster-scoped, ESSD retained)
- PVC `am-agent-fleet-state` (20Gi, `angrymiao-test`)

```bash
kubectl apply -f deploy/gitops/bootstrap/storageclass.yaml
kubectl apply -f deploy/gitops/bootstrap/core-pvc.yaml
```

The Application only manages Deployments, Services, and the Ingress. Keeping the
PVC out of GitOps avoids an accidental delete if the Application is ever synced
with prune enabled.