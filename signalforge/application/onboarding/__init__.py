"""Onboarding use cases: the seven wizard steps as application services.

Split by step rather than by entity, because that is how they are used and how
they fail: registering an application is one transaction, discovery is a fan-out
of unauthenticated probes that writes nothing, a connection test writes only its
own evidence, and the map, topology, governance and baseline steps each write one
section of one profile.
"""
