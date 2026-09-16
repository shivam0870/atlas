.PHONY: bootstrap start stop status check test e2e eval experiments benchmark observability
bootstrap:
	python3 scripts/bootstrap.py
start:
	python3 scripts/local.py start
stop:
	python3 scripts/local.py stop
status:
	python3 scripts/local.py status
check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/mypy src
	cd web && npm run build
test:
	.venv/bin/pytest -q
e2e:
	cd web && npm run test:e2e
eval:
	.venv/bin/python scripts/evaluate.py
experiments:
	.venv/bin/python scripts/evaluate.py --experiments
benchmark:
	.venv/bin/python scripts/benchmark.py
observability:
	docker-compose -f compose.yaml -f compose.observability.yaml up -d collector prometheus tempo grafana
