"""Explicit local providers with measured health and bounded recovery.

Cloud dispatch remains quarantined. Local fallbacks require an explicit list in
trusted settings; model content cannot select a provider or authorize tools.
"""
import asyncio
import json
import time
from contextlib import aclosing
from datetime import datetime,timezone
import httpx
import keyring
from backend.context_budget import enforce
from backend.output_contracts import validated_output
from backend.provider_policy import local_model_name,capabilities,require_capabilities,fallback_decision
from backend.provider_runtime import (LOCAL_ADMISSION, RetryPolicy, RuntimeLimit, retry_call,
    replay_allowed, failure_kind, check_deadline, wait_bounded)

OLLAMA = 'http://127.0.0.1:11434'
MEASURED = {}

async def _request(client,method,path,*,deadline=None,**kwargs):
    deadline=deadline or time.monotonic()+8
    async def call():
        response=await client.request(method,OLLAMA+path,**kwargs)
        response.raise_for_status()
        if len(response.content)>4*1024*1024:raise ValueError('provider_metadata_size_limit')
        value=response.json()
        if not isinstance(value,dict):raise ValueError('provider_metadata_invalid')
        return value
    return await retry_call(call,deadline=deadline,replay='read')

async def inspect_model(model,*,deadline=None):
    local_model_name(model)
    async with httpx.AsyncClient(timeout=5,trust_env=False,follow_redirects=False) as client:
        info=await _request(client,'POST','/api/show',json={'model':model},deadline=deadline)
    return capabilities(model,info)

def secret(provider):
    try:
        return keyring.get_password('Riwaq', provider)
    except Exception:
        return None

async def ollama_status():
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            payload=await _request(client,'GET','/api/tags')
            models=payload.get('models',[])
            if not isinstance(models,list) or len(models)>100 or any(not isinstance(x,dict) or not isinstance(x.get('name'),str) for x in models):raise ValueError('provider_model_list_invalid')
            inspection_limit=asyncio.Semaphore(4)
            async def inspect(item):
                capable = False;report=None
                try:
                    local_model_name(item['name'])
                    async with inspection_limit:
                        info=await _request(client,'POST','/api/show',json={'model':item['name']})
                    report=capabilities(item['name'],info,digest=item.get('digest'))
                    capable=report['declared']['text']
                except (httpx.HTTPError, ValueError, KeyError,TimeoutError):
                    pass
                return item | {'chat_capable':bool(capable),'capability_report':report,
                               'checked_at':datetime.now(timezone.utc).isoformat(),'chat_quality_tested':False}
            return {'available':True, 'models':await asyncio.gather(*(inspect(item) for item in models))}
    except (httpx.HTTPError, ValueError,TimeoutError):
        return {'available':False,'models':[], 'reason':'Ollama is not reachable on 127.0.0.1:11434'}

async def validate_provider(provider, model, mode):
    if provider != 'ollama':
        if mode != 'hybrid':
            raise ValueError('Cloud providers are blocked in local-only mode.')
        from backend.guard import deny_external_generation
        deny_external_generation(provider)
        if provider not in ('openai','anthropic'):
            raise ValueError('Unknown provider.')
        if not secret(provider):
            raise ValueError('Provider key is missing from the operating-system secret store.')
        return
    report=await inspect_model(model)
    require_capabilities(report,{'text'})


async def preflight(provider,model,config):
    """Keep the existing three-argument validator compatible with old callers.

    A configured equivalent local fallback may satisfy availability preflight;
    dispatch still rechecks both metadata and all operation requirements.
    """
    try:
        await validate_provider(provider,model,config.get('mode','local'))
        return
    except httpx.HTTPError as exc:
        if provider!='ollama' or not replay_allowed(exc):raise
        original=exc
    choices=config.get('local_fallback_models',[])
    if not isinstance(choices,list) or len(choices)>3:raise ValueError('invalid_local_fallback_settings')
    for candidate in choices:
        local_model_name(candidate)
        try:
            report=await inspect_model(candidate,deadline=config.get('_deadline'))
            require_capabilities(report,{'text'},config['context_size'])
            return
        except (httpx.HTTPError,ValueError):continue
    raise original

def decode_frame(provider, obj):
    """Pure protocol parsing, testable while cloud dispatch is quarantined."""
    if not isinstance(obj,dict):raise ValueError('provider_frame_not_object')
    packets=[];completed=False
    if provider=='ollama':
        if obj.get('error'):raise ValueError('Local model reported a generation error.')
        message=obj.get('message',{})
        if not isinstance(message,dict):raise ValueError('provider_message_invalid')
        delta=message.get('content','')
        if not isinstance(delta,str) or len(delta)>100000:raise ValueError('provider_delta_invalid')
        if 'done' in obj and type(obj['done']) is not bool:raise ValueError('provider_completion_flag_invalid')
        if delta:packets.append({'content':delta})
        if obj.get('done'):
            completed=True
            reason=obj.get('done_reason','stop')
            if not isinstance(reason,str) or len(reason)>80:raise ValueError('provider_finish_reason_invalid')
            if reason not in {'stop','length','tool_use','end_turn','max_tokens','stop_sequence','completed','unknown'}:reason='unknown'
            metrics={}
            for k in ('eval_count','eval_duration','total_duration','load_duration','prompt_eval_count'):
                if k in obj:
                    if type(obj[k]) is not int or not 0<=obj[k]<=10**18:raise ValueError('provider_usage_invalid')
                    metrics[k]=obj[k]
            packets.append({'finish_reason':reason,'metrics':metrics})
    else:
        typ=obj.get('type','')
        if typ=='response.output_text.delta':packets.append({'content':obj.get('delta','')})
        elif typ=='content_block_delta' and obj.get('delta',{}).get('type')=='text_delta':packets.append({'content':obj['delta']['text']})
        elif typ=='message_delta' and obj.get('delta',{}).get('stop_reason'):packets.append({'finish_reason':obj['delta']['stop_reason']})
        elif typ in ('response.completed','message_stop'):completed=True
        elif typ=='response.incomplete':
            completed=True;packets.append({'finish_reason':obj.get('response',{}).get('incomplete_details',{}).get('reason','incomplete')})
        elif typ in ('error','response.failed'):raise ValueError('Cloud provider could not complete the response.')
    return packets,completed


async def _stream_once(client,provider,model,messages,config):
    # The current dispatch policy supports only the installed local transport.
    # Cloud protocol decoding above remains independently testable.
    from backend.guard import deny_external_generation
    deny_external_generation(provider)
    if provider!='ollama':raise ValueError('provider_transport_not_enabled')
    payload={'model':model,'messages':messages,'stream':True,'think':False,'truncate':False,'shift':False,
             'options':{'num_ctx':config['context_size'],'num_predict':config['max_tokens'],
                        'temperature':config.get('temperature',0.3),
                        **({'seed':config['seed']} if config.get('seed') is not None else {})},'keep_alive':'5m'}
    if config.get('output_schema') is not None:payload['format']=config['output_schema']
    completed=False
    async with client.stream('POST',OLLAMA+'/api/chat',json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:continue
            if len(line)>200000:raise ValueError('provider_frame_size_limit')
            try:obj=json.loads(line)
            except (ValueError,RecursionError):raise ValueError('provider_frame_invalid_json') from None
            packets,done=decode_frame(provider,obj);completed=completed or done
            for packet in packets:yield packet
    if not completed:
        raise ValueError('Provider stream ended before its completion event.')


async def stream(provider,model,messages,config):
    from backend.guard import deny_external_generation
    deny_external_generation(provider)
    local_model_name(model)
    budget=enforce(messages,config)
    if config.get('tools'):raise ValueError('model_tool_execution_is_not_enabled')
    required={'text'}|({'images'} if any(m.get('images') for m in messages) else set())|({'structured_output'} if config.get('output_schema') is not None else set())
    if config.get('output_schema') is not None:
        from backend.output_contracts import validate_schema
        validate_schema(config['output_schema'])
    fallback=config.get('local_fallback_models',[])
    if not isinstance(fallback,list) or len(fallback)>3:raise ValueError('invalid_local_fallback_settings')
    for name in fallback:local_model_name(name)
    generation_limit=config.get('_generation_attempt_limit',3)
    if type(generation_limit) is not int or not 1<=generation_limit<=3:raise ValueError('invalid_generation_attempt_limit')
    candidates=list(dict.fromkeys([model,*fallback]));used=[];events=[];attempts=0;previous_error=None
    deadline=min(float(config.get('_deadline',time.monotonic()+120)),time.monotonic()+120)
    async with LOCAL_ADMISSION.acquire(str(config.get('_runtime_scope','local-owner')),deadline):
        async with asyncio.timeout(max(.001,deadline-time.monotonic())):
            async with httpx.AsyncClient(timeout=httpx.Timeout(120,connect=8),trust_env=False,follow_redirects=False) as client:
                for current in candidates:
                    if attempts>=generation_limit:break
                    check_deadline(deadline)
                    try:
                        info=await _request(client,'POST','/api/show',json={'model':current},deadline=deadline)
                        report=capabilities(current,info)
                        require_capabilities(report,required,config['context_size'])
                    except httpx.HTTPError as exc:
                        previous_error=exc;continue
                    except ValueError:
                        if current==model:raise
                        events.append({'candidate':current,'reason':'fallback_capability_mismatch'});continue
                    if current!=model:
                        decision=fallback_decision({'model':model},report,required=required,mode=config.get('mode','local'),configured_models=fallback,used_models=used,error_kind=failure_kind(previous_error),remaining_seconds=deadline-time.monotonic(),remaining_attempts=generation_limit-attempts,context_size=config['context_size'])
                        events.append(decision)
                        if not decision['allowed']:continue
                    used.append(current)
                    for local_attempt in range(1,min(2,generation_limit)+1):
                        if attempts>=generation_limit:break
                        cooldown=LOCAL_ADMISSION.cooldown()
                        if cooldown>0:await wait_bounded(cooldown,deadline)
                        attempts+=1;started=False;buffer='';terminal=None
                        try:
                            async with aclosing(_stream_once(client,provider,current,messages,config)) as generation:
                                async for packet in generation:
                                    started=True
                                    if config.get('output_schema') is not None:
                                        buffer+=packet.get('content','')
                                        if len(buffer)>100000:raise ValueError('structured_output_size_limit')
                                        if packet.get('finish_reason'):terminal=packet
                                    else:yield packet
                            if config.get('output_schema') is not None:
                                if not terminal or terminal['finish_reason'] not in {'stop','end_turn','stop_sequence','completed'}:
                                    raise ValueError('structured_output_incomplete')
                                validated_output(buffer,config['output_schema'])
                                yield {'content':buffer}
                                yield terminal
                            yield {'metrics':{'provider_runtime':{'provider':provider,'requested_model':model,'selected_model':current,'attempts':attempts,'fallback_used':current!=model,'recovery':events,'context_budget':config.get('_context_budget',budget)}}}
                            return
                        except httpx.HTTPError as exc:
                            previous_error=exc
                            if not replay_allowed(exc,output_started=started):raise
                            delay=RetryPolicy(attempts=2).delay(local_attempt,exc)
                            if failure_kind(exc)=='rate_limited':LOCAL_ADMISSION.defer(delay)
                            events.append({'attempt':attempts,'reason':failure_kind(exc),'wait_seconds':delay})
                            if local_attempt<2 and attempts<generation_limit:await wait_bounded(delay,deadline)
                            else:break
                if previous_error:raise ValueError('provider_generation_unavailable:'+failure_kind(previous_error)) from None
                raise ValueError('no_permitted_equivalent_local_provider')


async def runtime_status(model,config,probe=False):
    """Read metadata; optional explicit probe sends fixed synthetic text only."""
    checked=datetime.now(timezone.utc).isoformat()
    base={'provider':'ollama','account':'local-service','checked_at':checked,'cache':'none; fresh metadata','admission':LOCAL_ADMISSION.snapshot(),
          'external_generation':'blocked_by_existing_guard','write_operations':False,'probe_requested':bool(probe)}
    try:
        local_model_name(model)
        async with httpx.AsyncClient(timeout=3,trust_env=False,follow_redirects=False) as client:
            info=await _request(client,'POST','/api/show',json={'model':model})
            tags=await _request(client,'GET','/api/tags')
            version=await _request(client,'GET','/api/version')
        matched=next((m for m in tags.get('models',[]) if m.get('name')==model or m.get('model')==model),None)
        if not matched or not matched.get('digest'):raise ValueError('installed_model_digest_unavailable')
        report=capabilities(model,info,digest=matched['digest'],provider_version=version.get('version'))
        base.update(report)
        if report['processing']!='local':return {**base,'status':'unavailable','reason':'remote_model_not_permitted'}
        cached=MEASURED.get(report['fingerprint'])
        if cached and time.monotonic()-cached['at']<=3600:base['measured']=cached['measured'];base['probe_checked_at']=cached['checked_at']
        if probe:
            require_capabilities(report,{'text','structured_output'})
            schema={'type':'object','properties':{'value':{'type':'integer','minimum':7,'maximum':7}},'required':['value'],'additionalProperties':False}
            result=''
            probe_config={**config,'context_size':min(2048,report['context_limit'] or 2048),'max_tokens':64,'temperature':0,'seed':5,'output_schema':schema,'local_fallback_models':[],'_deadline':time.monotonic()+45}
            async for packet in stream('ollama',model,[{'role':'user','content':'Return only JSON: {"value":7}. This is synthetic capability test data.'}],probe_config):result+=packet.get('content','')
            passed=validated_output(result,schema)['value']==7
            base['measured']={**report['measured'],'text':'passed_fixed_probe' if passed else 'failed_fixed_probe','structured_output':'passed_fixed_probe' if passed else 'failed_fixed_probe'}
            base['probe_checked_at']=checked
            if len(MEASURED)>=32:MEASURED.pop(next(iter(MEASURED)))
            MEASURED[report['fingerprint']]={'at':time.monotonic(),'checked_at':checked,'measured':base['measured']}
        base['status']='limited' if report['declared']['text'] else 'unavailable'
        base['reason']='Live metadata is not a language-quality benchmark; only explicitly recorded synthetic probes are measured.'
        return base
    except (httpx.HTTPError,ValueError,TypeError,KeyError,TimeoutError):
        return {**base,'status':'unavailable','reason':'local_provider_or_required_capability_unavailable','measured':{'text':'unverified','structured_output':'unverified'}}


def release_local_models(timeout=20):
    """Ask the local runtime to unload the chat models it keeps in memory. Best effort and
    local only: used before a memory-heavy local worker starts, when no conversation is
    active. Returns the names that were asked to unload; never raises."""
    released = []
    try:
        with httpx.Client(timeout=timeout) as client:
            for item in client.get(f'{OLLAMA}/api/ps').json().get('models', [])[:8]:
                name = item.get('name')
                if isinstance(name, str) and name:
                    client.post(f'{OLLAMA}/api/generate', json={'model': name, 'keep_alive': 0})
                    released.append(name)
    except Exception:
        pass
    return released

