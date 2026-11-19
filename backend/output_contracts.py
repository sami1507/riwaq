"""Explicit bounded JSON output contracts; conformance is not factual correctness."""
from copy import deepcopy
from decimal import Decimal
import json,math
from typing import Any
from pydantic import BaseModel,ConfigDict,Field,model_validator
TYPES={'object','array','string','number','integer','boolean','null'}
KEYS={'type','properties','required','additionalProperties','items','enum','minItems','maxItems','minLength','maxLength','minimum','maximum'}

class OutputValidationError(ValueError):
 """Safe boundary error: never embeds the generated response or its values."""

def validated_output(text,schema,*,ordered_fields=(),authorize_resource=None,resource_fields=()):
 """Parse and validate before a trusted caller can consume structured data.

 Semantic field relationships and resource authorization are supplied by the
 application contract, never inferred or granted by the generated JSON.
 """
 validate_schema(schema)
 def pairs(items):
  result={}
  for key,value in items:
   if key in result:raise OutputValidationError('structured_output_duplicate_field')
   result[key]=value
  return result
 def reject(value):raise OutputValidationError('structured_output_nonfinite_number')
 try:
  if not isinstance(text,str) or len(text)>100000:raise OutputValidationError('structured_output_size_limit')
  result=json.loads(text,object_pairs_hook=pairs,parse_float=Decimal,parse_int=Decimal,parse_constant=reject)
  if violations(result,schema):raise OutputValidationError('structured_output_contract_failed')
  for start,end in ordered_fields:
   if start not in result or end not in result or result[start] is None or result[end] is None:
    raise OutputValidationError('structured_output_relationship_missing')
   if result[start]>result[end]:raise OutputValidationError('structured_output_relationship_failed')
  for field in resource_fields:
   if authorize_resource is None or field not in result or not authorize_resource(field,result[field]):
    raise OutputValidationError('structured_output_resource_unavailable')
  return result
 except OutputValidationError:raise
 except (ValueError,TypeError,RecursionError,OverflowError):
  raise OutputValidationError('structured_output_invalid_json') from None
def finite(value,depth=0):
 # Schema-node depth is checked at five below. Raw JSON additionally contains
 # properties/items wrappers and required/enum arrays; allow their overhead.
 if depth>12:raise ValueError('Schema structure is too deep')
 if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite schema value')
 if isinstance(value,dict):
  for x in value.values():finite(x,depth+1)
 if isinstance(value,list):
  for x in value:finite(x,depth+1)
def validate_schema(schema):
 finite(schema)
 if len(json.dumps(schema,ensure_ascii=False).encode())>8192:raise ValueError('Schema exceeds 8192 bytes')
 count=0
 def visit(node,depth):
  nonlocal count
  count+=1
  if count>64 or depth>5:raise ValueError('Schema exceeds 64 nodes or depth 5')
  if not isinstance(node,dict) or set(node)-KEYS:raise ValueError('Unsupported schema keyword')
  types=node.get('type');types=types if isinstance(types,list) else [types]
  if not types or any(not isinstance(t,str) or t not in TYPES for t in types) or len(set(types))!=len(types):raise ValueError('Invalid schema type')
  if len(types)>1 and any(t in {'object','array'} for t in types):raise ValueError('Only scalar type unions are supported')
  if 'object' in types:
   if set(node)-{'type','properties','required','additionalProperties'}:raise ValueError('Unsupported object constraint')
   props=node.get('properties');required=node.get('required',[])
   if not isinstance(props,dict) or len(props)>20 or any(not isinstance(k,str) or not 1<=len(k)<=80 or any(ord(c)<32 for c in k) for k in props):raise ValueError('Invalid object properties')
   if node.get('additionalProperties') is not False:raise ValueError('Objects require additionalProperties:false')
   if not isinstance(required,list) or any(not isinstance(k,str) or k not in props for k in required) or len(set(required))!=len(required):raise ValueError('Invalid required fields')
   for child in props.values():visit(child,depth+1)
  elif 'array' in types:
   if set(node)-{'type','items','minItems','maxItems'}:raise ValueError('Unsupported array constraint')
   visit(node.get('items'),depth+1)
  else:
   if set(node)-{'type','enum','minLength','maxLength','minimum','maximum'}:raise ValueError('Unsupported scalar constraint')
   if any(k in node for k in ['minLength','maxLength']) and 'string' not in types:raise ValueError('Length requires string type')
   if any(k in node for k in ['minimum','maximum']) and not set(types)&{'number','integer'}:raise ValueError('Numeric bounds require numeric type')
  for lo,hi,limit in [('minItems','maxItems',100),('minLength','maxLength',10000)]:
   for key in [lo,hi]:
    if key in node and (type(node[key]) is not int or not 0<=node[key]<=limit):raise ValueError('Invalid '+key)
   if node.get(lo,0)>node.get(hi,limit):raise ValueError('Inverted limits')
  for key in ['minimum','maximum']:
   if key in node and (type(node[key]) not in (int,float) or abs(node[key])>10**30):raise ValueError('Invalid numeric bound')
  if 'minimum' in node and 'maximum' in node and node['minimum']>node['maximum']:raise ValueError('Inverted numeric bounds')
  if 'enum' in node:
   values=node['enum']
   if not isinstance(values,list) or not 1<=len(values)<=32:raise ValueError('Invalid enum')
   for value in values:
    if isinstance(value,(dict,list)) or len(str(value))>1000 or not any(type_matches(value,t) for t in types):raise ValueError('Invalid enum member')
 if not isinstance(schema,dict) or schema.get('type')!='object':raise ValueError('Root schema must be an object')
 visit(schema,0)
 return schema

def type_matches(value,kind):
 if kind=='null':return value is None
 if kind=='boolean':return type(value) is bool
 if kind=='string':return isinstance(value,str)
 if kind=='array':return isinstance(value,list)
 if kind=='object':return isinstance(value,dict)
 if kind in {'number','integer'}:
  if type(value) not in (int,float,Decimal) or isinstance(value,float) and not math.isfinite(value):return False
  value=Decimal(str(value))
  return value.is_finite() and (kind=='number' or value==value.to_integral_value())
 return False

def violations(value,schema,path='$'):
 types=schema['type'] if isinstance(schema['type'],list) else [schema['type']]
 if not any(type_matches(value,t) for t in types):return [path+': wrong type']
 errors=[]
 if 'enum' in schema and not any(type_matches(value,'number') and type_matches(x,'number') and Decimal(str(value))==Decimal(str(x)) or type(value)==type(x) and value==x for x in schema['enum']):errors.append(path+': outside enum')
 if isinstance(value,dict):
  props=schema['properties']
  if set(value)-set(props):errors.append(path+': extra fields')
  if set(schema.get('required',[]))-set(value):errors.append(path+': missing fields')
  for key,item in value.items():
   if key in props:errors+=violations(item,props[key],path+'.'+key)
 elif isinstance(value,list):
  if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',100):errors.append(path+': array length')
  for i,item in enumerate(value[:101]):errors+=violations(item,schema['items'],path+f'[{i}]')
 elif isinstance(value,str):
  if not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',10000):errors.append(path+': string length')
 elif type_matches(value,'number'):
  for key,fail in [('minimum',lambda x,y:x<y),('maximum',lambda x,y:x>y)]:
   if key in schema and fail(Decimal(str(value)),Decimal(str(schema[key]))):errors.append(path+': '+key)
 return errors[:30]

class OutputContract(BaseModel):
 model_config=ConfigDict(extra='forbid',serialize_by_alias=True)
 schema_definition:dict[str,Any]=Field(alias='schema')
 citation_field:str|None=Field(default=None,max_length=80)
 @model_validator(mode='after')
 def validate_contract(self):
  validate_schema(self.schema_definition)
  self.schema_definition=deepcopy(self.schema_definition)
  def bounds(node):
   kinds=node['type'] if isinstance(node['type'],list) else [node['type']]
   if 'array' in kinds:node.setdefault('maxItems',100);bounds(node['items'])
   if 'string' in kinds:node.setdefault('maxLength',10000)
   for child in node.get('properties',{}).values():bounds(child)
  bounds(self.schema_definition)
  if self.citation_field:
   node=self.schema_definition['properties'].get(self.citation_field)
   if not node or node.get('type')!='array' or node['items'].get('type')!='string':raise ValueError('Citation field must be a root array of strings')
  return self
 def instructions(self):
  return ('The user explicitly requests one JSON object matching this schema; use its exact field names and types. '
   'A schema is only an output shape, not evidence for facts. Do not fabricate missing facts to fill it. '
   'If the shape cannot express missing evidence, explain the inability rather than invent data; conformance will be recorded as failed. '
   +'Canonical citation identifiers have brackets, for example [S1], and must refer to supplied excerpts. '
   +'Schema: '+json.dumps(self.schema_definition,ensure_ascii=False,separators=(',',':')))
 def bind(self,count):
  result=deepcopy(self.schema_definition)
  if self.citation_field:
   node=result['properties'][self.citation_field];item=node['items']
   markers=[f'[S{i+1}]' for i in range(count)]
   markers=[m for m in markers if not violations(m,item)]
   if not markers:
    if node.get('minItems',0)>0:raise ValueError('Output contract requires citations but no eligible source identifiers exist')
    node['maxItems']=0
   else:item['enum']=markers
  return result
 def check(self,text,count):
  def pairs(items):
   value={}
   for key,item in items:
    if key in value:raise ValueError('Duplicate JSON key')
    value[key]=item
   return value
  def reject(value):raise ValueError('Nonfinite JSON number')
  try:
   if len(text)>100000:raise ValueError('JSON response exceeds output limit')
   value=json.loads(text,object_pairs_hook=pairs,parse_float=Decimal,parse_int=Decimal,parse_constant=reject)
   shape=violations(value,self.schema_definition)
   bound=violations(value,self.bind(count))
   citation_ok=None
   if self.citation_field:
    citation_ok=isinstance(value,dict) and (self.citation_field not in value and self.citation_field not in self.schema_definition.get('required',[]) or self.citation_field in value and not violations(value[self.citation_field],self.bind(count)['properties'][self.citation_field]))
   return {'schema_valid':not shape,'citation_format_valid':bool(citation_ok) if self.citation_field else None,'errors':list(dict.fromkeys(shape+bound)),'semantic_support':'not evaluated'}
  except (ValueError,RecursionError,TypeError) as error:
   return {'schema_valid':False,'citation_format_valid':False if self.citation_field else None,'errors':[str(error)[:300]],'semantic_support':'not evaluated'}
