"""The Spring Actuator adapter family (REQ-031).

Two adapters over an endpoint pair that a Spring Boot application with Micrometer
on its classpath already serves. Nothing is asked of the application: no
contract to implement, no controller to write, no dependency to add beyond the
one it almost certainly already has. That is the point of the family - the
observe contract is what an application serves when it wants SignalForge to read
its logs or execute a remediation, and this is what SignalForge can read from an
application that has been asked for nothing at all.

Two adapters rather than one for the same reason the observe set is six: a
binding is per capability set and per credential scope, and the health endpoint
and the metrics endpoint fail independently. A single "actuator" binding would
report a metrics scrape healthy because `/actuator/health` answered.
"""
