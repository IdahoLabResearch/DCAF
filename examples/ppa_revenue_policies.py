# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Model multiple PPAs and merchant remainder revenue with ``GenerationPrice``.

The fixed-volume PPA buys 250 MWh from every generation event at a custom
exact-date price schedule. The fraction PPA buys 50% of each event at a callable
price that uses settlement context. All unallocated generation is sold through a
fixed-price remainder policy.

``GenerationPrice.schedule`` requires a price for the exact date of every
settlement it prices. Schedule entries are not carried forward.
"""

from datetime import date
from math import isclose

from dcaf import EnergyContract, EnergyProject, GenerationPrice, GenerationSettlementEvent
from dcaf.streams import Generation, GenerationStream


GENERATION_BY_DATE = {
    date(2026, 12, 31): 1_000.0,
    date(2027, 12, 31): 1_200.0,
    date(2028, 12, 31): 900.0,
}
FIXED_VOLUME_MWH = 250.0
FRACTION_PPA_SHARE = 0.50
FIXED_VOLUME_PRICE_BY_DATE = {
    date(2026, 12, 31): 52.00,
    date(2027, 12, 31): 53.50,
    date(2028, 12, 31): 55.00,
}
MERCHANT_PRICE_PER_MWH = 38.00


def fraction_ppa_price(event: GenerationSettlementEvent) -> float:
    """Return an escalating price with a high-volume delivery discount."""
    contract_year = event.date.year - 2026
    escalated_price = 46.00 * 1.02**contract_year
    volume_discount = 1.00 if event.delivered_mwh >= 500.0 else 0.00
    return escalated_price - volume_discount


def main() -> None:
    """Build the project, verify generation allocation, and print revenue."""
    generation = GenerationStream(
        [
            Generation(amount_mwh, event_date, label=f"{event_date.year} generation")
            for event_date, amount_mwh in GENERATION_BY_DATE.items()
        ]
    )

    analysis = (
        EnergyProject()
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:fixed_volume_ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=FIXED_VOLUME_MWH,
                price=GenerationPrice.schedule(FIXED_VOLUME_PRICE_BY_DATE),
                start=date(2026, 1, 1),
                end=date(2029, 1, 1),
                label="Fixed-volume PPA",
            ),
        )
        .generation_revenue_contract(
            name="revenue:fraction_ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=FRACTION_PPA_SHARE,
                price=GenerationPrice.callable(fraction_ppa_price),
                start=date(2026, 1, 1),
                end=date(2029, 1, 1),
                label="Fraction PPA",
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(MERCHANT_PRICE_PER_MWH),
            label="Merchant remainder",
        )
        .analyze()
    )

    fixed_volume_revenue = {
        flow.date: flow.amount for flow in analysis.cashflow_components["revenue:fixed_volume_ppa"]
    }
    fraction_revenue = {
        flow.date: flow.amount for flow in analysis.cashflow_components["revenue:fraction_ppa"]
    }
    merchant_revenue = {
        flow.date: flow.amount for flow in analysis.cashflow_components["revenue:merchant"]
    }

    print("Date         Gross MWh  Fixed PPA  Fraction PPA  Remainder")
    for event_date, gross_mwh in GENERATION_BY_DATE.items():
        fixed_volume_mwh = FIXED_VOLUME_MWH
        fraction_mwh = gross_mwh * FRACTION_PPA_SHARE
        remainder_mwh = gross_mwh - fixed_volume_mwh - fraction_mwh

        assert remainder_mwh > 0.0
        assert isclose(fixed_volume_mwh + fraction_mwh + remainder_mwh, gross_mwh)

        fraction_event = GenerationSettlementEvent(
            date=event_date,
            available_mwh=gross_mwh,
            requested_mwh=fraction_mwh,
            delivered_mwh=fraction_mwh,
            shortfall_mwh=0.0,
            allocated_generation_share=FRACTION_PPA_SHARE,
            component_name="revenue:fraction_ppa",
        )
        assert isclose(
            fixed_volume_revenue[event_date],
            fixed_volume_mwh * FIXED_VOLUME_PRICE_BY_DATE[event_date],
        )
        assert isclose(
            fraction_revenue[event_date],
            fraction_mwh * fraction_ppa_price(fraction_event),
        )
        assert isclose(
            merchant_revenue[event_date],
            remainder_mwh * MERCHANT_PRICE_PER_MWH,
        )

        print(
            f"{event_date}  {gross_mwh:9,.0f}  {fixed_volume_mwh:9,.0f}  "
            f"{fraction_mwh:12,.0f}  {remainder_mwh:9,.0f}"
        )

    print("\nRevenue by component")
    for name in ("revenue:fixed_volume_ppa", "revenue:fraction_ppa", "revenue:merchant"):
        print(f"{name:>28}: ${analysis.cashflow_components[name].sum():,.2f}")


if __name__ == "__main__":
    main()
