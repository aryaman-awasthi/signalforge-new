"""S1-T02: Fernet secret store, key from env (REQ-015)."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from signalforge.infrastructure.persistence.models import SecretRow
from signalforge.infrastructure.security.secrets import FernetSecretStore

pytestmark = pytest.mark.anyio

KEY = Fernet.generate_key().decode()


@pytest.mark.req("REQ-015")
async def test_roundtrip_and_ciphertext_at_rest(db_session):
    store = FernetSecretStore(db_session, key=KEY)
    await store.put("t1", "datadog_api_key", "dd-secret-value")
    await db_session.commit()

    row = (await db_session.execute(select(SecretRow))).scalars().one()
    assert b"dd-secret-value" not in row.ciphertext, "value must be encrypted at rest"
    assert await store.get("t1", "datadog_api_key") == "dd-secret-value"


@pytest.mark.req("REQ-015")
async def test_put_overwrites_and_delete_removes(db_session):
    store = FernetSecretStore(db_session, key=KEY)
    await store.put("t1", "token", "v1")
    await store.put("t1", "token", "v2")
    assert await store.get("t1", "token") == "v2"
    assert await store.delete("t1", "token") is True
    assert await store.get("t1", "token") is None
    assert await store.delete("t1", "token") is False


@pytest.mark.req("REQ-015")
async def test_secrets_are_tenant_scoped(db_session):
    store = FernetSecretStore(db_session, key=KEY)
    await store.put("t1", "shared-name", "tenant-one-value")
    assert await store.get("t2", "shared-name") is None, "cross-tenant read must fail"
    assert await store.list_names("t2") == []


@pytest.mark.req("REQ-015")
async def test_listing_exposes_names_only_and_repr_redacts(db_session):
    store = FernetSecretStore(db_session, key=KEY)
    await store.put("t1", "a", "value-a")
    names = await store.list_names("t1")
    assert names == ["a"]
    assert "value-a" not in repr(store) and KEY not in repr(store)


@pytest.mark.req("REQ-015")
async def test_wrong_key_cannot_decrypt(db_session):
    store = FernetSecretStore(db_session, key=KEY)
    await store.put("t1", "name", "value")
    await db_session.commit()
    wrong = FernetSecretStore(db_session, key=Fernet.generate_key().decode())
    with pytest.raises(Exception):
        await wrong.get("t1", "name")
