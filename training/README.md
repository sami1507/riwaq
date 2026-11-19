# تخصيص رواق — تجربة QLoRA فعلية، ومرشح مرفوض

اكتمل التدريب المحلي في 14 نوفمبر 2026. تغيرت معاملات LoRA وحُفظت وأعيد تحميلها للاستدلال الفعلي. لم ينجح المرشح في بوابة الجودة، ولذلك بقي نموذج التطبيق الأصلي دون محوّل. التفاصيل والمقارنات في [تقرير النتائج](../docs/TRAINING_RESULTS.md).

## ما استُخدم

- Windows، RTX 5060 بذاكرة8151MiB، RAM31.84GiB.
- بيئة مستقلة: `D:/RiwaqRuntime/training-v1/venv`. PyTorch2.7.1+cu128، Transformers4.57.1، PEFT0.17.1، bitsandbytes0.48.1؛ بقية الإصدارات في requirements-runtime.lock.txt.
- Qwen3-4B-Instruct-2507، Apache2.0، أصول رسمية عند revision `cdbee75f17c01a7cc42f958dc650907174af0554`.
- أُعيد بناء BF16 من GGUF Q4_K_M الموجود، ثم تحميله NF4 مع double quantization. هذه **ليست الأوزان الأصلية غير المكممة**؛ لا يُدرَّب GGUF مباشرة.
- المحوّل: آخر أربع طبقات، q_proj/v_proj، rank4 وalpha8 وdropout0.05؛163840 معاملًا قابلًا للتدريب. القاعدة مجمدة.
- 64 خطوة، batch1، accumulation8، طول256، lr0.0001، seed37. أطول سجل تدريب237 رمزًا، والتحقق239؛ لا اقتطاع.

## البيانات والحقوق

data-v3 هي النسخة المستخدمة:508 تدريب في21 عائلة،44 تحقق في6 عائلات،46 اختبار في6 عائلات منفصلة. الأمثلة مولدة بقواعد أصلية قابلة للفحص، لا بمحادثات المستخدم ولا بقاعدة المعرفة. راجع DATA_RIGHTS.md وmanifest.json؛ وسم project-original يصف الأصل والإذن الداخلي ولا يدّعي ترخيص طرف ثالث.

كل598 مثالًا اجتاز المدقق.200 جواب تدريب ثابت ضمن8 عائلات؛ لذلك لا يعادل العدد508 مسائل مستقلة. مراجعة هندسية بواسطة AI شملت20 مثالًا، وليست مراجعة بشرية مستقلة. v1 مستبعدة لتكرارات، وv2 تشخيصية؛ v3 تصلح بيانات مخطط nullable دون تغيير الأجوبة.

```powershell
.venv/Scripts/python.exe -X utf8 training/verify_data_v1.py
```

اسم المدقق تاريخي؛ يتحقق الآن من v3. sample.schema.json وvalidate_data.py بوابة منفصلة لإدخال بيانات جديدة مع تصريح ومراجعة، وليسا صيغة بيانات هذه التجربة الإجرائية.

**مجموعة الاختبار استُخدمت الآن للتقييم النهائي.** لا تعدّل النظام بناءً على أجوبتها ثم تصفها بأنها اختبار محجوب جديد. تجربة لاحقة تحتاج عائلات اختبار مستقلة. لا يدخل أي تقسيم من بيانات التجربة فهرس الإنتاج تلقائيًا.

## إعادة التشغيل على هذا الجهاز

الأوامر التالية تعيد التجربة في مجلدات جديدة، ولا تغيّر نموذج التطبيق. لا حاجة لتنزيل آخر. لا تشغّل التدريب وOllama الكبير معًا على GPU إذا ضاقت الذاكرة.

```powershell
$trainRuntime = 'D:/RiwaqRuntime/training-v1'
$trainPython = "$trainRuntime/venv/Scripts/python.exe"
$trainManifest = '506643d2fd591447bc9afa0053292e14cdfc4af42cb3d293d3634ca68d1dc971'
$trainCommon = @('--base', "$trainRuntime/reconstructed-base", '--data', 'training/data-v3', '--manifest-sha', $trainManifest, '--device', 'cuda', '--max-length', '256', '--rank', '4', '--last-layers', '4', '--accumulation', '8', '--learning-rate', '0.0001', '--seed', '37', '--max-seconds', '1800', '--max-rss-gib', '12', '--validate-every', '8', '--patience', '2', '--min-delta', '0.0001')
& $trainPython -X utf8 -B training/runtime_train.py @trainCommon --steps 4 --pause-after 2 --output "$trainRuntime/runs/replay-pilot-part1"
& $trainPython -X utf8 -B training/runtime_train.py @trainCommon --steps 4 --resume "$trainRuntime/runs/replay-pilot-part1/checkpoint-0002" --output "$trainRuntime/runs/replay-pilot-part2"
& $trainPython -X utf8 -B training/runtime_train.py @trainCommon --steps 64 --output "$trainRuntime/runs/replay-sft64-v3"
```

المجلد الموجود يُرفض بدل الكتابة فوقه. ملف STOP الفارغ داخل مجلد التشغيل طلب إيقاف تعاوني يُفحص بين الدفعات الصغيرة؛ قد ينتظر تحميل النموذج أو انتهاء التحقق. بعد وجود خطوة optimizer مكتملة تُلغى التدرجات الجزئية ويُحتفظ بحد مكتمل قابل للحفظ مع المؤشر والترتيب وحالات RNG؛ قبل أول خطوة لا توجد نقطة استئناف. الإيقاف القسري بمراقب الوقت/الذاكرة يحتفظ بآخر checkpoint مكتوب فقط، وقد يفقد العمل الأحدث غير المحفوظ. الاستئناف يتطلب نفس الوصفة وحد الخطوات والبصمات؛ لا يمكن تمديد64 إلى128 بمجرد تغيير --steps. حد الوقت يحتسب الزمن المحفوظ في checkpoint؛ لا يستعيد زمن العمل غير المحفوظ قبل انقطاع قسري. المراقب ليس عزل نظام تشغيل، ولا ضمان لتطابق CUDA بتًّا رغم البذرة الثابتة.

تختار التجربة checkpoint بحسب loss للتحقق كل8 خطوات مع patience2؛ هذا اختيار مرشح للتقييم وليس اعتمادًا. في التشغيل الفعلي كان checkpoint0064 الأفضل ولم تتفعّل قاعدة الإيقاف المبكر.

## التقييم والاستدلال

scripts/evaluate_training_v3.py يقارن مسارات التطبيق عبر FastAPI وOllama، مستقلًا عن تدريب المحوّل. scripts/evaluate_lora_validation.py يقارن القاعدة المعاد بناؤها بالمحوّل بنفس إعداد NF4 وgreedy. scripts/evaluate_lora_heldout.py يفرض قرار اختيار مجمدًا قبل فتح الاختبار؛ المجلدات وعلامة heldout-started تمنع إعادة الكتابة.

الأوامر المسجلة للتجربة الأصلية، وليست تعليمات لإعادة ضبطها على الاختبار:

```powershell
& $trainPython -X utf8 -B scripts/evaluate_lora_validation.py --python $trainPython --base "$trainRuntime/reconstructed-base" --run "$trainRuntime/runs/sft64-v3" --data training/data-v3 --manifest-sha $trainManifest --retention training/retention-v1.json --output "$trainRuntime/evaluation/validation-v1"
& $trainPython -X utf8 -B scripts/evaluate_lora_heldout.py --python $trainPython --base "$trainRuntime/reconstructed-base" --run "$trainRuntime/runs/sft64-v3" --data training/data-v3 --manifest-sha $trainManifest --selection reports/training-lora-selection.json --selection-sha 25128e8c5bae3f4b3190adb583a3252f3f4e620f35ef8f5a54d812d900b4572d --validation-report "$trainRuntime/evaluation/validation-v1/report.json" --output "$trainRuntime/evaluation/heldout-v1"
'{"id":"demo","messages":[{"role":"user","content":"Say hello in Arabic."}],"max_new_tokens":64}' | & $trainPython -X utf8 -B training/runtime_infer.py --base "$trainRuntime/reconstructed-base" --device cuda --max-requests 1
```

إضافة --adapter مع مسار runs/sft64-v3/checkpoint-0064 تحمّل المحوّل البحثي المرفوض صراحة. حذف ذلك الخيار هو الرجوع للقاعدة في هذه الأداة؛ لا يبدّل نموذج Ollama ولا إعدادات التطبيق.

## الملفات والنقل إلى جهاز آخر

- القاعدة المعاد بناؤها: D:/RiwaqRuntime/training-v1/reconstructed-base؛16 shard ونحو8.045GB من الأوزان **ولدت محليًا**.
- المحوّل وأدلة الاستئناف: runs/sft64-v3/checkpoint-0064؛ نحو2.06MB تشمل optimizer/RNG وtraining_state.
- حزمة المحوّل منفصلة وموسومة rejected. حزمة المصدر/الويب لا تتضمن البيئة أو الأوزان الكبيرة أو قاعدة بيانات المستخدم.
- requirements-runtime.lock.txt سجل بيئة محلية يحوي روابط file:///D:/ للأدوات؛ ليس ملف تثبيت محمولًا دون wheel cache.
- runtime_setup.py ينزّل فقط الحزم المحددة من مصادر رسمية، مع بصمات وسجل بايتات وسقف4GiB المأذون. السقف ليس تفويضًا دائمًا لتجارب جديدة. احتفظ بسجل التنزيل ولا تصفّره.
- runtime_assets.py يكتشف main عند الإعداد الأول. لإعادة هذه التجربة بدقة استخدم assets المحفوظة وassets.json عند revision أعلاه، لا أحدث main.
- runtime_convert.py يعيد البناء من GGUF المحلي. المحوّل مربوط ببصمة reconstruction.json الأصلية؛ تقرير تحويل جديد قد يختلف في الوقت أو المسار ولو تطابقت الأوزان. عند النقل انسخ الأصول والتقرير الأصلي كما هما. عند إعادة البناء قارن **كل** بصمات shards وملفات runtime بالتقرير الأصلي أولًا، ثم احتفظ بالتقرير الأصلي المطابق؛ لا تتجاوز التحقق أو تعدّل البصمات لقبول أوزان مختلفة.

النسخة الأصلية محفوظة في D:/RiwaqBuild/backups/before-training-v1-2026-11-14. الرجوع عن هذه التجربة لا يحتاج استبدال قاعدة بيانات المستخدم؛ المحوّل لم يُدمج. راجع دليل النسخ في README لاستعادة مصدر/بيانات قديمة في مجلد منفصل عند الضرورة.
