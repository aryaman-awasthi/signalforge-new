"""Push-mode adapters: SignalForge is the server (REQ-031, REQ-032).

A push adapter has no outbound call to make. Its whole job is `normalize` - one
pure function turning whatever an alerting or deployment system posts into the
canonical model - which is why these are the most heavily tested adapters in the
set for the least code: a normaliser that silently mislabels a severity is a
misled responder at 3am.

For a push binding, a healthy connector means "SignalForge is mounted and
accepting posts for this binding". Whether anything has actually arrived is
freshness, which the ingest API records (S3-T03) and the integration health
screen shows beside the direction.
"""
