"""Tests for the binding-free DDS discovery-sample introspection helpers.

Extracted from the Cyclone adapter so the `getattr`-with-fallback field
extraction is testable without the `cyclonedds` binding.

Synthetic duck-typed objects only.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.common.dds_introspection import (
    cyclone_extract_guid,
    cyclone_extract_hostname,
    cyclone_extract_participant_name,
    cyclone_extract_topic_name,
    cyclone_extract_vendor_id,
)


class _Obj:
    """Minimal attribute bag for building duck-typed discovery samples."""

    def __init__(self, **attrs: object) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


# ---------------------------- Cyclone extractors ---------------------------


def test_cyclone_extract_guid_bytes():
    assert cyclone_extract_guid(_Obj(key=b"\x01" * 16)) == b"\x01" * 16


def test_cyclone_extract_guid_inner_value():
    assert cyclone_extract_guid(_Obj(key=_Obj(value=b"\x02" * 16))) == b"\x02" * 16


def test_cyclone_extract_guid_missing_returns_none():
    assert cyclone_extract_guid(_Obj()) is None


def test_cyclone_extract_vendor_id_bytes():
    assert cyclone_extract_vendor_id(_Obj(vendor_id=b"\x01\x16")) == (1, 22)


def test_cyclone_extract_vendor_id_tuple():
    assert cyclone_extract_vendor_id(_Obj(vendor_id=(1, 5))) == (1, 5)


def test_cyclone_extract_vendor_id_missing_returns_none():
    assert cyclone_extract_vendor_id(_Obj()) is None


def test_cyclone_extract_hostname_str_and_bytes():
    assert cyclone_extract_hostname(_Obj(hostname="robot1")) == "robot1"
    assert cyclone_extract_hostname(_Obj(hostname=b"robot2")) == "robot2"


def test_cyclone_extract_hostname_missing_returns_none():
    assert cyclone_extract_hostname(_Obj()) is None


def test_cyclone_extract_topic_name_primary_and_fallback():
    assert cyclone_extract_topic_name(_Obj(topic_name="/scan")) == "/scan"
    # Cyclone falls back to `topic` when `topic_name` is absent.
    assert cyclone_extract_topic_name(_Obj(topic="/tf")) == "/tf"


def test_cyclone_extract_topic_name_missing_returns_none():
    assert cyclone_extract_topic_name(_Obj()) is None


def test_common_reexports_are_importable():
    # The adapters import these via the package `__init__`, not the submodule.
    from topicforge.adapters import common

    assert callable(common.cyclone_extract_guid)
    assert callable(common.cyclone_qos_to_profile)


# ------------------ defensive fallback branches (binding-shape variance) ----
# These pin the getattr-fallback paths that absorb shape differences between
# binding versions; a regression there fails silently.


def test_cyclone_extract_guid_skips_absent_attrs_then_finds_guid():
    # key / participant_key absent -> loop continues to the `guid` attr.
    assert cyclone_extract_guid(_Obj(guid=b"\x07" * 16)) == b"\x07" * 16


def test_cyclone_extract_guid_non_bytes_without_value_returns_none():
    assert cyclone_extract_guid(_Obj(key=12345)) is None


def test_cyclone_extract_vendor_id_from_vendor_attr():
    # Falls back from `vendor_id` to the `vendor` attribute.
    assert cyclone_extract_vendor_id(_Obj(vendor=b"\x01\x16")) == (1, 22)


def test_cyclone_extract_vendor_id_inner_vendorId_attr():
    assert cyclone_extract_vendor_id(_Obj(vendor_id=_Obj(vendorId=(1, 5)))) == (1, 5)


def test_cyclone_extract_hostname_from_user_data_bytes():
    assert cyclone_extract_hostname(_Obj(user_data=b"ud-host")) == "ud-host"


# --- Regressions found on a real bus -----------------------------------------

import uuid  # noqa: E402

from topicforge.adapters.common.dds_introspection import (  # noqa: E402
    is_alive_sample,
    vendor_id_from_guid,
)

_ECLIPSE_GUID = uuid.UUID("01107b9a-c109-4b9c-7bcc-95ae000001c1")


def test_cyclone_guid_accepts_uuid_key():
    """cyclonedds 11.0.1 exposes the builtin key as uuid.UUID."""
    sample = _Obj(key=_ECLIPSE_GUID)
    assert cyclone_extract_guid(sample) == _ECLIPSE_GUID.bytes


def test_cyclone_vendor_falls_back_to_guid_prefix():
    """No vendor field on the builtin sample: read it from the GUID prefix."""
    sample = _Obj(key=_ECLIPSE_GUID)
    assert cyclone_extract_vendor_id(sample) == (0x01, 0x10)


def test_vendor_id_from_guid_edge_cases():
    assert vendor_id_from_guid(None) is None
    assert vendor_id_from_guid(b"\x01") is None
    assert vendor_id_from_guid(bytes([0x01, 0x0F]) + bytes(14)) == (0x01, 0x0F)


@pytest.mark.parametrize(
    ("instance_state", "valid_data", "expected"),
    [
        (16, True, True),  # ALIVE
        (32, True, False),  # NOT_ALIVE_DISPOSED
        (64, True, False),  # NOT_ALIVE_NO_WRITERS: lease expired
        (16, False, False),  # invalid sample
    ],
)
def test_is_alive_sample(instance_state: int, valid_data: bool, expected: bool):
    sample = _Obj(sample_info=_Obj(instance_state=instance_state, valid_data=valid_data))
    assert is_alive_sample(sample) is expected


def test_is_alive_sample_without_sample_info_is_kept():
    assert is_alive_sample(_Obj(key=b"x")) is True


# ---------------------------------------------------------------------------
# Participant name and hostname from the builtin sample's `qos` (cyclonedds 11)
# ---------------------------------------------------------------------------


def _policy(type_name: str, **attrs):
    return type(type_name, (), attrs)()


def _live_qos_sample(*, with_name: bool = True):
    policies = [_policy("Policy.Liveliness.Automatic", lease_duration=10_000_000_000)]
    if with_name:
        policies.insert(0, _policy("Policy.EntityName", name="lidar_driver"))
    policies.append(_policy("Property", key="__Pid", value="18132"))
    policies.append(_policy("Property", key="__Hostname", value="DESKTOP-H0N0S7G"))
    return _Obj(qos=policies)


def test_cyclone_extract_participant_name_from_entity_name_policy():
    assert cyclone_extract_participant_name(_live_qos_sample()) == "lidar_driver"


def test_cyclone_extract_participant_name_absent_returns_none():
    assert cyclone_extract_participant_name(_live_qos_sample(with_name=False)) is None


def test_cyclone_extract_participant_name_attribute_fallback():
    assert cyclone_extract_participant_name(_Obj(participant_name="p1")) == "p1"


def test_cyclone_extract_participant_name_unscoped_class_name():
    sample = _Obj(qos=[_policy("EntityName", name="n1")])
    assert cyclone_extract_participant_name(sample) == "n1"


def test_cyclone_extract_hostname_from_property():
    assert cyclone_extract_hostname(_live_qos_sample()) == "DESKTOP-H0N0S7G"


def test_cyclone_extract_hostname_ignores_other_properties():
    sample = _Obj(qos=[_policy("Property", key="__Pid", value="1")])
    assert cyclone_extract_hostname(sample) is None


def test_cyclone_extractors_tolerate_odd_qos():
    for qos in (None, 42, object(), [object(), None]):
        sample = _Obj(qos=qos)
        assert cyclone_extract_participant_name(sample) is None
        assert cyclone_extract_hostname(sample) is None


def test_cyclone_extractors_never_raise_on_hostile_qos():
    class _Boom:
        def __iter__(self):
            raise RuntimeError("boom")

    sample = _Obj(qos=_Boom())
    assert cyclone_extract_participant_name(sample) is None
    assert cyclone_extract_hostname(sample) is None


def test_cyclone_extract_type_name_reads_the_endpoint_type() -> None:
    from types import SimpleNamespace

    from topicforge.adapters.common import cyclone_extract_type_name

    assert cyclone_extract_type_name(SimpleNamespace(type_name="LidarScan")) == "LidarScan"
    assert cyclone_extract_type_name(SimpleNamespace(type_name="")) is None
    assert cyclone_extract_type_name(SimpleNamespace()) is None
