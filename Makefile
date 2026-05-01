# Red Team Kitchen monorepo — Cortex (brain analysis) + Mercury agent (packages/mercury)
# GCP infra: gcp/  |  TRIBE v2 weights: tribev2_weights/ (gitignored, CC-BY-NC 4.0)
#
# Prerequisites: Python 3.11+, uv, git, ffmpeg
# GPU targets require RTX 5090 + TRIBE weights + Ollama

VENV    := C:/Users/soumi/cortex/.venv
PY      := $(VENV)/Scripts/python
PYTEST  := $(VENV)/Scripts/pytest
RUFF    := $(VENV)/Scripts/ruff
MYPY    := $(VENV)/Scripts/mypy
UV      := uv
PORT    := 8765

.PHONY: help install dev test test-unit test-integration test-e2e \
        lint type fmt ci serve serve-reload mercury-serve \
        submodule-init clean release

help:
	@echo "Cortex / Red Team Kitchen monorepo"
	@echo ""
	@echo "  install           Install runtime + dev deps (uv pip install -e .[dev])"
	@echo "  dev               Install dev deps + pre-commit hooks"
	@echo "  test / test-unit  Run unit tests (fast, no GPU, no Ollama)"
	@echo "  test-integration  Run integration tests (needs Ollama)"
	@echo "  test-e2e          Run end-to-end tests"
	@echo "  lint              ruff check ."
	@echo "  type              mypy cortex hermes cli"
	@echo "  fmt               ruff format ."
	@echo "  ci                lint + type + test-unit  (CI gate)"
	@echo "  serve             Start webapp on :$(PORT)"
	@echo "  serve-reload      Start webapp with --reload"
	@echo "  mercury-serve     Start mercury agent gateway"
	@echo "  submodule-init    git submodule update --init --recursive"
	@echo "  clean             Remove build artifacts and caches"
	@echo "  release VERSION=v0.2.0   Tag + push; fires .github/workflows/release.yml"

install:
	$(UV) pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
	$(UV) pip install -e ".[dev]"

dev:
	$(UV) pip install -e ".[dev]"
	$(VENV)/Scripts/pre-commit install

test: test-unit

test-unit:
	$(PYTEST) tests/unit/ -v --tb=short -m "not gpu and not ollama and not tribe and not network"

test-integration:
	$(PYTEST) tests/integration/ -v -m "not gpu and not tribe"

test-e2e:
	$(PYTEST) tests/e2e/ -v --tb=short

lint:
	$(RUFF) check .

fmt:
	$(RUFF) format .

type:
	$(MYPY) cortex hermes cli --ignore-missing-imports

ci: lint type test-unit

serve:
	$(PY) -m uvicorn webapp.server:app --host 0.0.0.0 --port $(PORT)

serve-reload:
	$(PY) -m uvicorn webapp.server:app --host 0.0.0.0 --port $(PORT) --reload

mercury-serve:
	cd packages/mercury && $(PY) cli.py

submodule-init:
	git submodule update --init --recursive

clean:
	rm -rf build dist cortex.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -not -path "./.venv/*" -delete 2>/dev/null || true

# Cut a public release. Usage:  make release VERSION=v0.2.0
# Tags the current HEAD and pushes the tag; .github/workflows/release.yml then
# builds the GHCR image and uploads bootstrap scripts + RELEASE-NOTES.md to
# the GitHub Release. Tag must match v*.*.* (with optional -rcN suffix).
release:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make release VERSION=v0.2.0"; exit 1; \
	fi
	@case "$(VERSION)" in \
	  v[0-9]*.[0-9]*.[0-9]*) ;; \
	  *) echo "VERSION must look like v0.2.0 (got: $(VERSION))"; exit 1 ;; \
	esac
	git tag -a "$(VERSION)" -m "Cortex $(VERSION)"
	git push origin "$(VERSION)"
	@echo ""
	@echo "Tag $(VERSION) pushed. Watch the release workflow:"
	@echo "  https://github.com/AlexiosBluffMara/cortex/actions/workflows/release.yml"
	@echo "Once it finishes, the release will be at:"
	@echo "  https://github.com/AlexiosBluffMara/cortex/releases/tag/$(VERSION)"
