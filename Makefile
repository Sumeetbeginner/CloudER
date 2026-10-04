.PHONY: install check test run worker
install:
	python -m pip install -c requirements.lock -e '.[dev]'
check:
	ruff check src tests scripts
	ruff format --check src tests scripts
	mypy
	node --check src/clouder/static/app.js
	npx prettier --check src/clouder/static tests/console.e2e.mjs
test:
	pytest --cov=clouder --cov-fail-under=85
run:
	clouder serve
worker:
	clouder worker
