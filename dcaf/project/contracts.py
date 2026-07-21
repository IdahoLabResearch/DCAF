# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Generation-linked contract terms for project-level revenue policies."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Literal, Self

from dcaf.shared.types import (
    ProFormaCategory,
    TaxTreatment,
    normalize_cashflow_classification,
)
from dcaf.streams.generation import Generation, GenerationStream

GenerationPriceMode = Literal["fixed", "schedule", "callable"]
ContractQuantityMode = Literal[
    "fraction_of_generation",
    "fixed_mwh_per_generation_event",
    "custom_mwh_generation_schedule",
]
ContractShortfallMode = Literal["error"]


@dataclass(frozen=True, slots=True)
class GenerationSettlementEvent:
    """Context for one generation-linked revenue settlement.

    Attributes
    ----------
    date : date
        Date of the generation event being settled.
    available_mwh : float
        Gross generation available before allocation.
    requested_mwh : float
        Generation requested by the revenue policy.
    delivered_mwh : float
        Generation actually delivered to the revenue component.
    shortfall_mwh : float
        Requested generation that was not delivered.
    allocated_generation_share : float
        Delivered generation divided by available generation.
    component_name : str, optional
        Name of the project component being settled.
    """

    date: date
    available_mwh: float
    requested_mwh: float
    delivered_mwh: float
    shortfall_mwh: float
    allocated_generation_share: float
    component_name: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationPrice:
    """Per-MWh price policy used to settle generation revenue.

    Direct construction and :meth:`fixed`, :meth:`schedule`, and :meth:`callable`
    enforce the same valid price-policy states.

    Attributes
    ----------
    mode : {"fixed", "schedule", "callable"}
        Strategy used to resolve the per-MWh settlement price.
    fixed_price : float, optional
        Constant per-MWh price used when ``mode`` is ``"fixed"``.
    price_schedule : tuple of tuple of date and float, optional
        Exact-date per-MWh prices used when ``mode`` is ``"schedule"``. Each
        priced settlement event must have an entry with the same date; prices
        are not carried forward between entries.
    price_callable : Callable, optional
        Callback used when ``mode`` is ``"callable"``.
    """

    mode: GenerationPriceMode
    fixed_price: float | None = None
    price_schedule: tuple[tuple[date, float], ...] = ()
    price_callable: Callable[[GenerationSettlementEvent], float] | None = None

    def __post_init__(self) -> None:
        """Validate and canonicalize the one active price-resolution strategy."""
        if self.mode not in {"fixed", "schedule", "callable"}:
            raise ValueError("mode must be 'fixed', 'schedule', or 'callable'")

        if self.mode == "fixed":
            if self.fixed_price is None:
                raise ValueError("fixed generation price requires fixed_price")
            if self.price_schedule or self.price_callable is not None:
                raise ValueError("fixed generation price cannot include a schedule or callback")
            price = float(self.fixed_price)
            _validate_finite(price, "fixed_price")
            object.__setattr__(self, "fixed_price", price)
            return

        if self.mode == "schedule":
            if self.fixed_price is not None or self.price_callable is not None:
                raise ValueError(
                    "scheduled generation price cannot include fixed_price or callback"
                )
            entries = tuple((entry_date, float(price)) for entry_date, price in self.price_schedule)
            if not entries:
                raise ValueError("price schedule must contain at least one entry")
            dates: set[date] = set()
            for entry_date, price in entries:
                if not isinstance(entry_date, date):
                    raise TypeError("price schedule dates must be date instances")
                if entry_date in dates:
                    raise ValueError("price schedule dates must be unique")
                dates.add(entry_date)
                _validate_finite(price, "price schedule prices")
            object.__setattr__(self, "price_schedule", tuple(sorted(entries)))
            return

        if self.fixed_price is not None or self.price_schedule:
            raise ValueError("callable generation price cannot include fixed_price or a schedule")
        if not callable(self.price_callable):
            raise ValueError("callable generation price requires a callable callback")

    @classmethod
    def fixed(cls, price: float) -> Self:
        """Return a fixed per-MWh generation price.

        Parameters
        ----------
        price : float
            Constant settlement price per delivered MWh.

        Returns
        -------
        GenerationPrice
            Price policy that applies the same price to every event.

        Raises
        ------
        ValueError
            If ``price`` is not finite.
        """
        return cls(mode="fixed", fixed_price=price)

    @classmethod
    def schedule(cls, price_schedule: Mapping[date, float]) -> Self:
        """Return an exact-date per-MWh generation price schedule.

        Parameters
        ----------
        price_schedule : Mapping[date, float]
            Per-MWh price keyed by generation settlement date. A price applies
            only when its key exactly matches the settlement event date; entries
            are not treated as price-change dates and are never carried forward.
            During analysis, every scheduled date must exist in the compiled
            project generation stream, and every priced settlement event must
            have a corresponding schedule entry.

        Returns
        -------
        GenerationPrice
            Price policy that looks up prices by event date.

        Raises
        ------
        ValueError
            If the schedule is empty, contains a non-finite price, or does not
            contain the exact date of a settlement event being resolved.
        """
        return cls(mode="schedule", price_schedule=tuple(price_schedule.items()))

    @classmethod
    def callable(cls, price_callable: Callable[[GenerationSettlementEvent], float]) -> Self:
        """Return a per-MWh generation price resolved from a settlement callback.

        Parameters
        ----------
        price_callable : Callable[[GenerationSettlementEvent], float]
            Callback invoked for each settlement event.

        Returns
        -------
        GenerationPrice
            Price policy that delegates price resolution to ``price_callable``.
        """
        return cls(mode="callable", price_callable=price_callable)

    def resolve(self, event: GenerationSettlementEvent) -> float:
        """Resolve the per-MWh price for an event.

        Parameters
        ----------
        event : GenerationSettlementEvent
            Settlement event being priced.

        Returns
        -------
        float
            Per-MWh settlement price.

        Raises
        ------
        ValueError
            If the configured price state is incomplete, the event date is
            missing from a schedule, or a callable returns a non-finite price.
        """
        match self.mode:
            case "fixed":
                if self.fixed_price is None:
                    raise ValueError("fixed generation price is missing")
                return self.fixed_price
            case "schedule":
                price_by_date = dict(self.price_schedule)
                if event.date not in price_by_date:
                    raise ValueError(f"price schedule has no entry for {event.date.isoformat()}")
                return price_by_date[event.date]
            case "callable":
                if self.price_callable is None:
                    raise ValueError("callable generation price is missing")
                price = float(self.price_callable(event))
                if not isfinite(price):
                    raise ValueError("generation price must be finite")
                return price


@dataclass(frozen=True, slots=True)
class EnergyContract:
    """Contract terms for generation-linked revenue.

    Use :meth:`fraction_of_generation`, :meth:`fixed_mwh_per_generation_event`,
    or :meth:`custom_mwh_generation_schedule` to construct a valid contract.

    Attributes
    ----------
    quantity_mode : {"fraction_of_generation", "fixed_mwh_per_generation_event",
        "custom_mwh_generation_schedule"}
        Strategy used to request generation from each eligible event.
    price : GenerationPrice
        Per-MWh settlement price policy.
    start : date, optional
        Inclusive contract start date.
    end : date, optional
        Exclusive contract end date.
    label : str
        Label applied to generated revenue cashflows.
    shortfall : {"error"}
        Shortfall handling mode. Currently only ``"error"`` is supported.
    generation_share : float, optional
        Gross generation fraction requested when ``quantity_mode`` is
        ``"fraction_of_generation"``.
    amount_mwh : float, optional
        Fixed MWh requested per generation event when ``quantity_mode`` is
        ``"fixed_mwh_per_generation_event"``.
    requested_generation : GenerationStream, optional
        Explicit requested MWh by unique date when ``quantity_mode`` is
        ``"custom_mwh_generation_schedule"``.
    pro_forma_category : ProFormaCategory or str or None
        Presentation category for generated cashflows.
    tax_treatment : TaxTreatment or str
        Tax classification for generated cashflows.
    """

    quantity_mode: ContractQuantityMode
    price: GenerationPrice
    start: date | None = None
    end: date | None = None
    label: str = "Contract Revenue"
    shortfall: ContractShortfallMode = "error"
    generation_share: float | None = None
    amount_mwh: float | None = None
    requested_generation: GenerationStream | None = None
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE
    tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE

    def __post_init__(self) -> None:
        if not isinstance(self.price, GenerationPrice):
            raise TypeError("price must be a GenerationPrice")
        _validate_contract_dates(self.start, self.end)
        if self.shortfall != "error":
            raise ValueError("shortfall must be 'error'")
        category, treatment = normalize_cashflow_classification(
            self.pro_forma_category,
            self.tax_treatment,
        )
        object.__setattr__(self, "pro_forma_category", category)
        object.__setattr__(self, "tax_treatment", treatment)
        self._validate_quantity()

    @classmethod
    def fraction_of_generation(
        cls,
        *,
        generation_share: float,
        price: GenerationPrice,
        start: date | None = None,
        end: date | None = None,
        label: str = "Contract Revenue",
        shortfall: ContractShortfallMode = "error",
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE,
        tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE,
    ) -> Self:
        """Create a contract that requests a fraction of eligible generation.

        Parameters
        ----------
        generation_share : float
            Fraction of each eligible generation event requested by the
            contract. Must be between ``0.0`` and ``1.0``.
        price : GenerationPrice
            Per-MWh settlement price policy.
        start : date, optional
            Inclusive contract start date.
        end : date, optional
            Exclusive contract end date.
        label : str, optional
            Label applied to generated revenue cashflows. Default is
            ``"Contract Revenue"``.
        shortfall : {"error"}, optional
            Shortfall handling mode. Currently only ``"error"`` is supported.
        pro_forma_category : ProFormaCategory or str or None, optional
            Presentation category for generated cashflows.
        tax_treatment : TaxTreatment or str, optional
            Tax classification for generated cashflows.

        Returns
        -------
        EnergyContract
            Contract configured with fraction-of-generation quantity terms.
        """
        return cls(
            quantity_mode="fraction_of_generation",
            price=price,
            start=start,
            end=end,
            label=label,
            shortfall=shortfall,
            generation_share=float(generation_share),
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
        )

    @classmethod
    def fixed_mwh_per_generation_event(
        cls,
        *,
        amount_mwh: float,
        price: GenerationPrice,
        start: date | None = None,
        end: date | None = None,
        label: str = "Contract Revenue",
        shortfall: ContractShortfallMode = "error",
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE,
        tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE,
    ) -> Self:
        """Create a contract that requests fixed MWh per eligible generation event.

        Parameters
        ----------
        amount_mwh : float
            Fixed generation quantity requested from each eligible generation event.
        price : GenerationPrice
            Per-MWh settlement price policy.
        start : date, optional
            Inclusive contract start date.
        end : date, optional
            Exclusive contract end date.
        label : str, optional
            Label applied to generated revenue cashflows. Default is
            ``"Contract Revenue"``.
        shortfall : {"error"}, optional
            Shortfall handling mode. Currently only ``"error"`` is supported.
        pro_forma_category : ProFormaCategory or str or None, optional
            Presentation category for generated cashflows.
        tax_treatment : TaxTreatment or str, optional
            Tax classification for generated cashflows.

        Returns
        -------
        EnergyContract
            Contract configured with fixed-MWh-per-generation-event quantity terms.
        """
        return cls(
            quantity_mode="fixed_mwh_per_generation_event",
            price=price,
            start=start,
            end=end,
            label=label,
            shortfall=shortfall,
            amount_mwh=float(amount_mwh),
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
        )

    @classmethod
    def custom_mwh_generation_schedule(
        cls,
        *,
        requested_generation: GenerationStream,
        price: GenerationPrice,
        start: date | None = None,
        end: date | None = None,
        label: str = "Contract Revenue",
        shortfall: ContractShortfallMode = "error",
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE,
        tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE,
    ) -> Self:
        """Create a contract that requests explicit per-date generation amounts.

        Parameters
        ----------
        requested_generation : GenerationStream
            Requested contract quantity by generation event date. During analysis,
            each date must match exactly one compiled project generation event and
            its requested MWh must not exceed that event's available generation.
        price : GenerationPrice
            Per-MWh settlement price policy.
        start : date, optional
            Inclusive contract start date.
        end : date, optional
            Exclusive contract end date.
        label : str, optional
            Label applied to generated revenue cashflows. Default is
            ``"Contract Revenue"``.
        shortfall : {"error"}, optional
            Shortfall handling mode. Currently only ``"error"`` is supported.
        pro_forma_category : ProFormaCategory or str or None, optional
            Presentation category for generated cashflows.
        tax_treatment : TaxTreatment or str, optional
            Tax classification for generated cashflows.

        Returns
        -------
        EnergyContract
            Contract configured with custom scheduled generation quantity terms.

        Raises
        ------
        ValueError
            If requested generation contains duplicate dates.
        """
        return cls(
            quantity_mode="custom_mwh_generation_schedule",
            price=price,
            start=start,
            end=end,
            label=label,
            shortfall=shortfall,
            requested_generation=requested_generation,
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
        )

    def requested_mwh_for(self, generation: Generation) -> float:
        """Return requested MWh for one available generation event.

        Parameters
        ----------
        generation : Generation
            Available generation event.

        Returns
        -------
        float
            Requested MWh for the event, or ``0.0`` when the event is outside
            the contract term or otherwise ineligible.

        Raises
        ------
        ValueError
            If the contract quantity configuration is incomplete.
        """
        if not self.includes(generation.date):
            return 0.0
        if generation.amount_mwh < 0.0:
            return 0.0
        match self.quantity_mode:
            case "fraction_of_generation":
                if self.generation_share is None:
                    raise ValueError("fraction-of-generation contract is missing generation_share")
                return generation.amount_mwh * self.generation_share
            case "fixed_mwh_per_generation_event":
                if self.amount_mwh is None:
                    raise ValueError("fixed-MWh contract is missing amount_mwh")
                return self.amount_mwh
            case "custom_mwh_generation_schedule":
                if self.requested_generation is None:
                    raise ValueError(
                        "custom generation schedule contract is missing requested_generation"
                    )
                requested_by_date: dict[date, float] = {}
                for entry in self.requested_generation:
                    requested_by_date[entry.date] = requested_by_date.get(entry.date, 0.0) + (
                        entry.amount_mwh
                    )
                return requested_by_date.get(generation.date, 0.0)

    def includes(self, event_date: date) -> bool:
        """Return whether an event date is eligible under the contract term.

        Parameters
        ----------
        event_date : date
            Generation event date.

        Returns
        -------
        bool
            ``True`` when ``event_date`` is within the inclusive-start,
            exclusive-end contract term.
        """
        if self.start is not None and event_date < self.start:
            return False
        if self.end is not None and event_date >= self.end:
            return False
        return True

    def _validate_quantity(self) -> None:
        if self.quantity_mode not in {
            "fraction_of_generation",
            "fixed_mwh_per_generation_event",
            "custom_mwh_generation_schedule",
        }:
            raise ValueError(
                "quantity_mode must be 'fraction_of_generation', "
                "'fixed_mwh_per_generation_event', or 'custom_mwh_generation_schedule'"
            )

        match self.quantity_mode:
            case "fraction_of_generation":
                if self.amount_mwh is not None or self.requested_generation is not None:
                    raise ValueError(
                        "fraction-of-generation contract cannot include amount_mwh or "
                        "requested_generation"
                    )
                if self.generation_share is None:
                    raise ValueError("generation_share is required")
                if not isfinite(self.generation_share) or not 0.0 <= self.generation_share <= 1.0:
                    raise ValueError("generation_share must be between 0 and 1")
            case "fixed_mwh_per_generation_event":
                if self.generation_share is not None or self.requested_generation is not None:
                    raise ValueError(
                        "fixed-MWh contract cannot include generation_share or requested_generation"
                    )
                if self.amount_mwh is None:
                    raise ValueError("amount_mwh is required")
                _validate_finite(self.amount_mwh, "amount_mwh")
                if self.amount_mwh < 0.0:
                    raise ValueError("amount_mwh must be non-negative")
            case "custom_mwh_generation_schedule":
                if self.generation_share is not None or self.amount_mwh is not None:
                    raise ValueError(
                        "custom generation schedule contract cannot include generation_share or "
                        "amount_mwh"
                    )
                if self.requested_generation is None:
                    raise ValueError("requested_generation is required")
                requested_dates: set[date] = set()
                for entry in self.requested_generation:
                    if entry.date in requested_dates:
                        raise ValueError("requested_generation dates must be unique")
                    requested_dates.add(entry.date)
                    _validate_finite(entry.amount_mwh, "requested_generation amounts")
                    if entry.amount_mwh < 0.0:
                        raise ValueError("requested_generation amounts must be non-negative")


def _validate_finite(value: float, name: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


def _validate_contract_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be after start")


__all__ = [
    "GenerationPrice",
    "GenerationSettlementEvent",
    "EnergyContract",
]
