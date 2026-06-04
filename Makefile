.PHONY: run test lint typecheck clean

run:
	python bot_main.py

test:
	python -m unittest discover tests/ -v

lint:
	ruff check .

typecheck:
	mypy .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -f kawaii.log
