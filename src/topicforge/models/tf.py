"""Models of `inspect_tf`: the raw capture an adapter returns and the report built from it.

`TfCapture` is internal (adapters hand it to `build_tf_report`); `TfReport` is the tool
result. Times are nanoseconds; `stamp_ns` is the transform's own header time (sim time
when the robot runs on `/clock`), `received_ns` the wall clock when it arrived.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(extra="forbid", frozen=True)


class TfSample(BaseModel):
    """One transform seen on `/tf` or `/tf_static`."""

    model_config = _CONFIG

    parent: str = Field(description="Parent frame (`header.frame_id`).")
    child: str = Field(description="Child frame (`child_frame_id`).")
    stamp_ns: int = Field(ge=0, description="Header stamp, ns.")
    received_ns: int = Field(ge=0, description="Wall clock at arrival, ns.")


class TfCapture(BaseModel):
    """What an adapter saw on `/tf_static` and `/tf` during one capture."""

    model_config = _CONFIG

    static: list[TfSample] = Field(default_factory=list, description="Samples from `/tf_static`.")
    dynamic: list[TfSample] = Field(default_factory=list, description="Samples from `/tf`.")
    window_actual_s: float = Field(ge=0, description="Seconds `/tf` was listened to.")
    dropped_count: int = Field(
        default=0, ge=0, description="Messages seen but not decoded (too large, bad YAML, no time)."
    )
    capped: bool = Field(default=False, description="True when the message cap ended the capture.")
    notes: list[str] = Field(default_factory=list, description="Why a stream is empty or cut.")


class TfFrame(BaseModel):
    """One frame of the tree, with the transform that places it under its parent."""

    model_config = _CONFIG

    name: str
    parent: str | None = Field(description="`None` for a root; with two parents, the latest seen.")
    kind: Literal["static", "dynamic", "both"] | None = Field(
        description="Source of the frame's transforms; `None` for a root."
    )
    message_count: int = Field(description="Transforms to `parent` captured.")
    last_stamp_ns: int | None
    age_s: float | None = Field(
        description="Seconds its stamp trails the newest `/tf` stamp; `None` if static or root."
    )
    stale: bool = Field(description="`age_s` above `stale_after_s`.")
    observed_frequency_hz: float | None = Field(description="Rate on arrival (wall) time.")
    sim_frequency_hz: float | None = Field(description="Rate on header stamps.")


class TfFinding(BaseModel):
    """One structural problem found in the tree."""

    model_config = _CONFIG

    kind: str = Field(
        description="`stale`, `two_parents`, `disconnected`, `static_and_dynamic`, `no_path`, "
        "`cycle`; open set."
    )
    frames: list[str]
    text: str


class TfReport(BaseModel):
    """The TF tree as captured: frames, roots, findings and an optional path."""

    model_config = _CONFIG

    frames: list[TfFrame] = Field(description="Sorted by name.")
    roots: list[str] = Field(description="Frames with no parent; one in a sound tree.")
    frame_count: int
    static_count: int = Field(description="Frames with a static transform.")
    dynamic_count: int = Field(description="Frames with a dynamic transform.")
    stale_count: int
    transform_count: int = Field(description="Distinct parent-child pairs.")
    message_count: int = Field(description="Transforms captured, repeats included.")
    findings: list[TfFinding] = Field(description="Empty when the tree is sound.")
    hints: list[str]
    path: list[str] | None = Field(
        default=None, description="Frames from `source_frame` to `target_frame`."
    )
    path_note: str | None = Field(default=None, description="Why `path` is `None`, or context.")
    reference_ns: int | None = Field(description="Newest `/tf` stamp: the clock for `age_s`.")
    window_s: float
    window_actual_s: float
    stamp_span_s: float | None
    real_time_factor: float | None = Field(
        description="Stamp time over wall time: about 1 on a real robot, other on sim."
    )
    truncated: bool = Field(description="Messages were dropped or the cap was hit.")
    mode_effective: Literal["mock", "live"] = Field(description="`live` or `mock` (fixtures).")
    note: str | None = Field(default=None, description="One sentence of context, or `None`.")
    urdf_source: Literal["robot_description", "none"] = "none"
    urdf_link_count: int | None = None
    declared_frames_missing: list[str] = Field(
        default_factory=list, description="URDF links with no TF frame."
    )
    frames_not_in_urdf: list[str] = Field(
        default_factory=list, description="TF frames not in the URDF (`map`, `odom` are normal)."
    )
    urdf_note: str | None = None


__all__ = ["TfCapture", "TfFinding", "TfFrame", "TfReport", "TfSample"]
