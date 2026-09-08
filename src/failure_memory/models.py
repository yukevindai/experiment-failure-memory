from datetime import datetime
from typing import Literal

import pint
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UNITS = pint.UnitRegistry()


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, str_strip_whitespace=True
    )


class Quantity(Strict):
    name: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=80)
    uncertainty: float | None = Field(default=None, ge=0)

    @field_validator("value", "uncertainty", mode="before")
    @classmethod
    def real_number(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError(
                "Measurements must be JSON numbers, not booleans or numeric strings"
            )
        if isinstance(value, int):
            try:
                float(value)
            except OverflowError as exc:
                raise ValueError("Measurement exceeds floating-point range") from exc
        return value

    @field_validator("unit")
    @classmethod
    def known_unit(cls, value):
        try:
            UNITS.Unit(value)
        except (pint.errors.PintError, ValueError, TypeError) as exc:
            raise ValueError(
                "Use a recognized unit; use dimensionless when appropriate"
            ) from exc
        return value


class Material(Strict):
    name: str = Field(min_length=1, max_length=200)
    lot: str = Field(default="", max_length=200)
    identifier: str = Field(default="", max_length=200)
    amount: Quantity | None = None


class Equipment(Strict):
    name: str = Field(min_length=1, max_length=200)
    asset_id: str = Field(default="", max_length=200)
    calibration_note: str = Field(default="", max_length=2000)


class Cause(Strict):
    cause: str = Field(min_length=1, max_length=1000)
    confidence: Literal["low", "medium", "high", "unknown"] = "unknown"
    rationale: str = Field(min_length=1, max_length=3000)


class Fix(Strict):
    action: str = Field(min_length=1, max_length=1000)
    outcome: Literal["succeeded", "failed", "mixed", "untried", "unknown"]
    evidence: str = Field(min_length=1, max_length=3000)


class Source(Strict):
    kind: Literal["notebook", "eln", "file", "observation"]
    reference: str = Field(min_length=1, max_length=2000)
    notes: str = Field(default="", max_length=3000)


class Experiment(Strict):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    performed_at: datetime
    status: Literal["failed", "partial", "succeeded", "inconclusive"]
    materials: list[Material] = Field(default_factory=list, max_length=100)
    conditions: list[Quantity] = Field(default_factory=list, max_length=100)
    procedure: list[str] = Field(default_factory=list, max_length=200)
    equipment: list[Equipment] = Field(default_factory=list, max_length=100)
    outcomes: str = Field(min_length=1, max_length=10000)
    measurements: list[Quantity] = Field(default_factory=list, max_length=100)
    suspected_causes: list[Cause] = Field(default_factory=list, max_length=50)
    fixes: list[Fix] = Field(default_factory=list, max_length=100)
    uncertainty_notes: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    source: Source

    @field_validator("performed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("performed_at requires a timezone")
        return value

    @field_validator("procedure", "tags")
    @classmethod
    def bounded_strings(cls, values):
        if any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError("Entries must be nonblank and at most 2000 characters")
        return values

    @model_validator(mode="after")
    def unique_quantities(self):
        for values in (self.conditions, self.measurements):
            if len({v.name.casefold() for v in values}) != len(values):
                raise ValueError(
                    "Quantity names must be unique within conditions/measurements"
                )
        return self


class Edit(Strict):
    expected_version: int = Field(ge=1, strict=True)
    record: Experiment


class Name(Strict):
    name: str = Field(min_length=1, max_length=200)


class Project(Name):
    description: str = Field(default="", max_length=3000)


class Member(Strict):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,80}$")
    role: str


class Comment(Strict):
    body: str = Field(min_length=1, max_length=10000)


class Link(Strict):
    target_id: str
    relation: Literal["repeat_of", "fix_for", "derived_from"]


class Archive(Strict):
    expected_version: int = Field(ge=1, strict=True)
    archived: bool = Field(strict=True)


class Import(Strict):
    schema_version: Literal["1.0"]
    connector: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    external_id: str = Field(min_length=1, max_length=200)
    record: Experiment
