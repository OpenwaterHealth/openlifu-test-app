# ---------------------------------------------------------------------------
# Shared constants for lifu_connector and its mixin modules.
# ---------------------------------------------------------------------------

# State machine constants
STATE_DISCONNECTED  = 0  # No TX connected
STATE_TX_CONNECTED  = 1  # TX connected, not yet configured
STATE_CONFIGURED    = 2  # TX programmed with beam parameters
STATE_READY         = 3  # Configured + HV/Console connected
STATE_RUNNING       = 4  # Sonication in progress

# Lookup tables
RGB_STATE_NAMES = {0: "Off", 1: "Red", 2: "Blue", 3: "Green"}
