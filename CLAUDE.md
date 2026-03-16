# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

DCAF (Discounted Cash Flow Analysis Framework) is a financial engineering library for nuclear energy project cost modeling. It provides NPV/DCF/IRR calculations, generation tracking, financing cost modeling, tax/incentive analysis (PTC, ITC, MACRS), and pro-forma generation, focused on Extended Power Uprate (EPU) projects.

## Commands

```bash
# Install dependencies (uses uv package manager)
uv sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/unit/test_cashflow_stream.py

# Run a single test by name
uv run pytest -k "test_name"

# Run tests in parallel
uv run pytest -n auto

# Type checking
uv run mypy dcaf/

# Linting and formatting
uv run ruff check dcaf/
uv run ruff format dcaf/
```

## Architecture

The public API is re-exported from `dcaf/__init__.py`. Business logic is organized across modules in `dcaf/core/`:

- **`cashflows.py`** — Core cashflow abstractions (CashFlow, CashFlowStream, CashFlowGroup, CashFlowTags)
- **`generation.py`** — Physical energy generation tracking (Generation, GenerationStream, GenerationGroup) with conversion to cashflows via `to_revenue()` and `to_cost()`
- **`depreciation.py`** — Depreciation schedules (`macrs_schedule()`, `vdb()`, `vdb_schedule()`, IRS rate tables for half-year and mid-quarter conventions)
- **`types.py`** — Shared type aliases (`Period`, `DayCountConvention`, `MACRSPropertyClass`, `MACRSConvention`, `SupportsLessThan`)
- **`utils.py`** — Shared utilities (`period_start`, `timedelta_fractional_years`, `compound_factor`, `hours_per_period`, `time_delta_per_period`)

### Core Abstractions

- **`CashFlow`** — Frozen dataclass. Immutable unit representing a single cashflow with `amount` (float), `date`, `label`, `is_cash` (cash vs accrual like depreciation), and `tags` (frozenset of `CashFlowTags`).
- **`CashFlowStream`** — Primary container of `CashFlow` objects. All operations (filter, apply, sort, group) return new instances. Supports fluent chaining: `stream.filter(fn).sort(fn).apply(fn)`.
- **`CashFlowGroup[KeyType]`** — Generic dict-like container mapping keys to `CashFlowStream`s. Produced by `group_by()`, `group_by_tag()`, `group_by_period()`. Supports `aggregate()`, `apply_to_groups()`, and `ungroup()`.
- **`CashFlowTags`** — Enum for categorization: REVENUE, EXPENSE, TAXABLE, TAX_DEDUCTIBLE, CAPEX, OPEX, DEPRECIATION.
- **`Generation`** — Frozen dataclass for a single generation data point with `amount_mwh`, `date`, `source`, `carrier`, and `label`.
- **`GenerationStream`** — Container for `Generation` objects mirroring the CashFlowStream pattern. Supports `from_capacity()` factory, `filter()`, `group_by()`, `sum()`, `discounted_sum()`, and conversion methods (`to_revenue()`, `to_cost()`) that produce `CashFlowStream` objects.
- **`GenerationGroup[KeyType]`** — Generic dict-like container mapping keys to `GenerationStream`s. Supports grouping by `source`, `carrier`, or `period`.

### Key Design Patterns

- **Immutability**: All data classes are frozen. All operations return new instances.
- **Functional composition**: Methods accept callables (`Callable[[CashFlow], ...]`) for customizable transformations, filtering, and grouping.
- **Exhaustive matching**: Uses `match/case` with `assert_never()` instead of catch-all exceptions.
- **Modern Python 3.13+**: PEP 695 type aliases (`type X = ...`), generic syntax on methods (`group_by[KeyType]`), `Literal` types for constrained parameters.
- **Day count conventions**: NPV calculations use configurable `DayCountConvention` (currently `"actual/365"`), centralized in `utils.timedelta_fractional_years()`.
- **Generation-to-cashflow bridge**: `GenerationStream` converts physical quantities (MWh) to financial cashflows, while tax incentives like PTC are modeled in `tax_incentives.py`.

## Code Style

- Python 3.13+ required
- Line length: 100 characters
- Ruff for linting/formatting, mypy for type checking
- Comprehensive type hints throughout, including generics and protocols

## Documentation

- Public-facing functionality must have thorough NumPy-style docstrings.
- Public docstrings should cover purpose, parameters, returns, raised exceptions when relevant, and include examples.
- Private functionality must have concise descriptive docstrings explaining what the code does and any important design decisions affecting its implementation or library use.
- When refactoring or adding APIs, update docstrings as part of the change rather than as a follow-up.
