"""Defines helpful fixtures for running doctests in the project directory."""
from datetime import date
import pytest

from dcaf.streams import CashFlowStream, CashFlowGroup
from dcaf.project import ProjectAnalysis, ProjectTimeline

@pytest.fixture(autouse=True)
def create_project(doctest_namespace):
    """Create a basic project that can be referred to in doctests"""
    doctest_namespace["project"] = ProjectAnalysis(
        "project",
        timeline=ProjectTimeline(date(2030, 1, 1), date(2035, 1, 1), date(2075, 1, 1),),
        capital_structure=None,
        generation_by_asset={},
        cashflow_components=CashFlowGroup({}),
        taxable_income=CashFlowStream([]),
        taxes=CashFlowStream([]),
        tax_rate=None,
    )

    yield
    doctest_namespace.clear()
