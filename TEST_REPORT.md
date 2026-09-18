# Atlas verification report

> This is the historical technical-release report. See [UPGRADE_RELEASE.md](UPGRADE_RELEASE.md) for the F01–F14 product upgrade and current executed verification.

Executed locally on 15–16 September 2026. Reference environment: Apple M1 Pro, 16 GiB RAM, macOS 26.5.2; native Ollama; PostgreSQL/Redis/collector in a shared Colima VM with 2 CPUs and 2 GiB RAM. All generation and embedding use local models; external model API cost is zero.

## Result and scope

All five implementation phases and the responsive console are present. The native app has passed backend and real-browser end-to-end checks. **This is not a claim that every original acceptance criterion is complete:** owner-reviewed corpus evaluation, complete container inference, remote CI, and cluster deployment are still outstanding.

Open http://127.0.0.1:8100 and choose **Acme Engineering**. The seeded documents include public Kubernetes documentation and a clearly synthetic private runbook. Northstar has separate private demonstration data.

## Executed automated checks

| Check | Executed result | Evidence |
|---|---|---|
| Backend tests against PostgreSQL/Redis and local embeddings | 35 passed | [JUnit report](artifacts/tests.xml) |
| Real Chrome browser journeys | 5 passed in the recorded completed run | [Summary](artifacts/browser-summary.json) |
| Ruff lint and formatting | Passed; 59 Python files formatted | `make check` |
| Mypy | Passed; 20 source modules | `make check` |
| TypeScript and production Vite build | Passed | `make check` |
| Human-review import guard | Refused the current unreviewed export before opening the database, as intended | `scripts/import_reviewed_labels.py` |

### Backend coverage

- Forced PostgreSQL row-level security without application tenant filters; cross-tenant writes; non-superuser runtime roles without RLS bypass; concurrent authenticated tenant reads.
- Missing, malformed, expired, and revoked keys; scope denial; cross-tenant source denial; cookie mutation protection.
- Atomic sliding-window limits, HTTP 429/Retry-After, monthly reservation concurrency/idempotency, HTTP 402, and uncertain-usage retention.
- Exact and opt-in semantic cache isolation, collection revision invalidation, cache outage fallback, and unavailable rate-enforcement rejection.
- Chunk boundaries and exact source offsets; fusion math; citation mapping; real embeddings; idempotent ingestion; no-evidence abstention.
- Atomic document/job/outbox creation, queue backpressure rollback, deletion during embedding, expired worker leases, duplicate delivery, and dead-letter exhaustion.
- Bounded agent steps, malformed planner output, tool failure, duplicate calls, and combining evidence types.
- Transient error classification and Retry-After parsing; primary failure and fallback; no retry after streaming begins; circuit opening, one half-open probe, and recovery.
- Evaluation metric math, review-hash invalidation, rejection of unreviewed labels, and deliberately degraded synthetic regression-gate failure.

### Browser journeys

1. Workspace login, dashboard, source preview, evaluation/settings navigation, and a 390-pixel mobile viewport without horizontal overflow.
2. A live answer with Acme's private release code and citation; switching to Northstar returns its separate code.
3. Raw-text upload, source inspection, and deletion.
4. File upload, waiting for completed indexing, asking about the new content, opening the inline citation and highlighted passage, then deleting the test document.
5. Switching workspaces during an active answer cancels it and clears answer/source state.

Screenshots: [overview](artifacts/atlas-overview.png), [answer and sources](artifacts/atlas-answer.png), [mobile](artifacts/atlas-mobile.png). Private browser output stays ignored because future failure diagnostics could contain session data.

## Real integrations and fault exercises

| Exercise | Result / practical limit | Evidence |
|---|---|---|
| MCP client SDK | Initialized, listed three tools, and called tenant-scoped tools in both workspaces | [MCP run](artifacts/mcp-smoke.json) |
| Local LangGraph agent | Combined inventory and document evidence; answered replicas, team, and release code with citations | [Agent run](artifacts/agent-smoke.json) |
| Actual generation failover | Forced unavailable primary; local llama.cpp produced the correct cited code; no external API call | [Failover run](artifacts/failover-smoke.json) |
| Local judge | Supported fixture scored 1, contradicted fixture scored 0 after prompt correction; only two synthetic cases, not calibration | [Retest](artifacts/judge-smoke.json), [retained initial failure](artifacts/judge-smoke-initial.json) |
| OTLP export | Collector received actual agent/query spans including tokens and zero API cost | [Request trace](artifacts/request-trace.json), [export check](artifacts/telemetry-export.json) |
| Docker image | Built; non-root runtime, health, readiness, UI, auth, and native-inference connectivity passed; uncached query timed out | [Partial container check](artifacts/container-smoke.json) |

The judge initially treated “synthetic Juniper” and “Juniper” as different entities. The corrected prompt permits an unambiguous shortened name. This small test shows why a local judge must not be treated as calibrated truth.

The packaged API's uncached query exceeded its 180-second client timeout while the shared 2-GiB VM became unresponsive. The temporary Atlas API/model containers were stopped; unrelated services and data volumes were preserved. Native inference remains the verified local route. Retest complete Docker inference with sufficient VM memory before claiming clean-clone Compose acceptance.

## Measured retrieval and load

The [README](README.md#executed-synthetic-engineering-experiments) contains all four requested experiment types with actual numbers: three chunk sizes, vector versus hybrid, reranking on/off, and three top-k values. The [raw artifact](artifacts/engineering-experiments.json) includes per-query results for **12 authored synthetic fixtures**. These do not replace the human-reviewed corpus evaluation.

The [versioned k6 run](artifacts/benchmarks/20260915T163153Z) records separate cached, uncached, and ingestion workloads. Query runs lasted 30 seconds after warmup and used a short release-code answer:

- Cached: 553 completed requests, 18.43 requests/s, p95 504.26 ms, zero observed HTTP/answer errors. **47 scheduled iterations were dropped** at the configured virtual-user ceiling; the full 20 requests/s offered load was not sustained.
- Uncached: 29 completed requests, 0.93 requests/s, p95 2,222.52 ms, zero observed HTTP/answer errors.
- Indexing: 40 unique documents / 80 chunks fully indexed in 58.11 seconds, **41.30 documents/minute**. HTTP acceptance latency is measured separately.
- Cache hit rate was 100% for the deliberately warmed repeated question and 0% with cache disabled. These are workload-specific rates, not organic usage estimates.
- External model API spend was $0. Hardware, electricity, and operator time are excluded. No hypothetical paid-provider dollar savings are claimed.

These dated measurements precede the final full-response HTTP trace timing and upload queue backpressure changes. They characterize that recorded build/workload, not every later build or production capacity.

## Remaining acceptance work

1. **Owner review:** all 50 candidate labels remain unreviewed. Review them in Evaluation, run development/held-out evaluation and the sweeps, then accept a measured regression baseline. Current corpus recall/faithfulness and a committed accepted threshold are intentionally pending.
2. **Calibration:** relevance floor, semantic-cache threshold, and local judge need independent quality review. Semantic reuse is disabled by default.
3. **Deployment:** complete container inference needs a suitably provisioned VM. Kubernetes templates have not been deployed or load-tested. Grafana/Tempo configuration is supplied; the optional dashboard stack was not browser-tested.
4. **Remote CI:** workflows are committed configuration; no remote green run is claimed. The corpus regression workflow explicitly skips until a human-reviewed baseline is accepted.
5. **Longer operational validation:** sustained mixed-question load, backup/restore, cluster shutdown/rollout behavior, and external TLS/network policies remain deployment work.

## Reproduce locally

```sh
make start
make check
make test
make e2e
.venv/bin/python scripts/mcp_smoke.py
.venv/bin/python scripts/agent_smoke.py
make benchmark
```

The failover script requires the separately configured local llama.cpp server. `make eval` intentionally rejects the current unreviewed corpus labels. See the README for setup, review, and deployment instructions.
