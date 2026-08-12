"""HTTP request and response models owned by the Python surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    service: str
    version: str
    environment: str
    status: Literal["ok"] = "ok"
    ready: bool
    request_id: str
    checks: dict[str, Literal["ok"]] = Field(default_factory=dict)


class StreamQueryRequest(ApiModel):
    query: str = Field(min_length=1, max_length=8_000)
    conversation_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class StreamEvent(ApiModel):
    event: Literal["accepted", "done"]
    request_id: str
    sequence: int = Field(ge=0)
    data: dict[str, Any]
