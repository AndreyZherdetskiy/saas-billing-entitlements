# Helm smoke on kind / minikube

Short checklist for local verification of chart `deploy/helm/billing-platform/` without registry push.

## Prerequisites

- Docker, [kind](https://kind.sigs.k8s.io/) or minikube
- Helm 3 (`~/.local/bin/helm` or PATH)
- Built local images (see Task 34 report)

```bash
docker build -f deploy/docker/Dockerfile.api -t local/billing-platform:api .
docker build -f deploy/docker/Dockerfile.worker -t local/billing-platform:worker .
docker build -f deploy/docker/Dockerfile.outbox-relay -t local/billing-platform:outbox-relay .
```

## kind (recommended)

```bash
kind create cluster --name billing-smoke
kind load docker-image local/billing-platform:api --name billing-smoke
kind load docker-image local/billing-platform:worker --name billing-smoke
kind load docker-image local/billing-platform:outbox-relay --name billing-smoke

# HPA stub requires metrics-server
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# For kind often need --kubelet-insecure-tls in metrics-server args (see kind docs)

helm template billing-platform deploy/helm/billing-platform | kubectl apply -f -
kubectl get deploy,hpa -l app.kubernetes.io/instance=billing-platform
```

**Expected:** 4 Deployments (api, worker, beat, outbox-relay), 1 HPA on api; api has `httpGet` `/health/live` and `/health/ready` on port 8000.

Without external Postgres/Redis/Kafka, api/worker/relay pods go CrashLoop or `Not Ready` — normal for scaffold-smoke; goal is render + apply + visible probes in `kubectl describe pod`.

## minikube

```bash
minikube start
eval $(minikube docker-env)
# rebuild images in minikube daemon (same docker build)
minikube addons enable metrics-server
helm template billing-platform deploy/helm/billing-platform | kubectl apply -f -
```

## Quick check without cluster

```bash
PATH="$HOME/.local/bin:$PATH" helm template billing-platform deploy/helm/billing-platform
uv run pytest tests/integration/test_helm_template.py tests/integration/test_helm_probes_render.py -v
```

Sufficient for CI / CP-S3-0 if kind unavailable.

## Probes (Task 35)

| Workload | Liveness | Readiness |
|----------|----------|-----------|
| api | `GET /health/live:8000` | `GET /health/ready:8000` |
| worker | `celery inspect ping` (exec) | same — broker registration check |
| outbox-relay | `os.kill(1,0)` (exec) | same — process alive; no HTTP |
| beat | probes not set (optional follow-up) |

## Teardown

```bash
helm template billing-platform deploy/helm/billing-platform | kubectl delete -f -
kind delete cluster --name billing-smoke
```

## Links

- Spec §8.6 (health api), §11.3 (Helm + HPA stub)
- [`ready-probe-fail.md`](ready-probe-fail.md) — ReadyProbeFail alert
- Chart: `deploy/helm/billing-platform/`
