"""Capability and fallback decisions from trusted local adapter metadata."""
from __future__ import annotations
import hashlib
import json
import re


def local_model_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
        raise ValueError('installed_local_model_name_required')
    if ':cloud' in value.lower() or '-cloud' in value.lower():
        raise ValueError('remote_model_not_permitted')
    return value


def capabilities(model, info, *, digest=None, provider_version=None):
    local_model_name(model)
    if not isinstance(info, dict) or not isinstance(info.get('capabilities'), list) or any(not isinstance(x,str) for x in info['capabilities']):
        raise ValueError('model_capability_metadata_invalid')
    remote = bool(info.get('remote_model') or info.get('remote_host'))
    caps = set(info['capabilities'])
    metadata = info.get('model_info', {})
    if not isinstance(metadata,dict):
        raise ValueError('model_capability_metadata_invalid')
    lengths = [v for k,v in metadata.items() if k.endswith('.context_length') and type(v) is int and 1 <= v <= 10**7]
    limit = min(lengths) if lengths else None
    identity = {'model':model,'digest':digest,'version':provider_version,'capabilities':sorted(caps),
                'context_limit':limit,'remote':remote,'modified_at':info.get('modified_at')}
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'provider':'ollama','model':model,'processing':'remote' if remote else 'local',
            'fingerprint':fingerprint,'model_digest':digest,'provider_version':provider_version,
            'declared':{'text':'completion' in caps and not remote,'images':'vision' in caps and not remote,
                        'tools':'tools' in caps and not remote,'audio':False,
                        'structured_output':'completion' in caps and not remote},
            'declaration_source':'Ollama /api/show; structured output is adapter protocol support, not model accuracy',
            'context_limit':limit,'context_limit_known':limit is not None,
            'measured':{'text':'unverified','images':'unverified','tools':'unverified','audio':'unsupported_by_adapter','structured_output':'unverified'}}


def require_capabilities(report, required, context_size=None):
    if report.get('processing') != 'local':
        raise ValueError('remote_model_not_permitted')
    for kind in required:
        if report.get('declared',{}).get(kind) is not True:
            raise ValueError('required_model_capability_unavailable:'+kind)
    if context_size is not None and report.get('context_limit') is not None and context_size > report['context_limit']:
        raise ValueError('requested_context_exceeds_declared_model_limit')


def fallback_decision(primary, candidate, *, required, mode, configured_models, used_models,
                      error_kind, output_started=False, remaining_seconds=0, remaining_attempts=0,
                      operation='generation', outcome_unknown=False, context_size=None):
    reason=None
    if operation!='generation' or outcome_unknown or output_started:reason='effect_or_output_may_exist'
    elif error_kind not in {'temporarily_unavailable','connection_unavailable','rate_limited'}:reason='error_not_eligible'
    elif candidate.get('provider')!='ollama' or candidate.get('processing')!='local':reason='external_fallback_not_authorized'
    elif candidate.get('model') not in configured_models or candidate.get('model') in used_models:reason='fallback_not_selected_or_already_used'
    elif remaining_seconds<=0 or remaining_attempts<=0:reason='fallback_budget_exhausted'
    else:
        try:require_capabilities(candidate,required,context_size)
        except ValueError:reason='fallback_capability_mismatch'
    return {'allowed':reason is None,'reason':reason or 'explicit_equivalent_local_fallback',
            'from':primary.get('model'),'to':candidate.get('model'),'privacy_mode':mode,
            'quality_equivalence':'not_measured; declared operation capability only'}
