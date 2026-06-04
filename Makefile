.PHONY: run test lint typecheck clean precommit

run:
	python bot_main.py

test:
	python -m pytest tests/ -x -q --ignore=tests/test_gpu.py --ignore=tests/test_llm.py --ignore=tests/testConnection.py

lint:
	/tmp/kawaii_venv/bin/ruff check .

typecheck:
	/tmp/kawaii_venv/bin/mypy . --ignore-missing-imports --no-strict-optional

precommit:
	/tmp/kawaii_venv/bin/pre-commit run --all-files

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -f kawaii.log
