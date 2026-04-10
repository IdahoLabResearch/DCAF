"""Defines helpful fixtures for running doctests in the streams directory."""
from datetime import date
import pytest

from dcaf.streams import CashFlow, CashFlowStream, Generation, GenerationStream
from dcaf.shared.types import ProFormaCategory

@pytest.fixture(autouse=True)
def create_streams(request, doctest_namespace):
    """Create basic stream objects that can be referenced in doctests."""
    module_name = request.module.__name__

    if "cashflows" in module_name:
        # CashFlowStreams
        stream = CashFlowStream(
            [CashFlow(100, date(2024, 1, 1), pro_forma_category=ProFormaCategory.REVENUE)]
        )
        doctest_namespace["stream"] = stream
        doctest_namespace["stream_in_thousands"] = stream

        # Cashflows
        cf = CashFlow(100.0, date(2027, 1, 1))
        doctest_namespace["cf1"] = cf
        doctest_namespace["cf2"] = cf
        doctest_namespace["cf3"] = cf

        yield
        doctest_namespace.clear()
    elif "generation" in module_name:
        # GenerationStreams
        stream = GenerationStream([Generation(1000.0, date(2027, 1, 1), source="unit_1")])
        doctest_namespace["stream"] = stream
        doctest_namespace["gen_stream"] = stream

        yield
        doctest_namespace.clear()
