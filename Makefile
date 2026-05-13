PYTHON ?= python
PIP    ?= $(PYTHON) -m pip

.PHONY: help install dev test lint fmt format check run run-mock run-live clean

help:
	@echo "TopicForge — common developer tasks"
	@echo ""
	@echo "  make install     Install runtime dependencies (editable)"
	@echo "  make dev         Install runtime + dev dependencies (editable)"
	@echo "  make test        Run the pytest suite"
	@echo "  make lint        Run ruff lint"
	@echo "  make fmt         Run ruff format"
	@echo "  make check       Lint + tests (CI bundle)"
	@echo "  make run-mock    Run the MCP server in mock mode (stdio)"
	@echo "  make run-live    Run the MCP server in live mode (stdio)"
	@echo "  make clean       Remove caches and build artifacts"

install:
	$(PIP) install -e .

dev:
	$(PIP) install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests

fmt format:
	$(PYTHON) -m ruff format src tests

check: lint test

run run-mock:
	TOPICFORGE_MODE=mock $(PYTHON) -m topicforge

run-live:
	TOPICFORGE_MODE=live $(PYTHON) -m topicforge

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
