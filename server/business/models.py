from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .security import check_password, identifier

ID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Role = Literal["admin", "teacher", "parent"]
Label = Annotated[str, Field(min_length=1, max_length=80)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def controls(cls, value, info):
        if isinstance(value, str):
            value.encode("utf-8")
            if info.field_name == "password":
                return value
            allowed = {9, 10, 13} if info.field_name == "body" else set()
            if any(ord(char) < 32 and ord(char) not in allowed for char in value):
                raise ValueError("Control characters are not accepted")
            if info.field_name in {"name", "display_name", "external_ref", "reason", "body"} and not value.strip():
                raise ValueError("A nonempty value is required")
        return value


class Login(Input):
    organization: str = Field(min_length=3, max_length=64)
    login: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128, repr=False, json_schema_extra={"writeOnly": True})

    @field_validator("organization", "login")
    @classmethod
    def normalize(cls, value):
        return identifier(value)


class Person(Input):
    login: str = Field(min_length=3, max_length=64)
    display_name: Label
    password: str = Field(repr=False, json_schema_extra={"writeOnly": True})
    roles: list[Role] = Field(default_factory=list, max_length=3)

    _login = field_validator("login")(classmethod(lambda cls, value: identifier(value)))
    _password = field_validator("password")(classmethod(lambda cls, value: check_password(value)))

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate roles")
        return value


class PersonAccess(Input):
    roles: list[Role] = Field(default_factory=list, max_length=3)
    active: bool = True
    expected_roles: list[Role] | None = Field(default=None, max_length=3)
    expected_active: bool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set & {"roles", "active"} or len(self.roles) != len(set(self.roles)) or self.expected_roles is not None and len(self.expected_roles) != len(set(self.expected_roles)):
            raise ValueError("Specify a unique role list or active state")
        return self


class PasswordReset(Input):
    password: str = Field(repr=False, json_schema_extra={"writeOnly": True})
    _password = field_validator("password")(classmethod(lambda cls, value: check_password(value)))


class Class(Input):
    name: Label
    course_name: str = Field(default="", max_length=80)


class Teachers(Input):
    teacher_ids: list[ID] = Field(max_length=50)
    expected_teacher_ids: list[ID] | None = Field(default=None, max_length=50)

    @field_validator("teacher_ids", "expected_teacher_ids")
    @classmethod
    def unique(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Duplicate teacher IDs")
        return value


class Student(Input):
    class_id: ID
    name: Label
    external_ref: Label
    remaining_units: int = Field(ge=-1_000_000, le=10_000_000)


class TestParentLink(Input):
    parent_id: ID
    student_id: ID


class Device(Input):
    name: Label
    class_ids: list[ID] = Field(max_length=100)

    @field_validator("class_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate class IDs")
        return value


class DeviceAccess(Input):
    active: bool
    class_ids: list[ID] = Field(max_length=100)

    @field_validator("class_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate class IDs")
        return value


def aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Use an ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("An explicit UTC offset is required")
    return parsed


class Lesson(Input):
    class_id: ID
    teacher_id: ID
    starts_at: str = Field(max_length=40)
    ends_at: str = Field(max_length=40)
    student_ids: list[ID] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def times(self):
        start, end = aware_time(self.starts_at), aware_time(self.ends_at)
        if not 0 < (end - start).total_seconds() <= 12 * 3600 or not 2000 <= start.year <= 2100:
            raise ValueError("Invalid lesson interval")
        if self.student_ids is not None and len(self.student_ids) != len(set(self.student_ids)):
            raise ValueError("Duplicate roster members")
        return self


class Leave(Input):
    student_id: ID
    reason: str = Field(min_length=1, max_length=200)


class CancelLeave(Input):
    reason: str = Field(min_length=1, max_length=200)


class AttendanceEvent(Input):
    event_ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    lesson_id: ID
    student_id: ID
    occurred_at: str | None = Field(max_length=40)
    consumed_units: int = Field(ge=0, le=10000)
    remaining_units: int = Field(ge=-1_000_000, le=10_000_000)
    student_revision: int = Field(ge=1, le=9_000_000_000_000_000)
    source: Literal["live", "makeup", "historical"]
    _time = field_validator("occurred_at")(classmethod(lambda cls, value: (aware_time(value), value)[1] if value is not None else None))

    @model_validator(mode="after")
    def known_time(self):
        if self.occurred_at is None and self.source != "makeup":
            raise ValueError("Live and historical timed events require a known timestamp")
        return self


class BalanceEvent(Input):
    event_ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    student_id: ID
    occurred_at: str = Field(max_length=40)
    remaining_units: int = Field(ge=-1_000_000, le=10_000_000)
    student_revision: int = Field(ge=1, le=9_000_000_000_000_000)
    source: Literal["live", "historical"]
    _time = field_validator("occurred_at")(classmethod(lambda cls, value: (aware_time(value), value)[1]))


class RevokeEvent(Input):
    event_ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    reason: str = Field(min_length=1, max_length=200)


class Content(Input):
    body: str = Field(min_length=1, max_length=4000)
    audience: Literal["arrived", "roster"] = "arrived"
    publish_after_class: bool = False


NoticeType = Literal["attendance", "low_hours_parent", "lesson_content", "absence_parent", "absence_teacher", "low_hours_teacher", "weekly_teacher", "weekly_admin", "monthly_admin"]


class Preferences(Input):
    preferences: dict[NoticeType, bool] = Field(max_length=9)


class AbsenceNotices(Input):
    student_ids: list[ID] = Field(min_length=1, max_length=500)

    @field_validator("student_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate students")
        return value


class Activity(Input):
    student_id: ID
    kind: Literal["new", "renewal"]
    occurred_at: str = Field(max_length=40)
    source_ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    _time = field_validator("occurred_at")(classmethod(lambda cls, value: (aware_time(value), value)[1]))


class Schedule(Input):
    enabled: bool = False
    weekly_weekday: int = Field(default=6, ge=0, le=6)
    weekly_hour: int = Field(default=21, ge=0, le=23)
    weekly_minute: int = Field(default=0, ge=0, le=59)
    monthly_delay_minutes: int = Field(default=0, ge=0, le=1440)
    low_hours_units: int = Field(default=1500, ge=0, le=100000)
