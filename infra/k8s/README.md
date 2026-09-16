# Kubernetes deployment template

These resources are a deployment template. They have not been deployed to a cluster as part of the local performance run.

1. Provision PostgreSQL 16 with pgvector, durable Redis (noeviction), separate cache Redis (allkeys-lru), an OTLP collector, and a local Ollama service. Give them the service names in `configmap.yaml`, or edit those endpoints.
2. Build and publish an immutable app image; replace `atlas-rag-api:latest` with its digest. For a local kind cluster, load the built image with `kind load docker-image atlas-rag-api:latest`.
3. Create the namespace. Create the three Secrets privately using `secrets.example.yaml` as a schema; do not apply its placeholder values. The API receives no migration credentials.
4. Run the migration Job and wait for completion before applying the Deployments, Service, and HPA.
5. Download the Qwen model into the Ollama service. The init containers download only the two pinned CPU retrieval models.
6. Port-forward `service/atlas-api 8100:8100`, then sign into the UI with a tenant API key. Local convenience workspace login is disabled in cluster mode.

The HPA requires metrics-server. Scaling API replicas does not multiply the capacity of a single inference server. Each API process admits one generation at a time; size the inference tier separately. Redis coordinates rate limits and circuit state across replicas. Workers use leased, idempotent jobs and may scale independently.

Production installation also needs ingress/TLS, backup/restore verification, managed secret delivery, a network policy tailored to service addresses, and storage sizing. These depend on the chosen cluster and are not implied by a local smoke test.
