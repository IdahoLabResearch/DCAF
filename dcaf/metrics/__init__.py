"""Financial metric calculations.

Standalone functions for NPV, IRR, and LCOE that serve as the single
source of truth for these calculations across the library.
"""

from dcaf.metrics.irr import irr
from dcaf.metrics.lcoe import brent_root, lcoe
from dcaf.metrics.npv import npv

__all__ = ["brent_root", "irr", "lcoe", "npv"]
