"""Persisted research scope; transport choices never confer new permissions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class KnowledgeFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str | None = Field(default=None, max_length=80)
    domain: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=30)
    version: str | None = Field(default=None, max_length=200)


class ResearchOptions(KnowledgeFilters):
    scopes: list[Literal['web', 'knowledge', 'files']] = Field(min_length=1, max_length=3)
    response_language: str = Field(default='ar', pattern=r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$', max_length=40)
    allow_network: StrictBool = False
    require_current: StrictBool = False

    @field_validator('scopes')
    @classmethod
    def unique_scopes(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Duplicate research scope')
        return value

    def filters(self):
        return {k: getattr(self, k) for k in KnowledgeFilters.model_fields if getattr(self, k)}
