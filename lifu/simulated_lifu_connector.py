"""Subclass of :class:`LIFUConnector` backed by an in-memory simulator.

Activated via the ``--simulate[=N]`` CLI flag in :mod:`main`. Replaces
the real ``LIFUInterface`` with :class:`SimulatedLIFUInterface` while
leaving every other code path of the connector intact.
"""

from __future__ import annotations

import logging

from lifu.lifu_connector import LIFUConnector
from lifu.simulated_interface import SimulatedLIFUInterface

logger = logging.getLogger(__name__)


class SimulatedLIFUConnector(LIFUConnector):
    """Drop-in connector that talks to a :class:`SimulatedLIFUInterface`."""

    def __init__(self, num_modules: int = 1):
        self._sim_num_modules = max(1, int(num_modules))
        logger.info(
            "SimulatedLIFUConnector: initializing with %d simulated TX module(s)",
            self._sim_num_modules,
        )
        super().__init__(hv_test_mode=False)

    def _make_interface(self, hv_test_mode=False):
        return SimulatedLIFUInterface(num_modules=self._sim_num_modules)
