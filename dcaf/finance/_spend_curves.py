"""Pre-defined construction spend curves for capital project financial modeling.

Each curve is a ``SpendSchedule``: a tuple of ``(duration_fraction, spend_fraction)``
breakpoints where ``spend_fraction`` is the fraction of total cost allocated to the
interval starting at ``duration_fraction`` and ending at the next breakpoint.
The final sentinel point is always ``(1.0, 0.0)``.
"""

from types import MappingProxyType

from dcaf.shared.types import SpendSchedule, SpendScheduleName

# NOTE: these off/on switches keep ruff from dictating the formatting of this block
# fmt: off

# Uniform spending rate throughout the construction period.
FLAT_CURVE: SpendSchedule = (
    (0.000, 0.025),
    (0.025, 0.025),
    (0.050, 0.050),
    (0.100, 0.050),
    (0.150, 0.050),
    (0.200, 0.050),
    (0.250, 0.050),
    (0.300, 0.050),
    (0.350, 0.050),
    (0.400, 0.050),
    (0.450, 0.050),
    (0.500, 0.050),
    (0.550, 0.050),
    (0.600, 0.050),
    (0.650, 0.050),
    (0.700, 0.050),
    (0.750, 0.050),
    (0.800, 0.050),
    (0.850, 0.050),
    (0.900, 0.050),
    (0.950, 0.050),
    (1.000, 0.000),
)

# Smooth bell-shaped profile with spending concentrated in the middle.
BELL_CURVE: SpendSchedule = (
    (0.000, 0.00003345755644),
    (0.025, 0.00003345755644),
    (0.050, 0.00006691511288),
    (0.100, 0.00221592420597),
    (0.150, 0.00221592420597),
    (0.200, 0.02699548325659),
    (0.250, 0.02699548325659),
    (0.300, 0.12098536225957),
    (0.350, 0.12098536225957),
    (0.400, 0.19947188697021),
    (0.450, 0.19947188697021),
    (0.500, 0.12098536225957),
    (0.550, 0.12098536225957),
    (0.600, 0.02699548325659),
    (0.650, 0.02699548325659),
    (0.700, 0.00221592420597),
    (0.750, 0.00221592420597),
    (0.800, 0.00006691511288),
    (0.850, 0.00006691511288),
    (0.900, 0.00000074335976),
    (0.950, 0.00000074335976),
    (1.000, 0.00000000000000),
)

# Uniform middle with tapered start and finish.
RAMPED_CURVE: SpendSchedule = (
    (0.000, 0.010000000000),
    (0.025, 0.010000000000),
    (0.050, 0.020000000000),
    (0.100, 0.037500000000),
    (0.150, 0.037500000000),
    (0.200, 0.064166666667),
    (0.250, 0.064166666667),
    (0.300, 0.064166666667),
    (0.350, 0.064166666667),
    (0.400, 0.064166666667),
    (0.450, 0.064166666667),
    (0.500, 0.064166666667),
    (0.550, 0.064166666667),
    (0.600, 0.064166666667),
    (0.650, 0.064166666667),
    (0.700, 0.064166666667),
    (0.750, 0.064166666667),
    (0.800, 0.037500000000),
    (0.850, 0.037500000000),
    (0.900, 0.020000000000),
    (0.950, 0.020000000000),
    (1.000, 0.000000000000),
)

# Spending rises then falls symmetrically, peaking at the midpoint.
TRIANGLE_CURVE: SpendSchedule = (
    (0.000, 0.01),
    (0.025, 0.01),
    (0.050, 0.02),
    (0.100, 0.04),
    (0.150, 0.04),
    (0.200, 0.06),
    (0.250, 0.06),
    (0.300, 0.08),
    (0.350, 0.08),
    (0.400, 0.10),
    (0.450, 0.10),
    (0.500, 0.08),
    (0.550, 0.08),
    (0.600, 0.06),
    (0.650, 0.06),
    (0.700, 0.03),
    (0.750, 0.03),
    (0.800, 0.02),
    (0.850, 0.02),
    (0.900, 0.01),
    (0.950, 0.01),
    (1.000, 0.00),
)

# Spending increases steadily from start to finish.
LINEAR_CURVE: SpendSchedule = (
    (0.000, 0.004545),
    (0.025, 0.004545),
    (0.050, 0.009090),
    (0.100, 0.018180),
    (0.150, 0.018180),
    (0.200, 0.027270),
    (0.250, 0.027270),
    (0.300, 0.036360),
    (0.350, 0.036360),
    (0.400, 0.045500),
    (0.450, 0.045500),
    (0.500, 0.054540),
    (0.550, 0.054540),
    (0.600, 0.063630),
    (0.650, 0.063630),
    (0.700, 0.072720),
    (0.750, 0.072720),
    (0.800, 0.081810),
    (0.850, 0.081810),
    (0.900, 0.090900),
    (0.950, 0.090900),
    (1.000, 0.000000),
)

# Entire spend is booked immediately at construction start.
UPFRONT_CURVE: SpendSchedule = (
    (0.000, 1.000000),
    (1.000, 0.000000),
)

# fmt: on

SPEND_CURVE_REGISTRY: dict[SpendScheduleName, SpendSchedule] = {
    "flat": FLAT_CURVE,
    "bell": BELL_CURVE,
    "ramped": RAMPED_CURVE,
    "triangle": TRIANGLE_CURVE,
    "linear": LINEAR_CURVE,
    "upfront": UPFRONT_CURVE,
}


def get_spend_curve(name: SpendScheduleName) -> SpendSchedule | None:
    """Get the predefined spend curve with the given name.
    Returns None if there is not a curve defined for the provided name.
    """
    return SPEND_CURVE_REGISTRY.get(name)


def get_spend_profile(name: SpendScheduleName) -> SpendSchedule:
    """Return a read-only built-in construction spend profile by name.

    Parameters
    ----------
    name : SpendScheduleName
        Name of the built-in spend profile.

    Returns
    -------
    SpendSchedule
        Immutable breakpoint schedule for the requested profile.

    Raises
    ------
    KeyError
        If ``name`` is not a known built-in profile.

    Examples
    --------
    >>> get_spend_profile("triangle")[:3]
    ((0.0, 0.01), (0.025, 0.01), (0.05, 0.02))
    """
    try:
        return SPEND_CURVE_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown spend profile '{name}'") from exc


def get_spend_profiles() -> MappingProxyType[SpendScheduleName, SpendSchedule]:
    """Return the built-in construction spend profiles as a read-only mapping.

    Returns
    -------
    MappingProxyType[SpendScheduleName, SpendSchedule]
        Read-only mapping of profile names to immutable breakpoint schedules.

    Examples
    --------
    >>> sorted(get_spend_profiles().keys())
    ['bell', 'flat', 'linear', 'ramped', 'triangle', 'upfront']
    """
    return MappingProxyType(dict(SPEND_CURVE_REGISTRY))
