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
        self._sim_link_tx_to_12v()

    def _make_interface(self, hv_test_mode=False):
        return SimulatedLIFUInterface(num_modules=self._sim_num_modules)

    def _sim_link_tx_to_12v(self):
        """Drop the simulated TX off 'USB' while 12 V is off, as real hardware does."""
        hv = self.interface.hvcontroller
        tx = self.interface.txdevice
        turn_12v_on, turn_12v_off = hv.turn_12v_on, hv.turn_12v_off

        def on():
            result = turn_12v_on()
            tx.emit_connected()
            return result

        def off():
            result = turn_12v_off()
            tx.emit_disconnected()
            return result

        hv.turn_12v_on, hv.turn_12v_off = on, off
