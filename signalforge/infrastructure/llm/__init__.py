"""The one implementation of the model path (CLAUDE.md, Environment).

Deliberately exports nothing: `infrastructure.config` reads `hosts` while the
provider reads `infrastructure.config`, so a re-exporting package module would
be a circular import waiting for its first user. Import the module you mean.
"""
