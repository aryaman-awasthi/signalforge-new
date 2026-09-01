"""The http-observe adapter set (REQ-031).

Six adapters over the one frozen contract in `contracts/observe.py`. Any
application that serves those paths with those shapes is fully observable and
partly controllable by SignalForge with no application-specific code anywhere in
the platform - which is the whole plug-and-play claim, made concrete.

They are six adapters rather than one because a binding is per capability set
and per credential scope: reading an application's logs and being allowed to
restart it are different permissions, held as different credentials, with
different blast radii. One "observe" adapter would have had to offer remediation
to everyone who wanted metrics.
"""
