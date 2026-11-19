"""Read-only views of the actual shared runtime controls, not a second engine."""
from pathlib import Path
from typing import Any
import importlib.util,json
from pydantic import BaseModel,ConfigDict,Field,model_validator
from backend import guard,skill_registry,skill_execution
from backend.db import get_db,data_dir,settings

ROOT=Path(__file__).resolve().parents[1]
SKILLS=frozenset('skill-catalog skill-dependency-resolution skill-conflict-resolution skill-trigger-testing skill-versioning skill-release-gate model-capability-check token-budgeting structured-output-validation provider-fallback retry-backoff idempotent-actions execution-timeouts concurrency-control rate-limit-handling connector-health workflow-state-machine audit-events tool-contract-testing ci-pipeline environment-variable-audit service-monitoring backup-disaster-recovery llm-evaluation-dataset skill-regression-suite'.split())

class SystemInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation:str
    skills:list[str]=Field(default_factory=list,max_length=8)
    taskId:str|None=Field(default=None,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    constraints:dict[str,Any]=Field(default_factory=dict,max_length=5)
    schemaDefinition:dict[str,Any]|None=None
    @model_validator(mode='after')
    def check(self):
        if self.operation not in SKILLS:raise ValueError('Unsupported system inspection')
        if self.schemaDefinition is not None:
            from backend.output_contracts import validate_schema
            validate_schema(self.schemaDefinition)
        if set(self.constraints)-{'language','format','processing','outputs','read_only'}:raise ValueError('Unsupported composition constraint')
        return self

def route(request):
    # Explicit technical identifiers only: normal user operations retain their
    # existing router. The capability cards supply an exact system operation.
    text=request.strip().lower()
    return [s for s in sorted(SKILLS) if text==s or text.startswith(s+' ')]

def validate_activation(selected,manifest):
    required={s.get('release_profile') for s in manifest['skills'] if s['slug'] in selected and s.get('release_profile')}
    if not required:return
    from backend.skill_release import stored_decision
    gate=stored_decision(ROOT,data_dir())
    if required!={'batch05-fast'} or not gate['approved']:raise ValueError('release_gate_not_approved_for_current_source')

def discovery(manifest,*,source_count=0):
    from backend.skill_transform import status
    from backend.skill_release import stored_decision
    transform=status();tool_ids={x for s in manifest['skills'] for x in s.get('tools',[])}
    # Fixed local handlers are present. Specialized runtime/inputs are checked
    # independently; the existence of an identifier is not a provider probe.
    local={'planning-engine','local-office-writer','guard','skill-contracts','skill-lifecycle','skill-loader','skill-readback','skill-artifacts','skill-file-versions','os-credential-store','productivity-planning','productivity-schedule','productivity-meetings','productivity-mail','system-runtime','local-summary','registered-source-evidence','project-memory','project-extracted-content','private-hybrid-retrieval','document-ingestion','coding-context','registered-static-web','approved-catalogue-research'}
    tools={t:{'available':t in local,'operations':[]} for t in tool_ids}
    for t in ('isolated-data-transform','isolated-document-transform'):tools[t]={'available':bool(transform.get('available')),'operations':[]}
    native=all(importlib.util.find_spec(x) is not None for x in ('docx','openpyxl','pptx'))
    dependencies={'native-ooxml-writer':native,'extracted-authorized-files':source_count>0}
    gate=stored_decision(ROOT,data_dir())
    releases={s['slug']:gate for s in manifest['skills'] if s.get('release_profile')=='batch05-fast'}
    result=skill_registry.discovery(manifest,ROOT,tool_status=tools,dependency_status=dependencies,release_status=releases)
    result['readiness_scope']='Bundled handler and declared dependencies; actual resources, destination permission and operation-specific providers are rechecked on execution.'
    result['release_gate']=gate
    return result

async def inspect(operation,inputs,who,pid,rid,check):
    from backend.skill_core import catalog
    from backend.skill_observability import snapshot,audit_trace
    from backend.skill_release import stored_decision
    from backend.skill_pinning import pin_receipt
    from backend import providers
    from backend.skills import checked_row,recheck
    config=SystemInput.model_validate(inputs.systemOperation or {'operation':operation})
    if config.operation!=operation:raise ValueError('System operation and selected skill differ')
    check();manifest=catalog();targets=config.skills or ['intent-routing']
    with get_db() as c:
        row=checked_row(c,pid,config.taskId or rid,who);recheck(c,row,who)
        doc=json.loads(row['document'])
        if operation=='audit-events':out=audit_trace(c,who,pid,row['id'])
        elif operation=='service-monitoring':out=snapshot(c,who,pid)
        elif operation in {'idempotent-actions','execution-timeouts','concurrency-control','workflow-state-machine'}:
            out={'control':skill_execution.public_control(c,row),'state':row['state'],'operation_id':row['id'],'idempotency':'owner/project/key + parameter fingerprint; uncertain effects are not replayed','transitions':{k:sorted(v) for k,v in skill_execution.TRANSITIONS.items()},'scope':'existing skill_runs executor; no external exactly-once guarantee'}
        else:out=None
    limited=False
    if out is None:
        if operation=='skill-catalog':
            current=discovery(manifest);out={**current,'skills':[{k:v for k,v in s.items() if k not in {'input_schema','output_schema'}} for s in current['skills']]}
        elif operation=='skill-dependency-resolution':out=skill_registry.resolve_dependencies(targets,manifest)
        elif operation=='skill-conflict-resolution':out=skill_registry.resolve_conflicts(targets,manifest,constraints=config.constraints or {'processing':'local-only'})
        elif operation=='skill-versioning':out=pin_receipt(doc['package_pin']) if doc.get('package_pin') else {'state':'legacy_unpinned'}
        elif operation in {'model-capability-check','connector-health'}:
            cfg=settings();out=await providers.runtime_status(cfg['model'],cfg,probe=False);limited=True
        elif operation=='token-budgeting':
            from backend.context_budget import receipt
            out=receipt([{'role':'user','content':inputs.text}],settings());limited=True
        elif operation=='structured-output-validation':
            if config.schemaDefinition is None:out={'state':'input_required','required':'schemaDefinition and text','executed_tools':0};limited=True
            else:
                from backend.output_contracts import OutputContract
                out=OutputContract.model_validate({'schema':config.schemaDefinition}).check(inputs.text,0)
                limited=not out['schema_valid']
        elif operation=='environment-variable-audit':
            from backend.environment_audit import audit_environment
            out=audit_environment(ROOT);limited=out['state']!='checked'
        elif operation in {'provider-fallback','retry-backoff','rate-limit-handling'}:
            cfg=settings();out={'configured_local_fallback_models':cfg.get('local_fallback_models',[]),'cloud_fallback':False,'unknown_effect_retry':False,'live_runtime':await providers.runtime_status(cfg['model'],cfg,probe=False)};limited=True
        elif operation=='backup-disaster-recovery':
            out={'implementation':'existing Guard sealed backup and quarantined restore','scope':'SQLite plus database-persisted file bytes; external workspaces and OS credentials excluded','restore_executed':False,'required_action':'Use the existing Guard backup interface or the documented local recovery CLI; this inspection does not copy or replace a database.'};limited=True
        else:
            out={'gate':stored_decision(ROOT,data_dir()),'profile':'batch05-fast','execution_started':False,'command':'python scripts/run_skill_regression.py --output NEW_REPORT_DIRECTORY --ci','scope':'Read-only evidence view; fixed operator runner performs tests separately.'}
            limited=not out['gate']['approved']
    check()
    if out.get('blockers') or out.get('clarifications') or out.get('ready') is False:limited=True
    return out,[],limited,'فُحصت حالة المكوّن الفعلية ضمن النطاق المبين. لا ينفذ فحص الحالة إرسالًا أو استعادة أو نشرًا.'
