"""Host contracts for bounded document transformations; parsing stays isolated."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from backend import isolated_skill_documents as worker
from backend.skill_transform import execute_transform,status

class DocumentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation: Literal['pdf-create','pdf-form-fill','pdf-merge','pdf-split','pdf-redact','docx-edit','docx-compare','template-fill','convert','table-extract']
    sourceIds: list[str]=Field(default_factory=list,max_length=8)
    options: dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def bounded(self):
        worker.validate_request(self.model_dump())
        return self

def run_document_operation(request,sources,*,check,stop=None):
    request=request if isinstance(request,DocumentRequest) else DocumentRequest.model_validate(request)
    supplied={source.get('id',source.get('file_id')) for source in sources}
    if supplied!=set(request.sourceIds): raise ValueError('Document sources do not match the authorized request')
    return execute_transform('documents',request.model_dump(),sources,check=check,stop=stop,timeout_seconds=90)
