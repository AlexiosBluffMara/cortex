.PHONY: help install dev test test-unit test-integration lint type ci clean

help:
	@echo "Cortex — make targets"
	@echo ""
	@echo "  install     Install runtime deps + cortex (editable)"
	@echo "  dev         Install dev deps (pytest, ruff, mypy, pre-commit)"
	@echo "  test        Run the full test suite"
	@echo "  test-unit   Run unit tests only (fast, no GPU)"
	@echo "  test-integration   Run integration tests (needs Ollama)"
	@echo "  lint        Run ruff check"
	@echo "  type        Run mypy"
	@echo "  ci          Run lint + type + test-unit (what CI runs)"
	@echo "  clean       Remove build artifacts and caches"

install:
	pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
	pip install -e ".[dev]"

dev:
	pip install -e ".[dev]"
	pre-commit install

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v --tb=short

test-integration:
	pytest tests/integration/ -v -m "not gpu and not tribe"

lint:
	ruff check .

type:
	mypy cortex hermes cli --ignore-missing-imports

ci: lint type test-unit

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
