# Stock A

PARAMS = {
    "A": {"spread": 6, "sweep_edge": 1, "order_size": 5, "pe": 1150}
}

# Position limits (based on competition risk limits)
MAX_POSITION = 100  # placeholder, update when they release limits
MAX_ORDER_SIZE = 40

# General
QUOTE_REFRESH_INTERVAL = 5  # seconds between re-quoting if nothing changed


# Stock B / options

B_STRIKES = [950, 1000, 1050]
B_BOX_PAIRS = [(950, 1000), (1000, 1050), (950, 1050)]

B_PARITY_THRESHOLD = 3.0
B_BOX_THRESHOLD = 3.0

B_PARITY_ORDER_SIZE = 3
B_BOX_ORDER_SIZE = 3

B_SIGNAL_COOLDOWN = 1.0  # seconds between repeated same signal
B_MAX_GROSS_FAMILY = 80

B_MAX_POSITION = {
    "B": 40,
    "B_C_950": 20,
    "B_C_1000": 20,
    "B_C_1050": 20,
    "B_P_950": 20,
    "B_P_1000": 20,
    "B_P_1050": 20,
}