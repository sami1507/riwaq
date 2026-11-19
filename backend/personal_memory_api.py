"""Explicit owner routes; no route can approve Stage8 or upload gate evidence."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend import guard, local_auth, personal_memory
from backend.db import get_db

router=APIRouter(prefix='/api/personal-memory')


class Consent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:bool
    expected_version:int=Field(ge=1)


class Create(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    rule:dict
    idempotency_key:str=Field(min_length=8,max_length=100)
    consent_version:int=Field(ge=1)


class Update(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    rule:dict
    expected_version:int=Field(ge=1)
    consent_version:int=Field(ge=1)


def invoke(function,*args,**kwargs):
    principal=local_auth.current_principal()
    with get_db() as c:
        try:return function(c,guard.Actor(principal['owner_id'],principal['session_id']),*args,**kwargs)
        except personal_memory.PersonalMemoryError as exc:raise HTTPException(exc.status_code,exc.code) from exc


@router.get('/settings')
def settings(): return invoke(personal_memory.get_settings)


@router.patch('/settings')
def update_settings(body:Consent):return invoke(personal_memory.set_enabled,**body.model_dump())


@router.get('')
def entries():return invoke(personal_memory.list_entries)


@router.post('')
def create(body:Create):return invoke(personal_memory.create_entry,**body.model_dump())


@router.get('/{identifier}')
def entry(identifier:str):return invoke(personal_memory.get_entry,identifier)


@router.patch('/{identifier}')
def update(identifier:str,body:Update):return invoke(personal_memory.update_entry,identifier,**body.model_dump())


@router.delete('/{identifier}')
def delete(identifier:str,expected_version:int):return invoke(personal_memory.delete_entry,identifier,expected_version)
