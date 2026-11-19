import ast,json
from decimal import Decimal

def unique(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate key')
  d[k]=v
 return d
def parse(text):
 def reject(x):raise ValueError('nonfinite')
 return json.loads(text,parse_int=Decimal,parse_float=Decimal,parse_constant=reject,object_pairs_hook=unique)
def equal(a,b,key=''):
 if b is None:return a is None
 if type(b) is bool:return type(a) is bool and a==b
 if type(b) in (int,float):return isinstance(a,Decimal) and a==Decimal(str(b))
 if isinstance(b,str):
  if key=='expression' and isinstance(a,str):
   try:return ast.dump(ast.parse(a,mode='eval'))==ast.dump(ast.parse(b,mode='eval'))
   except (SyntaxError,ValueError):return False
  return type(a) is str and a==b
 if isinstance(b,list):return isinstance(a,list) and len(a)==len(b) and all(equal(x,y,key) for x,y in zip(a,b))
 if isinstance(b,dict):return isinstance(a,dict) and set(a)==set(b) and all(equal(a[k],v,k) for k,v in b.items())
 return False
