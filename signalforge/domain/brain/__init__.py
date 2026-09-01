"""The Incident Brain: a versioned, provenance-carrying record of reasoning.

Pure domain. Nothing here imports FastAPI, SQLAlchemy or a vendor SDK, nothing
here holds a clock, and nothing here knows a model id - a call site names a
`ModelClass` and configuration decides the rest (CLAUDE.md, Environment).
"""
