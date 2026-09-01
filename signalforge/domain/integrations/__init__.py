"""Domain for the data the generic adapters own (REQ-031, REQ-033, REQ-035).

The adapter SPI says what an integration can be asked to do; this package holds
the rules about what it may then say, remember and rank. It exists because three
of the shipped adapters are *simulations of an integrated system* (itsm-lite,
teams-sim, page-sim) and one keeps an index of its own (kb-index): those have
state, and state with rules belongs in the domain rather than inside an HTTP
client.

Nothing here imports a framework, a driver or an adapter runtime.
"""
