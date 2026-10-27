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
uv run ruff check .
uv run ruff format .
```

## Architecture

The package root (`dcaf/__init__.py`) exports only the project-level API (`EnergyProject`, `ProjectAnalysis`, contracts, policies). Lower-level primitives live in subpackages under `dcaf/` and are imported from them directly:

- **`streams/`** — Stream primitives: `base.py` (`BaseStream`, `BaseGroup`), `cashflows.py` (`CashFlow`, `CashFlowStream`, `CashFlowGroup`), `generation.py` (`Generation`, `GenerationStream`, `GenerationGroup`, with conversion to cashflows via `to_revenue()` and `to_cost()`)
- **`finance/`** — Financing and cost schedules: `amortization.py`, `construction.py` (spend schedules and financing), `escalation.py`, `opex.py`, `outage.py`
- **`tax/`** — `depreciation.py` (`macrs_schedule()`, `vdb()`, `vdb_schedule()`, IRS rate tables for half-year and mid-quarter conventions), `incentives.py` (`ptc()`, `itc()`, `itc_adjusted_basis()`), `liability.py` (`compute_taxable_income()`, `tax_liability()`)
- **`metrics/`** — Standalone `npv`, `irr`, and `lcoe` functions
- **`shared/`** — `types.py` (`Period`, `DayCountConvention`, `MACRSPropertyClass`, `MACRSConvention`, `ProFormaCategory`, `TaxTreatment`, `SupportsLessThan`, ...), `time.py` (`period_start`, `timedelta_fractional_years`, `compound_factor`, `hours_per_period`, `time_delta_per_period`, ...), `validation.py`
- **`project/`** — The high-level `EnergyProject` builder (`builder.py`), its compiler (`_compiler/`), and `analysis.py`, `contracts.py`, `policies.py`, `timeline.py`, `config.py`

### Core Abstractions

- **`CashFlow`** — Frozen dataclass. Immutable unit representing a single cashflow with `amount` (float), `date`, `label`, `is_cash` (cash vs accrual like depreciation), `pro_forma_category` (`ProFormaCategory` or `None`), and `tax_treatment` (`TaxTreatment`).
- **`CashFlowStream`** — Primary container of `CashFlow` objects (subclass of `BaseStream`). All operations (filter, apply, sort, group) return new instances. Supports fluent chaining: `stream.filter(fn).sort(fn).apply(fn)`.
- **`CashFlowGroup[KeyType]`** — Generic dict-like container mapping keys to `CashFlowStream`s. Produced by `group_by()`, `group_by_pro_forma_category()`, `group_by_tax_treatment()`, `group_by_period()`. Supports `aggregate()`, `apply_to_groups()`, and `ungroup()`.
- **`ProFormaCategory` / `TaxTreatment`** — Enums for categorization. `ProFormaCategory`: REVENUE, OPERATING_COST, CAPITAL_COST, TAX, TAX_CREDIT, DEPRECIATION, FINANCING_PROCEEDS, FINANCING_INTEREST, FINANCING_PRINCIPAL, OTHER. `TaxTreatment`: NONE, TAXABLE, DEDUCTIBLE.
- **`Generation`** — Frozen dataclass for a single generation data point with `amount_mwh`, `label`, and either a legacy `date` or half-open `period_start`/`period_end` bounds.
- **`GenerationStream`** — Container for `Generation` objects mirroring the CashFlowStream pattern. Supports `from_capacity()` factory, `filter()`, `group_by()`, `sum()`, `discounted_sum()`, and conversion methods (`to_revenue()`, `to_cost()`) that produce `CashFlowStream` objects.
- **`GenerationGroup[KeyType]`** — Generic dict-like container mapping keys to `GenerationStream`s. Supports grouping by a key function or by time `period`.
- **`EnergyProject`** — Immutable fluent builder: each configuration method returns a new instance. `analyze()` compiles it into a `ProjectAnalysis`; `cashflows()`, `metrics()`, and `pro_forma()` are shortcuts.
- **`ProjectAnalysis`** — Frozen compiled result of `EnergyProject.analyze()`, exposing the `cashflows` property and the `metrics()` and `pro_forma()` methods.

### Key Design Patterns

- **Immutability**: Value types (`CashFlow`, `Generation`, project config and analysis records) are frozen dataclasses. Streams and groups are plain (non-frozen) dataclasses holding an `entries` list or `groups` dict, but all operations return new instances.
- **Functional composition**: Methods accept callables (`Callable[[CashFlow], ...]`) for customizable transformations, filtering, and grouping.
- **Exhaustive matching**: Uses `match/case` with `assert_never()` instead of catch-all exceptions.
- **Python 3.11 compatible**: `TypeAlias` annotations (`X: TypeAlias = ...`), module-level `TypeVar`s for generics (no PEP 695 `type X = ...` or `def f[T]` syntax), `Literal` types for constrained parameters. Avoid stdlib APIs newer than 3.11 (e.g. `warnings.deprecated`).
- **Day count conventions**: NPV calculations use configurable `DayCountConvention` (`"actual/365-no-leap"`, `"actual/365-fixed"`, or `"actual/actual"`, the default), centralized in `shared.time.timedelta_fractional_years()`.
- **Generation-to-cashflow bridge**: `GenerationStream` converts physical quantities (MWh) to financial cashflows, while tax incentives like PTC are modeled in `tax/incentives.py`.

## Conventions

### Date intervals are half-open `[start, end)`

Every date-bounded interval in the public API uses **inclusive start, exclusive end** semantics — matching Python's `range`, slicing, and `datetime` arithmetic. This applies to:

- `EnergyProject.generation(operations_start, operations_end)` and `ProjectTimeline.operations_end`
- `EnergyProject.construction(construction_start, construction_end)` and `construction_spend_schedule(start_date, end_date)`
- `EnergyProject.construction_outage(start, end)` / `EnergyProject.generation_outage(start, end)`
- `GenerationStream.from_outage(start, end)`, `generator_outage(start, end)`, `construction_outage(start, end)`
- `CashFlowStream.date_range(start, end)`, `GenerationStream.date_range`
- `AmortizationBuilder.interest_free(from_date, to_date)`

Concretely: to model a full calendar year 2026, write `operations_start=date(2026, 1, 1), operations_end=date(2027, 1, 1)`. The end date is the first day **after** the interval. **Do not** use `date(2026, 12, 31)` to mean "last day of 2026" — always use the first day of the following period as the exclusive upper bound.

This convention does **not** apply to integer period indices (e.g., `AmortizationBuilder.interest_free(from_period, to_period)`), which use inclusive bounds on both ends because they select discrete elements rather than spans of time.

When introducing a new date-interval API, follow the half-open convention and document it explicitly in the docstring (`"exclusive end"` or `"half-open [start, end)"`).

## Code Style

- Python 3.11+ supported (`requires-python = ">=3.11"`, ruff `target-version = "py311"`)
- Line length: 100 characters
- Ruff for linting/formatting, mypy for type checking
- Comprehensive type hints throughout, including generics and protocols

## Documentation

- Public-facing functionality must have thorough NumPy-style docstrings.
- Public docstrings should cover purpose, parameters, returns, raised exceptions when relevant, and include examples.
- Private functionality must have concise descriptive docstrings explaining what the code does and any important design decisions affecting its implementation or library use.
- When refactoring or adding APIs, update docstrings as part of the change rather than as a follow-up.

## AI Behavioral Guidelines

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
