"""Detection: steady state to a corroborated, human-gated proposal (S4-T01).

Everything in this package is pure. It takes what was measured - rollups, probe
results, alerts, a topology snapshot and the application's own onboarding
profile - and returns a verdict. It performs no I/O, holds no clock of its own
and reads no configuration: the same inputs always produce the same verdict,
which is what makes a proposal explainable to the human who has to decide on it.

The pipeline, in the order `engine.evaluate` runs it:

    rollups  -> rolling baseline -> threshold -> breach -> sustained anomaly
             -> SLO burn (fast proposes, slow opens Watch)
             -> corroboration (2+ independent families, one customer-facing)
             -> health score (worst region wins)
             -> severity proposal from observed impact
             -> stance: Steady, Watch or Proposed

The last line is the important one. This package proposes; it never declares.
Declaration, severity change and closure are human acts (REQ-043), and there is
no code path here that transitions an incident.
"""
