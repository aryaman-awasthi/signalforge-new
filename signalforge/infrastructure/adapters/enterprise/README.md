# Enterprise connectors: interface known, runtime not installed here

These five folders hold manifests and no runtime. That is a deliberate, visible
state, not an omission: `availability: available` means SignalForge knows the
interface, the registry lists the adapter, the capability matrix shows what it
would cover, the wizard can show it - and the connector health loop reports any
binding to it as **UNKNOWN** with "no runtime on this node" rather than a green
tick.

The alternative designs were both dishonest. Hiding them would let an evaluation
conclude the platform cannot integrate with Datadog. Shipping a mock runtime
under a `built_in` flag would let a demo show a Datadog integration working while
nothing was ever queried - which is the exact failure mode the capability matrix
exists to prevent (`domain/adapters/matrix.py`: interface-only entries are listed
and never counted as coverage).

Adding a real runtime later is the same operation as installing any adapter: write
the class, register it in `infrastructure/adapters/builtin.py`, and flip
`availability` to `built_in`. Nothing else in the platform changes, and no
manifest field, binding or screen has to be redesigned - which is the claim these
folders are here to make checkable.
