.PHONY: setup dev test test-live lint build-web demo-audio

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]" pytest httpx
	cd web && npm ci && npm run build

dev:
	@set -a && [ -f .env ] && . ./.env; set +a; \
	PYTHONPATH=server .venv/bin/uvicorn bside.main:app --port 8000 --reload

test:
	.venv/bin/ruff check server
	.venv/bin/python -m pytest server/tests/unit server/tests/e2e -q

test-live:
	BSIDE_LIVE_B2=1 .venv/bin/python -m pytest server/tests -q

lint:
	.venv/bin/ruff check server --fix

build-web:
	cd web && npm run build

demo-audio:
	.venv/bin/python scripts/make_demo_audio.py
