"""S2-T05: artifacts 07 and 10 exist in full and stay true (REQ-020..025).

Documents rot when nothing checks them. These tests pin the two claims that
would matter if they stopped being true: that the data-design document is
generated from the live models rather than typed by hand, and that the component
architecture still describes the components the code actually has.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


@pytest.mark.req("REQ-020")
def test_component_architecture_is_authored_not_a_stub():
    text = (DOCS / "07-component-architecture.md").read_text(encoding="utf-8")
    assert "STUB" not in text
    for section in [
        "## 1. Layering",
        "## 2. The event backbone",
        "## 3. Time",
        "## 4. Observability",
        "## 5. Process model",
        "## 6. Web",
    ]:
        assert section in text, f"missing {section}"
    assert "```mermaid" in text, "the layering and bus diagrams are the point"


@pytest.mark.req("REQ-021", "REQ-022")
def test_component_architecture_states_the_bus_guarantees():
    text = (DOCS / "07-component-architecture.md").read_text(encoding="utf-8")
    for claim in [
        "at-least-once",
        "in **one** transaction",
        "blake2b(partition_key) % 16",
        "dead_letters",
        "DeadLetterService",
    ]:
        assert claim in text, f"the bus contract must name {claim!r}"


@pytest.mark.req("REQ-023", "REQ-024")
def test_data_design_is_generated_from_the_live_models():
    from signalforge.infrastructure.persistence.models import Base

    text = (DOCS / "10-data-design.md").read_text(encoding="utf-8")
    assert "STUB" not in text
    assert "scripts/gen_erd.py" in text, "the ERD must be generated, never handwritten"

    for table in Base.metadata.tables:
        assert f"`{table}`" in text, (
            f"table {table} is missing from docs/10 - run make erd"
        )
    assert "erDiagram" in text


@pytest.mark.req("REQ-023")
def test_data_design_records_the_rules_a_diagram_cannot_show():
    text = (DOCS / "10-data-design.md").read_text(encoding="utf-8")
    for claim in [
        "Retention planes",
        "no branch",
        "retention_watermarks",
        "per **(tenant, plane)**",
        "read_for_dispatch",
        "_platform",
    ]:
        assert claim in text, f"docs/10 must record {claim!r}"


@pytest.mark.req("REQ-020")
def test_the_erd_generator_is_reproducible():
    """Running it twice must produce the same document apart from its timestamp,
    or "the docs changed" stops meaning "the schema changed"."""
    import scripts.gen_erd as gen_erd

    first = gen_erd.render(gen_erd.load_metadata())
    second = gen_erd.render(gen_erd.load_metadata())
    strip = lambda text: "\n".join(  # noqa: E731
        line for line in text.splitlines() if not line.startswith("Generated:")
    )
    assert strip(first) == strip(second)
