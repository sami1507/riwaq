"""Bounded educational inputs; client fields never assert clinical authority."""
from datetime import date
from typing import Literal
import re,unicodedata
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

TOPICS=('symptom_diagnosis','lab_result','chest_symptoms','medication_question','mass_conversion','clinical_calculator','source_contexts','unclear_report','current_drug_warning','observational_study')
class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    @field_validator('*',mode='after')
    @classmethod
    def safe_text(cls,value):
        if isinstance(value,str) and any(ord(c)<32 and c not in '\n\r\t' for c in value):raise ValueError('Control characters are not accepted')
        if isinstance(value,str):
            value=value.strip()
            if not value:raise ValueError('Use a nonblank value or omit the unknown field')
        return value

def decimal_text(value):
    if value is None:return value
    value=''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in value).replace('٫','.')
    if not re.fullmatch(r'[+-]?\d{1,15}(?:\.\d{1,12})?',value):raise ValueError('Use an unambiguous decimal, without thousands separators or scientific notation')
    return value

class Lab(Input):
    name:str|None=Field(default=None,min_length=1,max_length=120)
    value:str|None=Field(default=None,max_length=32)
    unit:str|None=Field(default=None,min_length=1,max_length=60)
    reference_low:str|None=Field(default=None,max_length=32)
    reference_high:str|None=Field(default=None,max_length=32)
    reference_unit:str|None=Field(default=None,min_length=1,max_length=60)
    sample_date:str|None=Field(default=None,max_length=10)
    reading_uncertain:bool=False
    _numbers=field_validator('value','reference_low','reference_high')(decimal_text)
    @field_validator('sample_date')
    @classmethod
    def valid_date(cls,value):
        if value is not None:date.fromisoformat(value)
        return value
    @model_validator(mode='after')
    def ordered_range(self):
        from fractions import Fraction
        if self.reference_low is not None and self.reference_high is not None and Fraction(self.reference_low)>Fraction(self.reference_high):raise ValueError('Report range is reversed')
        return self

class Quantity(Input):
    value:str=Field(max_length=32)
    from_unit:Literal['mcg','mg','g','mL']
    to_unit:Literal['mcg','mg','g','mL']
    _value=field_validator('value')(decimal_text)

class SourceContext(Input):
    label:str=Field(min_length=1,max_length=120)
    country:str|None=Field(default=None,max_length=80)
    population:str|None=Field(default=None,max_length=200)
    effective_date:str|None=Field(default=None,max_length=10)
    @field_validator('effective_date')
    @classmethod
    def valid_date(cls,value):
        if value is not None:date.fromisoformat(value)
        return value

class Explain(Input):
    topic:Literal['symptom_diagnosis','lab_result','chest_symptoms','medication_question','mass_conversion','clinical_calculator','source_contexts','unclear_report','current_drug_warning','observational_study']
    language:Literal['ar','en']='ar'
    level:Literal['simple','detailed','technical']='simple'
    purpose:Literal['education','personal_information']='education'
    lab:Lab|None=None
    quantity:Quantity|None=None
    severe_persistent_chest_pain:bool=False
    shortness_of_breath:bool=False
    medication_name:str|None=Field(default=None,max_length=120)
    calculator_name:str|None=Field(default=None,max_length=120)
    source_contexts:list[SourceContext]=Field(default_factory=list,max_length=2)
    image_kind:Literal['report','raw_medical_image']='report'
    optional_understanding_check:bool=False
    # These purposes are deliberately unavailable, not unchecked consent boxes.
    persist:Literal[False]=False
    allow_training:Literal[False]=False
