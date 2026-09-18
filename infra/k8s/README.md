# Kubernetes deployment template

These resources are a legacy deployment template. They have not been deployed to a cluster as part of the local performance run. They also predate the F01–F14 account and upload upgrade: identity credentials, the MFA key, public origin/SMTP configuration and shared persistent upload storage still need to be integrated. Do not apply them as a complete deployment of the current product. Use [the current deployment guide](../../DEPLOYMENT.md) to choose and verify a hosting path.

1. Provision PostgreSQL 16 with pgvector, durable Redis (noeviction), separate cache Redis (allkeys-lru), an OTLP collector, and a local Ollama service. Give them the service names in `configmap.yaml`, or edit those endpoints.
2. Build and publish an immutable app image; replace `atlas-rag-api:latest` with its digest. For a local kind cluster, load the built image with `kind load docker-image atlas-rag-api:latest`.
3. Create the namespace. Create the three Secrets privately using `secrets.example.yaml` as a schema; do not apply its placeholder values. The API receives no migration credentials.
4. Run the migration Job and wait for completion before applying the Deployments, Service, and HPA.
5. Download the Qwen model into the Ollama service. The init containers download only the two pinned CPU retrieval models.
6. After adding the current account configuration and persistent storage, access the configured HTTPS origin and register/verify an individual account. Browser API-key login has been retired; integration keys are for service clients.

The HPA requires metrics-server. Scaling API replicas does not multiply the capacity of a single inference server. Each API process admits one generation at a time; size the inference tier separately. Redis coordinates rate limits and circuit state across replicas. Workers use leased, idempotent jobs and may scale independently.

Production installation also needs ingress/TLS, backup/restore verification, managed secret delivery, a network policy tailored to service addresses, and storage sizing. These depend on the chosen cluster and are not implied by a local smoke test.
