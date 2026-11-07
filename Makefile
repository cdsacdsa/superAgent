.PHONY: install test lint run demo worker sandbox-image smoke bicep

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[azure,dev]"

test:
	pytest -q

lint:
	ruff check src tests

run:            ## API + in-process worker (uses .env)
	uvicorn superagent.api.app:app --reload --port 8000

demo:           ## offline demo: no API keys, fake models, mock ITSM with sample tickets
	SA_FAST_PROVIDER=fake SA_EXPERT_PROVIDER=fake SA_ITSM_PROVIDER=mock SA_SANDBOX_PROVIDER=mock \
	SA_STORE_PROVIDER=memory SA_POLL_ENABLED=true uvicorn superagent.api.app:app --port 8000

worker:
	python -m superagent.worker

sandbox-image:
	docker build -t superagent-sandbox:latest sandbox/

smoke:
	./scripts/smoke-test.sh http://localhost:8000

bicep:
	az bicep build --file infra/main.bicep --stdout > /dev/null
