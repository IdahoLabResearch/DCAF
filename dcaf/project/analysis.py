"""Analysis result types for project-level workflows."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from math import isclose, isfinite
from os import PathLike
from typing import TYPE_CHECKING

from dcaf.shared.types import DayCountConvention, Period, ProFormaCategory, TaxTreatment

from dcaf.project.config import CapitalStructure
from dcaf.project.timeline import ProjectTimeline

if TYPE_CHECKING:
    from dcaf.streams.cashflows import CashFlowGroup, CashFlowStream
    from dcaf.streams.generation import GenerationStream


def _validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is not finite (inf or NaN)."""
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class ProjectMetrics:
    """Summary metrics for a compiled project analysis.

    Attributes
    ----------
    valuation_date : date
        Reference date used for discounting.
    discount_rate : float
        Discount rate applied to compute NPV and levelized cost.
    npv : float
        Net present value of all cash flows.
    xirr : float or None
        Internal rate of return, or ``None`` when the cash-only project stream
        does not admit a solvable IRR through :meth:`CashFlowStream.irr`.
    total_cash : float
        Undiscounted sum of all cash flows.
    total_generation : float
        Total energy generated across all periods, in MWh.
    discounted_generation : float
        Present value of generation (MWh discounted at ``discount_rate``).
    levelized_cost : float or None
        Levelized cost of energy (LCOE) in $/MWh, or ``None`` when
        ``discounted_generation`` is zero.
    """

    valuation_date: date
    discount_rate: float
    npv: float
    xirr: float | None
    total_cash: float
    total_generation: float
    discounted_generation: float
    levelized_cost: float | None

    def __str__(self) -> str:
        lines = [
            f"Valuation Date: {self.valuation_date.isoformat()}",
            f"Discount Rate: {self.discount_rate:.6f}",
            f"NPV: {self.npv:.6f}",
            f"Total Cash: {self.total_cash:.6f}",
            f"Total Generation: {self.total_generation:.6f}",
            f"Discounted Generation: {self.discounted_generation:.6f}",
            f"LCOE/Levelized Cost: {self.levelized_cost:.6f}"
            if self.levelized_cost is not None
            else "LCOE/Levelized Cost: n/a",
            f"XIRR: {self.xirr:.6f}" if self.xirr is not None else "XIRR: n/a",
        ]
        return "\n".join(lines)


@dataclass(frozen=True)
class ProjectProFormaRow:
    """One row in a project pro-forma table.

    Attributes
    ----------
    name : str
        Row identifier (e.g. ``"Revenues"`` or ``"default:revenue"``).
    amounts : tuple of float
        Aggregated amounts aligned to the pro-forma period sequence.
    """

    name: str
    amounts: tuple[float, ...]


@dataclass(frozen=True)
class ProjectProForma:
    """Period-based pro-forma for a compiled project analysis.

    Attributes
    ----------
    period : Period
        Aggregation frequency (e.g. ``"year"`` or ``"month"``).
    periods : tuple of date
        Ordered period start dates covering all modeled cash flows.
    rows : tuple of ProjectProFormaRow
        Summary rows followed by underlying component detail rows.

    Examples
    --------
    >>> pf = project.pro_forma()
    >>> for row in pf.rows:
    ...     print(row.name, row.amounts)
    Revenues (...)
    Operating Costs (...)
    EBITDA (...)
    Depreciation (...)
    EBIT (...)
    Taxes (...)
    Tax Credits (...)
    Capital Costs (...)
    Free Cash Flow to the Firm (...)
    Financing Interest (...)
    Interest Tax Shield (...)
    Financing Principal (...)
    Free Cash Flow to Equity (...)
    """

    period: Period
    periods: tuple[date, ...]
    rows: tuple[ProjectProFormaRow, ...]

    def row_map(self) -> dict[str, tuple[float, ...]]:
        """Return the pro-forma rows as a name-to-amounts mapping.

        Returns
        -------
        dict[str, tuple[float, ...]]
            Dictionary keyed by row name where each value is the tuple of
            amounts aligned to :attr:`periods`.

        Examples
        --------
        >>> pf = project.pro_forma()
        >>> pf.row_map()["Free Cash Flow to Equity"]
        (...)
        """
        return {row.name: row.amounts for row in self.rows}

    def to_csv(self, path: str | PathLike[str]) -> None:
        """Write the pro-forma table to a CSV file.

        Parameters
        ----------
        path : str or os.PathLike[str]
            Destination file path. Existing files are overwritten.

        Notes
        -----
        The output uses a header row containing ``"Row"`` followed by ISO date
        strings for each pro-forma period. Each subsequent row contains the row
        name and the corresponding numeric amounts in period order.

        Examples
        --------
        >>> pf = project.pro_forma()
        >>> pf.to_csv("pro_forma.csv")
        """
        with open(path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Row", *[dt.isoformat() for dt in self.periods]])
            for row in self.rows:
                writer.writerow([row.name, *row.amounts])

    def __str__(self) -> str:
        header = ["Row", *[dt.isoformat() for dt in self.periods]]
        lines = ["\t".join(header)]
        for row in self.rows:
            values = [f"{amount:.6f}" for amount in row.amounts]
            lines.append("\t".join([row.name, *values]))
        return "\n".join(lines)


@dataclass(frozen=True)
class ProjectAnalysis:
    """Compiled output of an ``EnergyProject``.

    Produced by :meth:`EnergyProject.analyze`. Holds all intermediate and final
    quantities needed to compute metrics, build pro-formas, and inspect
    individual cash-flow components.

    Attributes
    ----------
    name : str
        Project name inherited from the builder.
    timeline : ProjectTimeline
        Timeline assumptions used during analysis.
    capital_structure : CapitalStructure or None
        Resolved capital structure (with tax rate filled in when available).
    generation_by_asset : dict[str, GenerationStream]
        Physical generation for each named asset.
    cashflow_components : CashFlowGroup[str]
        All named cash-flow components, including taxes.
    taxable_income : CashFlowStream
        Aggregate taxable income stream (revenues minus deductible costs).
    taxes : CashFlowStream
        Tax liability stream (empty when no tax rate is configured).
    tax_rate : float or None
        Project tax rate used to compute :attr:`taxes`.
    """

    name: str
    timeline: ProjectTimeline
    capital_structure: CapitalStructure | None
    generation_by_asset: dict[str, GenerationStream]
    cashflow_components: CashFlowGroup[str]
    taxable_income: CashFlowStream
    taxes: CashFlowStream
    tax_rate: float | None

    @property
    def generation(self) -> GenerationStream:
        """Return the combined generation stream across all assets.

        Returns
        -------
        GenerationStream
            Single stream containing every generation entry from
            :attr:`generation_by_asset`.
        """
        from dcaf.streams.generation import GenerationStream

        return GenerationStream.from_streams(*self.generation_by_asset.values())

    @property
    def cashflows(self) -> CashFlowStream:
        """Return all project cash flows as a single sorted stream.

        Returns
        -------
        CashFlowStream
            Union of all entries in :attr:`cashflow_components`, sorted by
            date using :meth:`CashFlowStream.sort`.
        """
        return self.cashflow_components.ungroup().sort()

    def metrics(
        self,
        discount_rate: float | None = None,
        valuation_date: date | None = None,
        convention: DayCountConvention = "actual/365",
    ) -> ProjectMetrics:
        """Compute summary metrics for the project analysis.

        Parameters
        ----------
        discount_rate : float, optional
            Rate used for NPV and LCOE calculations. Defaults to the project
            WACC when a capital structure is configured.
        valuation_date : date, optional
            Reference date for discounting. Defaults to the construction start
            date or the earliest cash-flow date.
        convention : DayCountConvention, optional
            Day count convention for fractional-year calculations.
            Default is ``"actual/365"``.

        Returns
        -------
        ProjectMetrics
            NPV, XIRR, total cash, generation totals, and LCOE.

        Raises
        ------
        ValueError
            If ``discount_rate`` cannot be resolved or ``valuation_date``
            cannot be inferred.
        """
        effective_rate = self._discount_rate(discount_rate)
        effective_valuation_date = self._valuation_date(valuation_date)
        total_stream = self.cashflows
        cash_only = total_stream.cash_only()
        generation = self.generation
        discounted_generation = (
            generation.discounted_sum(
                rate=effective_rate,
                valuation_date=effective_valuation_date,
                convention=convention,
            )
            if generation.entries
            else 0.0
        )
        levelized_cost = None
        if discounted_generation > 0.0:
            cost_flows = total_stream.filter(
                lambda flow: flow.pro_forma_category
                in (ProFormaCategory.OPERATING_COST, ProFormaCategory.CAPITAL_COST)
            ).cash_only()
            levelized_cost = (
                -cost_flows.outflows().npv(
                    rate=effective_rate,
                    valuation_date=effective_valuation_date,
                    convention=convention,
                )
                / discounted_generation
            )
        try:
            project_irr = cash_only.irr(convention=convention)
        except ValueError:
            project_irr = None
        return ProjectMetrics(
            valuation_date=effective_valuation_date,
            discount_rate=effective_rate,
            npv=cash_only.npv(
                rate=effective_rate,
                valuation_date=effective_valuation_date,
                convention=convention,
            ),
            xirr=project_irr,
            total_cash=cash_only.sum(),
            total_generation=generation.sum(),
            discounted_generation=discounted_generation,
            levelized_cost=levelized_cost,
        )

    def summary(
        self,
        discount_rate: float | None = None,
        valuation_date: date | None = None,
        convention: DayCountConvention = "actual/365",
    ) -> ProjectMetrics:
        """Return summary metrics for the project analysis.

        This method is a backward-compatible alias for :meth:`metrics`.

        Parameters
        ----------
        discount_rate : float, optional
            Rate used for NPV and LCOE calculations. Defaults to the project
            WACC when a capital structure is configured.
        valuation_date : date, optional
            Reference date for discounting. Defaults to the construction start
            date or the earliest cash-flow date.
        convention : DayCountConvention, optional
            Day count convention for fractional-year calculations.
            Default is ``"actual/365"``.

        Returns
        -------
        ProjectMetrics
            NPV, XIRR, total cash, generation totals, and LCOE.

        Raises
        ------
        ValueError
            If ``discount_rate`` cannot be resolved or ``valuation_date``
            cannot be inferred.
        """
        return self.metrics(
            discount_rate=discount_rate,
            valuation_date=valuation_date,
            convention=convention,
        )

    def pro_forma(self, period: Period = "year") -> ProjectProForma:
        """Build a grouped period-aggregated pro-forma table.

        Parameters
        ----------
        period : Period, optional
            Aggregation frequency. Default is ``"year"``.

        Returns
        -------
        ProjectProForma
            Pro-forma with grouped summary rows followed by component detail rows.
        """
        from dcaf.streams.cashflows import CashFlowStream

        revenues = self._cashflows_for_category(ProFormaCategory.REVENUE)
        operating_costs = self._cashflows_for_category(ProFormaCategory.OPERATING_COST)
        depreciation = self._cashflows_for_category(ProFormaCategory.DEPRECIATION)
        taxes = self._cashflows_for_category(ProFormaCategory.TAX)
        tax_credits = self._cashflows_for_category(ProFormaCategory.TAX_CREDIT)
        capital_costs = self._cashflows_for_category(ProFormaCategory.CAPITAL_COST)
        financing_interest = self._cashflows_for_category(ProFormaCategory.FINANCING_INTEREST)
        financing_principal = self._cashflows_for_category(ProFormaCategory.FINANCING_PRINCIPAL)
        interest_tax_shield = self._interest_tax_shield()
        shield_adjustment = interest_tax_shield.apply(
            lambda flow: flow.replace(
                amount=-flow.amount,
                label=f"Unlevered {flow.label}",
                pro_forma_category=None,
            )
        )
        ebitda = CashFlowStream.from_streams(revenues, operating_costs)
        ebit = CashFlowStream.from_streams(ebitda, depreciation)
        free_cash_flow_to_firm = CashFlowStream.from_streams(
            revenues,
            operating_costs,
            taxes,
            tax_credits,
            capital_costs,
            shield_adjustment,
        )
        free_cash_flow_to_equity = CashFlowStream.from_streams(
            free_cash_flow_to_firm,
            financing_interest,
            financing_principal,
            interest_tax_shield,
        )

        summary_streams = (
            ("Revenues", revenues),
            ("Operating Costs", operating_costs),
            ("EBITDA", ebitda),
            ("Depreciation", depreciation),
            ("EBIT", ebit),
            ("Taxes", taxes),
            ("Tax Credits", tax_credits),
            ("Capital Costs", capital_costs),
            ("Free Cash Flow to the Firm", free_cash_flow_to_firm),
            ("Financing Interest", financing_interest),
            ("Interest Tax Shield", interest_tax_shield),
            ("Financing Principal", financing_principal),
            ("Free Cash Flow to Equity", free_cash_flow_to_equity),
        )

        summary_totals = [
            (name, self._totals_by_period(stream, period)) for name, stream in summary_streams
        ]
        detail_totals = [
            (name, self._totals_by_period(stream, period))
            for name, stream in self.cashflow_components.items()
            if not name.startswith("project:")
        ]

        period_keys = {dt for _name, totals in (*summary_totals, *detail_totals) for dt in totals}
        periods = tuple(sorted(period_keys))

        rows = [
            ProjectProFormaRow(
                name=name,
                amounts=tuple(totals.get(dt, 0.0) for dt in periods),
            )
            for name, totals in summary_totals
        ]
        rows.extend(
            ProjectProFormaRow(
                name=name,
                amounts=tuple(totals.get(dt, 0.0) for dt in periods),
            )
            for name, totals in detail_totals
        )
        return ProjectProForma(period=period, periods=periods, rows=tuple(rows))

    def _cashflows_for_category(
        self,
        category: ProFormaCategory,
    ) -> CashFlowStream:
        return self.cashflows.filter(pro_forma_category=category).sort()

    def _totals_by_period(
        self,
        stream: CashFlowStream,
        period: Period,
    ) -> dict[date, float]:
        if not stream.entries:
            return {}
        return stream.group_by(period=period).aggregate(lambda grouped: grouped.sum())

    def _interest_tax_shield(self) -> CashFlowStream:
        from dcaf.streams.cashflows import CashFlow, CashFlowStream
        from dcaf.tax.liability import compute_taxable_income, tax_liability

        if self.tax_rate is None:
            return CashFlowStream()

        deductible_interest = self.cashflows.filter(
            pro_forma_category=ProFormaCategory.FINANCING_INTEREST,
            tax_treatment=TaxTreatment.DEDUCTIBLE,
        )
        if not deductible_interest.entries:
            return CashFlowStream()

        taxable_revenue = self.cashflows.filter(tax_treatment=TaxTreatment.TAXABLE)
        deductions_without_interest = self.cashflows.filter(
            lambda flow: (
                flow.tax_treatment is TaxTreatment.DEDUCTIBLE
                and flow.pro_forma_category is not ProFormaCategory.FINANCING_INTEREST
            )
        )
        taxable_income_without_interest = compute_taxable_income(
            taxable_revenue,
            deductions_without_interest,
            label="Taxable Income Before Interest",
        )
        taxes_without_interest = tax_liability(
            taxable_income_without_interest,
            tax_rate=self.tax_rate,
            label="Taxes Before Interest",
        )
        actual_by_date = self.taxes.group_by(lambda flow: flow.date).aggregate(lambda s: s.sum())
        hypothetical_by_date = taxes_without_interest.group_by(lambda flow: flow.date).aggregate(
            lambda s: s.sum()
        )
        shield_dates = tuple(sorted(set(actual_by_date) | set(hypothetical_by_date)))
        entries = []
        for shield_date in shield_dates:
            shield_amount = actual_by_date.get(shield_date, 0.0) - hypothetical_by_date.get(
                shield_date,
                0.0,
            )
            if isclose(shield_amount, 0.0):
                continue
            entries.append(
                CashFlow(
                    amount=shield_amount,
                    date=shield_date,
                    label="Interest Tax Shield",
                    is_cash=True,
                    pro_forma_category=None,
                    tax_treatment=TaxTreatment.NONE,
                )
            )
        return CashFlowStream(entries)

    def _discount_rate(self, rate: float | None) -> float:
        if rate is not None:
            _validate_finite(rate, "discount_rate")
            return rate
        if self.capital_structure is not None:
            try:
                return self.capital_structure.wacc
            except ValueError as exc:
                raise ValueError(
                    "discount_rate is required when capital_structure.wacc cannot be resolved; "
                    "set a tax rate on the capital structure or pass discount_rate explicitly"
                ) from exc
        raise ValueError("discount_rate is required when capital_structure is not configured")

    def _valuation_date(self, valuation_date: date | None) -> date:
        if valuation_date is not None:
            return valuation_date
        if self.timeline.construction_start is not None:
            return self.timeline.construction_start
        if self.cashflows.entries:
            return min(flow.date for flow in self.cashflows.entries)
        raise ValueError("valuation_date is required when the project has no dated cashflows")


__all__ = [
    "ProjectAnalysis",
    "ProjectMetrics",
    "ProjectProForma",
    "ProjectProFormaRow",
]
