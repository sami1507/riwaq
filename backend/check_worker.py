"""Fixed arithmetic/static-analysis worker. Never execute user code.
Run by the parent with -I -S, finite input/output, memory/job and wall limits.
This is not an arbitrary-code sandbox.
"""
import ast
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
from decimal import Decimal, localcontext, ROUND_HALF_UP
from fractions import Fraction

VERSION = 'checks-v1.1'
MEMORY_MB = 256

def start_watchdog(cpu_seconds=2, wall_seconds=5):
    # Fixed trusted worker only. Windows CPU job-time enforcement was not reliable
    # in this environment; a process watchdog supplements the parent wall timeout.
    cpu_started=time.process_time();wall_started=time.monotonic()
    def watch():
        while True:
            if time.process_time()-cpu_started>=cpu_seconds or time.monotonic()-wall_started>=wall_seconds:os._exit(124)
            time.sleep(.02)
    threading.Thread(target=watch,daemon=True,name='riwaq-check-limits').start()

def limits(cpu_seconds=2, wall_seconds=5, memory_mb=MEMORY_MB):
    if os.name == 'nt':
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name,ctypes.c_ulonglong) for name in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.GetCurrentProcess.restype=wintypes.HANDLE
        handle=kernel.CreateJobObjectW(None,None);value=Extended()
        value.BasicLimitInformation.LimitFlags=0x100|0x8|0x2000|0x2
        value.BasicLimitInformation.ActiveProcessLimit=1;value.BasicLimitInformation.PerProcessUserTimeLimit=cpu_seconds*10_000_000
        value.ProcessMemoryLimit=memory_mb*1024*1024
        if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(value),ctypes.sizeof(value)) or not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):raise RuntimeError('Cannot enforce checker resource limits')
        start_watchdog(cpu_seconds,wall_seconds)
        return handle
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(memory_mb*1024*1024,)*2)
    resource.setrlimit(resource.RLIMIT_CPU,(cpu_seconds,cpu_seconds))
    start_watchdog(cpu_seconds,wall_seconds)
    return None

class Arithmetic:
    def __init__(self,text):
        if not isinstance(text,str) or not text.strip() or len(text)>512:raise ValueError('Expression must contain 1–512 characters')
        self.text=text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫×÷−','0123456789.*/-')).strip()
        if re.search(r'[^0-9.\s()+*/-]',self.text):raise ValueError('Only decimal numbers, parentheses and + - * / are supported')
        self.tokens=re.findall(r'\d+(?:\.\d+)?|\.\d+|[^\s]',self.text)
        if len(self.tokens)>128:raise ValueError('Expression has too many tokens')
        self.at=0;self.depth=0
    def take(self):
        if self.at>=len(self.tokens):raise ValueError('Incomplete expression')
        token=self.tokens[self.at];self.at+=1;return token
    def bound(self,value):
        if value.numerator.bit_length()>2048 or value.denominator.bit_length()>2048:raise ValueError('Arithmetic result exceeds bit limit')
        return value
    def factor(self):
        self.depth+=1
        if self.depth>16:raise ValueError('Expression is nested too deeply')
        token=self.take()
        if token in ('+','-'):value=self.factor()*(1 if token=='+' else -1)
        elif token=='(':
            value=self.expr()
            if self.take()!=')':raise ValueError('Unbalanced parentheses')
        elif re.fullmatch(r'\d+(?:\.\d+)?|\.\d+',token):
            if len(token)>64:raise ValueError('Numeric literal exceeds 64 characters')
            value=Fraction(token)
        else:raise ValueError('Unsupported arithmetic token')
        self.depth-=1;return self.bound(value)
    def term(self):
        result=self.factor()
        while self.at<len(self.tokens) and self.tokens[self.at] in ('*','/'):
            op=self.take();value=self.factor()
            if op=='/' and not value:raise ValueError('Division by zero')
            result=self.bound(result*value if op=='*' else result/value)
        return result
    def expr(self):
        result=self.term()
        while self.at<len(self.tokens) and self.tokens[self.at] in ('+','-'):
            op=self.take();value=self.term();result=self.bound(result+value if op=='+' else result-value)
        return result
    def result(self):
        value=self.expr()
        if self.at!=len(self.tokens):raise ValueError('Unexpected remaining arithmetic tokens')
        with localcontext() as context:
            context.prec=80
            decimal=Decimal(value.numerator)/Decimal(value.denominator)
            display=format(decimal,'f')
        cents,remainder=divmod(abs(value.numerator)*100,value.denominator)
        if remainder*2>=value.denominator:cents+=1
        rounded=('-' if value<0 and cents else '')+str(cents//100)+'.'+str(cents%100).zfill(2)
        divisor=value.denominator
        for factor in (2,5):
            while divisor%factor==0:divisor//=factor
        return {'expression':self.text,'exact':str(value),'decimal':display,'decimal_exact':Fraction(display)==value,'rounded_2':rounded,'rounding':'ROUND_HALF_UP','scope':'Arithmetic expression only; units and the model of the problem are not verified'}

def analyze_python(source):
    if not isinstance(source,str) or len(source.encode('utf-8'))>16384 or len(source.splitlines())>256:raise ValueError('Python source exceeds 16 KiB or 256 lines')
    tree=ast.parse(source);nodes=list(ast.walk(tree))
    if len(nodes)>2048:raise ValueError('Python source exceeds 2048 syntax nodes')
    stack=[(tree,0)]
    while stack:
        node,depth=stack.pop()
        if depth>40:raise ValueError('Python syntax nesting exceeds 40')
        stack.extend((child,depth+1) for child in ast.iter_child_nodes(node))
    findings=[]
    def add(node,code,text):
        if len(findings)<30:findings.append({'code':code,'line':getattr(node,'lineno',1),'summary':text})
    for node in nodes:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            for default in node.args.defaults+node.args.kw_defaults:
                if isinstance(default,(ast.List,ast.Dict,ast.Set)):add(node,'mutable_default','Mutable default is shared across calls; consider None plus per-call allocation.')
        if isinstance(node,ast.Return) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr in ('sort','reverse','append','extend','update'):
            add(node,'returned_mutator','If this receiver is a built-in collection, this mutating method returns None. Confirm receiver type; consider a non-mutating alternative when required.')
        if isinstance(node,ast.ExceptHandler) and node.type is None:add(node,'bare_except','Bare except also catches cancellation/system-exit exceptions; choose a specific exception when appropriate.')
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Div,ast.FloorDiv)) and isinstance(node.right,ast.Call) and isinstance(node.right.func,ast.Name) and node.right.func.id=='len':add(node,'empty_length_division','If this collection is empty, division by len(collection) divides by zero. Check the caller contract or a guard.')
        if isinstance(node,ast.Compare) and any(isinstance(op,(ast.Is,ast.IsNot)) for op in node.ops) and any(isinstance(v,ast.Constant) and v.value is not None and not isinstance(v.value,bool) for v in [node.left]+node.comparators):add(node,'literal_identity','Identity is not value equality for numeric/string literals.')
    return {'parsed':True,'executed':False,'node_count':len(nodes),'diagnostics':findings,'scope':'Static syntax and selected patterns only. No compilation, imports, execution or behavioral correctness verification.'}

def main():
    raw=sys.stdin.buffer.read(20001)
    if len(raw)>20000:raise ValueError('Tool input exceeds 20000 bytes')
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={'tool','input'} or type(data['input']) is not str:raise ValueError('Invalid tool envelope')
    mathematical=data['tool']=='solve_math'
    cpu_seconds,wall_seconds,memory_mb=(20,30,512) if mathematical else (2,5,MEMORY_MB)
    handle=limits(cpu_seconds,wall_seconds,memory_mb)
    # Fixed worker has no network functionality. Guard accidental networking too.
    import socket
    def deny(*args,**kwargs):raise OSError('Checker network is disabled')
    socket.socket.connect=deny;socket.create_connection=deny
    if data['tool']=='calculate':result=Arithmetic(data['input']).result()
    elif mathematical:
        # -I -S never runs site hooks or PYTHONPATH. These two fixed locations
        # belong to the executable and this source checkout, never the request.
        from pathlib import Path
        if not getattr(sys,'frozen',False):
            # POSIX virtual environments link bin/python to the base
            # interpreter; resolving that link would select the base
            # site-packages instead of this environment's (CORE portability).
            executable=Path(sys.executable).resolve() if os.name=='nt' else Path(os.path.abspath(sys.executable))
            environment_root=executable.parent.parent
            packages=environment_root/'Lib'/'site-packages' if os.name=='nt' else environment_root/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
            sys.path.insert(0,str(packages));sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
        import sympy,mpmath
        if sympy.__version__!='1.14.0' or mpmath.__version__!='1.3.0':raise ValueError('Unsupported math engine version')
        from backend.reasoning_math import solve_math,validate_math_result
        result=validate_math_result(solve_math(data['input']),data['input'])
        result.update(engines={'sympy':sympy.__version__,'mpmath':mpmath.__version__})
    elif data['tool']=='solve_problem':
        # -I -S intentionally excludes normal imports. Load one fixed reviewed
        # sibling module; neither a path nor an import name comes from input.
        if getattr(sys,'frozen',False):
            from backend import reasoning_problem as module
        else:
            filename=os.path.join(os.path.dirname(os.path.abspath(__file__)),'reasoning_problem.py')
            spec=importlib.util.spec_from_file_location('riwaq_fixed_problem',filename)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=module.solve_problem(data['input'],lambda expression:Arithmetic(expression).result())
    elif data['tool']=='analyze_python':result=analyze_python(data['input'])
    else:raise ValueError('Unknown checker')
    result.update(engine=VERSION,input_sha256=hashlib.sha256(data['input'].encode()).hexdigest(),memory_limit_mb=memory_mb,cpu_limit_seconds=cpu_seconds,cpu_enforcement='fixed-worker watchdog; configured OS CPU limit not relied upon',network='fixed-worker guard; not OS isolation',process_limited=True)
    print(json.dumps({'status':'ok','result':result},ensure_ascii=False))
    return handle

if __name__=='__main__':
    try:job_handle=main()
    except (Exception,MemoryError) as error:
        print(json.dumps({'status':'error','error':str(error)[:400]},ensure_ascii=False));sys.exit(1)
