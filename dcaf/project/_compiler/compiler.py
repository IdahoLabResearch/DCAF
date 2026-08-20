# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""ProjectCompiler: the orchestrator that assembles a full project analysis."""

from __future__ import annotations

from dataclasses import dataclass

from dcaf.project._builder_config import ProjectConfig
from dcaf.project._compiler import construction, debt, generation, opex, revenue, tax, validation
from dcaf.project._compiler.context import (
    AnalysisContext,
    ComponentAccumulator,
    infer_levelized_cost_escalation_rate,
)
from dcaf.project.analysis import ProjectAnalysis
from dcaf.streams.cashflows import CashFlowGroup


@dataclass(frozen=True, slots=True)
class ProjectCompiler:
    """Compile an immutable project configuration into a project analysis."""

    context: AnalysisContext

    @classmethod
    def from_config(cls, config: ProjectConfig) -> ProjectCompiler:
        """Build a compiler with a resolved analysis context."""
        return cls(AnalysisContext(config))

    @property
    def config(self) -> ProjectConfig:
        """Return the resolved config used by compile helpers."""
        return self.context.config

    def compile(self) -> ProjectAnalysis:
        """Compile all project configuration into a :class:`ProjectAnalysis`.

        Each domain module builds its own component stream(s) and, for
        multi-instance domains (fixed opex, variable cost, construction
        outages), registers them directly into ``accumulator`` under their
        named component key. This method is orchestration only: it sequences
        domain calls, then hands the accumulated ``{component_key: stream}``
        map to `tax.compute_project_taxes` to derive taxable income and
        the final project-wide tax liability before assembling the result.
        """
        context = self.context
        accumulator = ComponentAccumulator()
        levelized_revenue_basis = ComponentAccumulator()

        validation.validate_generation_revenue_configuration(context)
        validation.validate_component_keys(context)
        generation_stream = generation.build_generation(context)

        built_construction = construction.build_construction(context)
        accumulator.add("construction", built_construction.total)

        accumulator.add("revenue", revenue.build_revenue(context, generation_stream))
        levelized_revenue_basis.add(
            "revenue", revenue.build_revenue_basis(context, generation_stream)
        )

        for name, stream in revenue.build_generation_linked_policy_streams(
            context, generation_stream
        ):
            accumulator.add(name, stream)

        opex.build_fixed_opex_components(context, accumulator)
        opex.build_variable_cost_components(context, generation_stream, accumulator)
        construction.build_construction_outage_components(context, generation_stream, accumulator)

        accumulator.add("itc", tax.build_itc(context, built_construction.total))
        accumulator.add("ptc", tax.build_ptc(context, generation_stream))
        accumulator.add("depreciation", tax.build_depreciation(context, built_construction.total))

        debt_proceeds_stream = debt.build_debt_proceeds(context, built_construction)
        accumulator.add("debt_proceeds", debt_proceeds_stream)
        accumulator.add("debt_service", debt.build_debt(context, debt_proceeds_stream))

        for name, stream in self.config.custom_cashflows.items():
            accumulator.add_custom(name, stream)

        taxable_income, taxes = tax.compute_project_taxes(context, accumulator.streams)
        accumulator.add("project:tax_liability", taxes)

        return ProjectAnalysis(
            timeline=context.timeline,
            valuation=self.config.valuation,
            generation=generation_stream,
            cashflow_components=CashFlowGroup(accumulator.streams),
            taxable_income=taxable_income,
            taxes=taxes,
            tax_rate=self.config.tax_rate,
            levelized_revenue_basis=CashFlowGroup(levelized_revenue_basis.streams),
            levelized_cost_escalation_rate=infer_levelized_cost_escalation_rate(context),
            tax_allow_refund=self.config.tax_allow_refund,
        )
