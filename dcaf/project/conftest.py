# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Defines helpful fixtures for running doctests in the project directory."""

from datetime import date
import pytest

from dcaf.streams import CashFlowStream, CashFlowGroup
from dcaf.streams.generation import GenerationStream
from dcaf.project import ProjectAnalysis
from dcaf.project.timeline import ProjectTimeline


@pytest.fixture(autouse=True)
def create_project(doctest_namespace):
    """Create a basic project that can be referred to in doctests"""
    doctest_namespace["project"] = ProjectAnalysis(
        timeline=ProjectTimeline(
            date(2030, 1, 1),
            date(2035, 1, 1),
            date(2075, 1, 1),
        ),
        valuation=None,
        generation=GenerationStream(),
        cashflow_components=CashFlowGroup({}),
        taxable_income=CashFlowStream([]),
        taxes=CashFlowStream([]),
        tax_rate=None,
    )

    yield
    doctest_namespace.clear()
