.PHONY: run test lint typecheck clean precommit

run:
	python bot_main.py

# Service-free unit suite; live Ollama/MySQL/GPU checks remain opt-in.
test:
	python -m pytest tests/ -x -q --ignore=tests/test_gpu.py --ignore=tests/test_llm.py --ignore=tests/testConnection.py

lint:
	python -m ruff check .

typecheck:
	python -m mypy . --ignore-missing-imports --no-strict-optional

precommit:
	python -m pre_commit run --all-files

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -f kawaii.log
