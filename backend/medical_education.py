"""Explicit, bounded education; never diagnosis, prescribing or clinical approval.

No model call, network request, content persistence, memory extraction or training.
Only fixed source references and declared input fields influence this path.
"""
from datetime import datetime,timezone
from fractions import Fraction
import hashlib,json
from pathlib import Path
from backend.medical_schema import Explain,TOPICS

VERSION='medical-education-v1'
ROOT=Path(__file__).resolve().parents[1]

def registry():
    raw=(ROOT/'config/medical_sources.json').read_bytes()
    if len(raw)>32768:raise ValueError('Medical source registry exceeds limit')
    value=json.loads(raw)
    if value.get('version')!='medical-education-sources-v1':raise ValueError('Unsupported medical source registry')
    return value,hashlib.sha256(raw).hexdigest()

def capabilities():
    source,fingerprint=registry()
    return {'version':VERSION,'topics':list(TOPICS),'languages':['ar','en'],'levels':['simple','detailed','technical'],
        'input_mode':'Explicit topic and structured fields; not comprehensive free-text symptom triage',
        'clinical_use_approved':False,'qualified_medical_review':False,'live_guidance_verified':False,
        'storage':'ephemeral response only','training':False,'network_requests':0,'registry_sha256':fingerprint,
        'sources':source['sources'],'limitations':['No diagnosis or individual dose selection','No raw medical-image interpretation','No clinical calculator approved','Dynamic drug warnings need a current official source outside this offline path']}

def explain(request:Explain):
    ar=request.language=='ar'
    def text(a,b):return a if ar else b
    result={'version':VERSION,'execution_status':'completed','review_state':'educational_source_supported',
        'request_context':{'topic':request.topic,'purpose':request.purpose,'level':request.level,'language':request.language,'input_mode':'explicit_structured_fields'},
        'summary':'','definitions':[],'steps':[],'example':None,'limitations':[],
        'missing_fields':[],'result':{},'verification':[], 'evidence_records':[],
        'privacy':{'content_saved':False,'personal_memory_written':False,'project_memory_written':False,'training_used':False,'external_data_sent':False},
        'clinical_review':{'qualified_review_completed':False,'clinical_use_approved':False},
        'generated_at':datetime.now(timezone.utc).isoformat()}
    evidence=[]
    def cite(identifier,claims):evidence.append((identifier,claims))
    topic=request.topic
    # An explicitly declared urgent combination takes precedence over every
    # selected lesson, level or computational operation. No diagnosis asserted.
    if request.severe_persistent_chest_pain and request.shortness_of_breath:
        result['review_state']='urgent_help'
        result['summary']=text('اطلب المساعدة الطبية الطارئة المحلية الآن؛ ألم صدري شديد مستمر مع ضيق تنفس يحتاج تقييمًا عاجلًا. لا تنتظر اكتمال هذا الشرح.','Seek local emergency medical help now. Severe persistent chest pain with shortness of breath needs urgent assessment; do not wait to finish this explanation.')
        result['limitations']=[text('لم يُحدَّد بلدك، لذلك لا أذكر رقمًا للطوارئ. هذا توجيه لطلب المساعدة وليس تشخيصًا.','Your country is unspecified, so no emergency number is supplied. This is a direction to seek help, not a diagnosis.')]
        cite('nhs-chest',['summary'])
    elif topic=='symptom_diagnosis':
        result['summary']=text('العَرَض هو ما يشعر به الشخص، مثل الغثيان. التشخيص هو تحديد الحالة التي تفسر ما يحدث بعد تقييم المعلومات المناسبة. وجود عرض لا يحدد سببه وحده.','A symptom is an experience such as nausea. Diagnosis identifies the condition through assessment. A symptom alone does not identify its cause.')
        result['definitions']=[text('العرض: تجربة يبلغ عنها الشخص.','Symptom: an experience reported by the person.'),text('التشخيص: عملية تحديد الحالة، وقد تستعين بالتاريخ والفحص والاختبارات.','Diagnosis: identifying a condition, potentially using history, examination and tests.')]
        result['example']=text('«أشعر بالغثيان» وصف لعرض؛ لا يخبرنا وحده ما السبب.','“I feel nauseated” describes a symptom; it does not identify the cause.')
        if request.level!='simple':
            result['steps']=[text('نبدأ بما يصفه الشخص، ثم نجمع المعلومات المناسبة عن بدايته وسياقه.','Start with the reported experience, then gather relevant information about its onset and context.'),text('التشخيص يربط هذه المعلومات بالتاريخ والفحص والاختبارات المناسبة؛ لا يُستنتج اسم المرض من اسم العرض وحده.','Diagnosis relates that information to history, examination and appropriate tests; a symptom name alone does not determine a disease.')]
        if request.level=='technical':
            result['definitions'].append(text('العلامة: معلومة يمكن ملاحظتها أو قياسها في الفحص أو الاختبار؛ تختلف عن تجربة العرض التي يصفها الشخص.','Sign: information observable or measurable through examination or testing, distinct from the reported experience of a symptom.'))
            result['steps'].append(text('قد تدخل العلامات والأعراض والتاريخ ونتائج الاختبارات في التقييم نفسه؛ قوة الاستنتاج تعتمد على مجموع الأدلة، ولا يقدّم هذا المثال تشخيصًا لشخص.','Signs, symptoms, history and test findings may contribute to the same assessment. The conclusion depends on the evidence together; this example does not diagnose a person.'))
            cite('nci-sign',['definitions'])
        cite('nci-symptom',['summary','definitions','example']);cite('nci-diagnosis',['summary','definitions'])
    elif topic=='lab_result':
        lab=request.lab
        required=('name','value','unit','reference_low','reference_high','reference_unit','sample_date')
        result['missing_fields']=[field for field in required if lab is None or getattr(lab,field) is None]
        if lab and lab.reading_uncertain:
            result['review_state']='needs_data';result['summary']=text('قراءة الرقم أو الرمز غير محسومة. صحّح القيمة والوحدة من التقرير قبل المقارنة.','The number or symbol is uncertain. Confirm the value and unit from the report before comparison.')
            result['missing_fields']=['confirmed_reading']
        elif result['missing_fields']:
            result['review_state']='needs_data';result['summary']=text('يلزم اسم الفحص والقيمة والوحدة والمجال المرجعي ووحدته وتاريخ العينة كما في التقرير. الرقم وحده لا يكفي.','Provide the test name, value, unit, report reference range with its unit, and sample date. A number alone is insufficient.')
        elif lab.unit.strip()!=lab.reference_unit.strip():
            result['review_state']='needs_data';result['missing_fields']=['matching_reference_unit'];result['summary']=text('وحدة النتيجة لا تطابق وحدة المرجع المقدمة. لا يمكن المقارنة دون توحيد موثَّق للوحدات.','The supplied result and reference units differ. Comparison needs a documented unit match.')
        else:
            value,low,high=map(Fraction,(lab.value,lab.reference_low,lab.reference_high))
            position='below' if value<low else 'above' if value>high else 'within'
            label={'below':text('أقل من','below'),'above':text('أعلى من','above'),'within':text('ضمن','within')}[position]
            result['summary']=text(f'القيمة {lab.value} {lab.unit} {label} المجال الذي أدخلته من التقرير ({lab.reference_low}–{lab.reference_high} {lab.reference_unit}). هذه مقارنة عددية وليست تشخيصًا.',f'The value {lab.value} {lab.unit} is {label} the supplied report range ({lab.reference_low}–{lab.reference_high} {lab.reference_unit}). This is a numerical comparison, not a diagnosis.')
            result['result']={'position':position,'report':lab.model_dump(),'reference_origin':'user_supplied_not_independently_verified'}
            result['verification']=[{'kind':'exact_rational_comparison','performed':True,'scope':'Supplied value and same-unit range only'}]
        result['limitations']=[text('خارج المرجع لا يثبت مرضًا، وداخله لا يضمن غياب المشكلة. تفسير النتيجة يحتاج السياق المناسب.','Outside-range results do not alone establish disease; inside-range results do not guarantee health. Interpretation needs context.')]
        cite('medline-lab',['limitations'])
    elif topic=='chest_symptoms':
        result['review_state']='needs_professional_review'
        result['summary']=text('هذا المسار لا يقيّم الأعراض الحرة ولا يستبعد الخطر. اطلب تقييمًا طبيًا للألم الصدري؛ عند ألم مستمر شديد مع ضيق تنفس اطلب المساعدة الطارئة فورًا.','This path does not assess unrestricted symptoms or rule out danger. Seek medical assessment for chest pain; severe persistent pain with shortness of breath needs emergency help now.')
        cite('nhs-chest',['summary'])
    elif topic=='medication_question':
        result['review_state']='needs_professional_review'
        result['summary']=text('لا أختار جرعة شخصية هنا. تحقّق مع الصيدلي أو الطبيب من الاسم الدقيق والمادة الفعالة والتركيز والشكل وطريق الاستخدام، ومن معلومات الطفل أو الحالة اللازمة للقرار.','An individual dose is not selected here. Ask a pharmacist or clinician to verify the exact name, active ingredient, concentration, form and route, and the patient details needed for the decision.')
        result['missing_fields']=['verified_active_ingredient','verified_concentration_and_form','relevant_patient_context']
        result['limitations']=[text('الاسم التجاري وحده لا يكفي، ولا تُغيَّر وصفة موجودة بهذا المسار.','A brand name alone is insufficient; this path does not change a prescription.')]
    elif topic=='mass_conversion':
        q=request.quantity
        if q is None:
            result['review_state']='needs_data';result['missing_fields']=['quantity'];result['summary']=text('حدد القيمة ووحدتي الكتلة للتحويل التعليمي.','Supply the value and both mass units for the educational conversion.')
        elif 'mL' in (q.from_unit,q.to_unit):
            result['review_state']='needs_data';result['missing_fields']=['verified_concentration'];result['summary']=text('الكتلة والحجم كميتان مختلفتان. التحويل بين mg وmL يحتاج تركيزًا صحيحًا؛ هذا المسار لا يختار جرعة أو تركيزًا.','Mass and volume are different quantities. Converting mg to mL requires a verified concentration; this path does not select a dose or concentration.')
        else:
            scale={'mcg':Fraction(1,1000),'mg':Fraction(1),'g':Fraction(1000)}
            factor=scale[q.from_unit]/scale[q.to_unit]
            from backend.check_worker import Arithmetic
            calculation=Arithmetic(f'({q.value})*({factor.numerator}/{factor.denominator})').result()
            exact=Fraction(calculation['exact'])
            if exact/factor!=Fraction(q.value):raise ValueError('Independent unit inverse check failed')
            result['review_state']='mathematically_verified'
            result['result']={'exact':calculation['exact'],'decimal':calculation['decimal'],'decimal_exact':calculation['decimal_exact'],'unit':q.to_unit,'factor':str(factor),'clinical_suitability_checked':False}
            result['summary']=f"{q.value} {q.from_unit} = {calculation['decimal']} {q.to_unit}."
            result['steps']=[text(f'نضرب في عامل التحويل الدقيق {factor}.',f'Multiply by the exact conversion factor {factor}.')]
            result['verification']=[{'kind':'existing_bounded_arithmetic','performed':True,'record':calculation},{'kind':'inverse_unit_check','performed':True}]
            result['limitations']=[text('تحويل وحدات تعليمي؛ لا يثبت ملاءمة جرعة لأي شخص.','Educational unit conversion; this does not establish an appropriate dose for any person.')]
    elif topic=='clinical_calculator':
        result['review_state']='unsupported'
        result['summary']=text('لا توجد آلة حاسبة سريرية معتمدة في هذا المسار. يلزم تعريف الصيغة وإصدارها ومصدرها والسكان الذين تحققت لديهم وكل المدخلات المطلوبة، ثم مراجعة مختصة قبل الاستخدام الشخصي.','No clinical calculator is approved in this path. A formula needs its version, source, validated population, required inputs and qualified review before individual use.')
        result['missing_fields']=['reviewed_formula_and_population','all_required_inputs'];cite('who-lmm',['clinical_review'])
    elif topic=='source_contexts':
        result['review_state']='unverified_source_context'
        if len(request.source_contexts)!=2:
            result['missing_fields']=['two_source_contexts'];result['summary']=text('أدخل وصف المصدرين وبلد كل منهما وسكانه وتاريخ سريانه للمقارنة.','Provide two source descriptions with country, population and effective date.')
        elif any(getattr(source,key) is None for source in request.source_contexts for key in ('country','population','effective_date')):
            result['missing_fields']=[f'source_contexts[{index}].{key}' for index,source in enumerate(request.source_contexts) for key in ('country','population','effective_date') if getattr(source,key) is None]
            result['summary']=text('بعض بيانات السياق غير معروفة؛ لا يمكن استنتاج توافق المصدرين من حقول مفقودة. أدخل البلد والسكان وتاريخ السريان عند توفرها.','Some context fields are unknown; missing fields do not establish source agreement. Supply country, population and effective date when available.')
        else:
            a,b=request.source_contexts
            differences=[key for key in ('country','population','effective_date') if getattr(a,key)!=getattr(b,key)]
            result['result']={'supplied_sources':[s.model_dump() for s in request.source_contexts],'different_fields':differences,'source_contents_read':False}
            result['summary']=text('أوصاف المصدرين تختلف في: '+('، '.join(differences) if differences else 'لا اختلاف ظاهر في الحقول المقدمة')+'. لم تُفحص الصفحتان أو التوصيات هنا؛ لا تُدمج توصيتان علاجيّتان من هذه البيانات.', 'The supplied source descriptions differ in: '+(', '.join(differences) if differences else 'no supplied field')+'. Their contents and recommendations were not inspected here; these metadata do not justify merging treatments.')
        result['limitations']=[text('وصف المستخدم ليس توثيقًا مستقلًا. استخدم قراءة المصدر المأذونة وقارنه بسياق السؤال قبل تقرير الملاءمة.','User-supplied descriptions are not independent verification. Read authorized sources and compare their scope before deciding applicability.')]
    elif topic=='unclear_report':
        result['review_state']='needs_data' if request.image_kind=='report' else 'unsupported'
        result['summary']=text('صحّح الرقم أو العلامة العشرية أو الوحدة غير المقروءة من التقرير؛ لا أبني تفسيرًا على تخمينها.','Confirm the unreadable number, decimal mark or unit in the report; interpretation cannot rest on a guessed reading.') if request.image_kind=='report' else text('هذا المسار لا يقدم قراءة تشخيصية للصورة الطبية الخام. يمكن شرح تقرير الاختصاصي المكتوب وحدوده عند توفره.','This path does not diagnose from a raw medical image. A specialist’s written report can be explained when available.')
        result['missing_fields']=['confirmed_report_text'];cite('who-lmm',['clinical_review'])
    elif topic=='current_drug_warning':
        result['review_state']='current_evidence_unavailable'
        result['summary']=text('لم يُتحقق هنا من أحدث تحذير دوائي. هذا المسار لا يجلب تحديثات مباشرة؛ يلزم المصدر الدوائي الرسمي الحالي للبلد والمنتج المقصود أو التحقق مع الصيدلي.','The latest drug warning has not been checked here. This path does not fetch live updates; use the current official medicine source for the relevant country and product or verify with a pharmacist.')
        result['limitations']=[text('لا أستنتج من غياب الدليل أن الدواء آمن، ولا أوصي بتغيير علاج شخصي.','Absence of evidence here does not establish safety or justify changing personal treatment.')]
    elif topic=='observational_study':
        result['summary']=text('الدراسة الرصدية تتابع التعرضات والنتائج دون إسناد تدخل تجريبي. وجود ارتباط في ملخصها وحده لا يثبت السببية.','An observational study measures exposures and outcomes without assigning an experimental intervention. An association in its summary alone does not establish causation.')
        result['steps']=[text('افحص ترتيب الزمن، والمربكات، وطريقة اختيار العينة، وحجم الأثر وعدم يقينه.','Examine timing, confounding, sample selection, effect size and uncertainty.'),text('اطلب طرق الدراسة ونتائجها الكاملة؛ لم تُراجع دراسة بعينها في هذا الشرح.','Review the full methods and results; no particular study was reviewed in this explanation.')]
        result['definitions']=[text('التصميم الرصدي: ملاحظة الأفراد أو قياس النتائج دون تعيين تدخل تجريبي.','Observational design: observing individuals or measuring outcomes without assigning an experimental intervention.')]
        if request.level=='technical':
            result['steps'].append(text('المربك عامل يرتبط بالتعرض والنتيجة وقد يفسر جزءًا من الارتباط؛ مجرد وجود ارتباط لا يحسم اتجاه السببية أو يستبعد تفسيرًا بديلًا. هذه أسئلة نقدية، لا نتيجة مراجعة لدراسة بعينها.','A confounder relates to exposure and outcome and can explain part of an association. Association alone does not settle causal direction or rule out an alternative explanation. These are critical appraisal questions, not findings from reviewing a particular study.'))
        cite('nci-observational',['definitions'])
    if request.level=='simple':result['definitions']=[]
    if request.optional_understanding_check and result['review_state'] not in ('urgent_help','needs_data','unsupported'):
        result['understanding_check']=text('اختياري: كيف تشرح الفكرة بكلماتك؟ الهدف التأكد من وضوح شرحي، وليس اختبارك.','Optional: how would you explain the idea in your own words? This checks my explanation, not you.')
        cite('ahrq-teach-back',['understanding_check'])
    sources,fingerprint=registry();by_id={s['id']:s for s in sources['sources']}
    result['registry_sha256']=fingerprint
    result['evidence_records']=[{'source':by_id[key],'supports':claims,'freshness':'reference_reviewed_at_development_not_live_at_answer','clinical_applicability_certified':False} for key,claims in evidence]
    # Structured values, prose and sources are returned together; nothing is
    # copied into project memory, learning candidates, examples or transcripts.
    return result
