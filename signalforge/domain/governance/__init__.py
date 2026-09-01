"""Governance and safe execution: the consequential-action boundary (S6).

Pure domain. Nothing here imports FastAPI, SQLAlchemy or a vendor SDK, nothing
holds a clock, and nothing calls a model. The platform safety floor lives in
`policy.py` as code rather than configuration, which is what makes "a high-risk
action can never auto-execute" a property of the system instead of a setting.
"""
