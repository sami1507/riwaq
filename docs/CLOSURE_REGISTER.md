# سجل إغلاق متطلبات رواق

هذا سجل تتبع للأسباب المشتركة ومعاييرها الأصلية؛ وجود صف أو ملف لا يعني إغلاقه. نسخة JSON تحفظ النصوص الأصلية من السجلات المتاحة، الحالات المفردة والأدلة وحدودها.

نسخة السجل: 3؛ مقام الفجوات الأصلي: 36؛ الإضافات: 4؛ المغلق بدليل: 0.

[السجل الكامل بصيغته المنظمة](CLOSURE_REGISTER.json) · [حالة المشروع](../PROJECT_STATE.md)

## حدود التغطية

- Missing verbatim TECH-001..100 and earlier prompt files prevent claiming complete original-text coverage.
- A historical passed case remains bounded to its evidence code/environment; fresh full regression is not fresh semantic/live/native validation.
- The 100/60/40 known cases are not automatically 200 failures; current records preserve their exact passed/partial/blocked results.
- Eight AI components and 125 catalog entries are existing architecture/counts, not 225 independent skills.
- Native post-pairing journey remains deferred by explicit user choice; Office must be probed in the current environment, not dismissed based on old COM failure.
- Training environment exists; actual historical QLoRA failed quality adoption. No new training, downloads, production deployment or paid resource was launched by this ledger.

## خط الأساس والفحوص الحالية

| الدليل | ناجح | فاشل | متخطى | النطاق |
|---|---:|---:|---:|---|
| [reports/closure-20261116/baseline-tests.xml](../reports/closure-20261116/baseline-tests.xml) | 2808 | 0 | 37 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/optin-documents-research.xml](../reports/closure-20261116/optin-documents-research.xml) | 44 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/optin/tests.xml](../reports/closure-20261116/optin/tests.xml) | 14 | 3 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/optin-current-config/tests.xml](../reports/closure-20261116/optin-current-config/tests.xml) | 3 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/symlink-current-elevated.xml](../reports/closure-20261116/symlink-current-elevated.xml) | 0 | 0 | 1 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/personal-memory/final.xml](../reports/closure-20261116/personal-memory/final.xml) | 52 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/data-revision/coverage-final.xml](../reports/closure-20261116/data-revision/coverage-final.xml) | 19 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/data-revision/security-final.xml](../reports/closure-20261116/data-revision/security-final.xml) | 6 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/semantic/review-final.xml](../reports/closure-20261116/semantic/review-final.xml) | 249 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/semantic/claim-review-final.xml](../reports/closure-20261116/semantic/claim-review-final.xml) | 33 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/catalog/dependency-final-elevated.xml](../reports/closure-20261116/catalog/dependency-final-elevated.xml) | 73 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/audio-decode/affected-final.xml](../reports/closure-20261116/audio-decode/affected-final.xml) | 195 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/audio-decode/review-final.xml](../reports/closure-20261116/audio-decode/review-final.xml) | 49 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/visual-authority/final-fixed-fixture.xml](../reports/closure-20261116/visual-authority/final-fixed-fixture.xml) | 49 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |
| [reports/closure-20261116/voice-authority/security-final.xml](../reports/closure-20261116/voice-authority/security-final.xml) | 5 | 0 | 0 | فحوص منفذة؛ راجع نوع كل حالة |

تشترك هذه الجولات في بعض الاختبارات؛ لا تُجمع أعدادها كحالات مستقلة. تبقى الجولات الفاشلة السابقة محفوظة ويُذكر سبب إعادة الفحص في دليل الشريحة.

## مجموعات القبول المعروفة

| المجموعة | المقام الثابت | النتيجة التاريخية المحفوظة |
|---|---:|---|
| AI | 100 | [{'passed': 19, 'failed': 0, 'partial': 78, 'blocked': 3, 'skipped': 0, 'not_applicable': 0}](../reports/ai-core-continuation-20261116/ci-final/AI_CORE_EVAL_RESULTS.json) |
| MATH | 60 | [{'passed': 60, 'failed': 0, 'partial': 0, 'blocked': 0, 'skipped': 0, 'not_applicable': 0}](../reports/math-reasoning-20261116/evaluation/final-v2/MATH_REASONING_RESULTS.json) |
| LEARN | 40 | [{'passed': 22, 'failed': 0, 'partial': 16, 'blocked': 2, 'skipped': 0, 'not_applicable': 0}](../reports/learning-20261116/evaluation/final-v4/LEARNING_RESULTS.json) |

هذه النتائج لا تُنسب تلقائيًا إلى إصدار جديد؛ الاختبارات المعروفة لا تثبت التعميم، ونجاح الأداة لا يثبت الرحلة الأصلية أو جودة كل لغة.

## حالة الفجوات

| الحالة | العدد |
|---|---:|
| غير منفذ | 2 |
| جزئي | 32 |
| قيد الإصلاح | 4 |
| منفذ وغير متحقق | 0 |
| فشل التحقق | 0 |
| متعذر بسبب متطلب خارجي | 2 |
| مكتمل ومختبر | 0 |

## الأسباب المشتركة وخطوة الإغلاق

| المعرف | الفجوة | الأولوية / الحالة | الخطوة ومعيار الإغلاق | المراجع الأصلية |
|---|---|---|---|---|
| GAP-001 | استكمال ربط مصادر المتطلبات الأصلية | P1 / متعذر بسبب متطلب خارجي | مطابقة النصوص الأصلية عند توفرها، وإضافة متطلبات غير ممثلة بإضافة مسجلة إلى المقام.<br>**القبول:** كل متطلب أصلي له معرف ومرجع ومعيار دون اختراع تفاصيل TECH-001..100 أو إسقاطها. | تغطية المصدر/التشغيل العام |
| GAP-002 | قبول أمني مستقل وبوابة البيانات الشخصية | P0 / جزئي | إكمال الضوابط المشتركة، تقييم انطباق ASVS، ثم مراجعة مستقلة دون فتح البوابة قبل قبول موثق.<br>**القبول:** اختبارات سلبية وإيجابية ومراجعة مستقلة لكل مسار حرج، مع عدم رفع بوابة المرحلة8 تلقائيًا. | G-01, G-07, G-09, G-10, DATA-02, SKILL-010, SKILL-038, LEARN-012 |
| GAP-003 | تعميم عزل العمال ومنع الخروج على مستوى النظام | P0 / جزئي | دمج pin العامل في إعداد الحزمة وفحص المصدر النهائي، ثم استكمال حدود المحركات الأصلية المتبقية دون اعتبار عملية محدودة عزل نظام تشغيل.<br>**القبول:** لا قراءة خارج النطاق أو شبكة من المدخل غير الموثوق؛ إلغاء وتنظيف وإيصال مثبت لكل عامل معلن. | G-03, G-05, DOC-13, DOC-15, VID-12, SKILL-027, SKILL-029, AI-19-T04, AI-19-T05 |
| GAP-004 | أذونات الهوية المحلية والمزامنة والحسابات الخارجية | P0 / جزئي | استكمال اختبارات محلية للحساب/السحب، وتجهيز التكامل الخارجي دون اختلاق حساب أو موافقة.<br>**القبول:** لا كشف وجود مورد أو تسليم بعد الإبطال؛ يوسم الاختبار المحلي منفصلًا عن الحسابات السحابية. | G-02, G-04, SEC-01, SKILL-024, SKILL-040, AI-19-T02, AI-19-T03, LEARN-025 |
| GAP-005 | الحذف والمشتقات والنسخ الاحتياطية والتعافي الشامل | P0 / جزئي | تحديد سلالة كل مشتق وسياسة تسوية الحذف/الصلاحية، ثم استعادة معزولة بمراجع وملفات ومفاتيح مع RTO/RPO معلنين.<br>**القبول:** نسخة سليمة وتالفة وملف مفقود ومفتاح غائب؛ لا إحياء صلاحية/حذف، وقياس فعلي منفصل عن الهدف. | G-08, SKILL-012, SKILL-039, SKILL-123, AI-03-T04, AI-10-T05, LEARN-027 |
| GAP-006 | تجديد جميع المخرجات التابعة بعد تصحيح المصدر | P1 / جزئي | اكتمل تجديد التحليل والجدول والرسم والتقرير المرتبط بوصفة محفوظة، مع مجموع30→40 ومتوسط40/3 وحفظ النسخ. يبقى ربط تصحيح التفريغ الصوتي AI-18-T05 والمسارات القديمة دون وصفة خارج هذه الشريحة؛ التحقق المكتبي في GAP-013.<br>**القبول:** تعديل O3 من 20 إلى 30 يعطي المجموع 40 والمتوسط 40/3 في جميع المخرجات، ويمنع تسليم نسخة قديمة. | AI-11-T05, AI-13-T01, AI-13-T05, AI-18-T05, LEARN-006, LEARN-010 |
| GAP-007 | الفهم الدلالي للطلب المركب والقيود والمتابعة | P1 / جزئي | تقييم صياغات جديدة للنفي والضمائر والمخرجات واللغة؛ إصلاح الأخطاء المقاسة مع إعادة مستقلة.<br>**القبول:** لا إسقاط مخرج أو تحويل سؤال تقدم إلى إلغاء؛ استيضاح المؤثر فقط واحترام القيود المتناقضة. | SKILL-001, SKILL-002, SKILL-003, SKILL-103, AI-01-T01, AI-01-T02, AI-01-T03, AI-01-T04, AI-01-T05, AI-02-T01, AI-02-T02, AI-02-T03, AI-02-T04, AI-02-T05 |
| GAP-008 | الاستلزام الدلالي والمعلومات غير المدعومة والاستدلال العام | P1 / جزئي | تشغيل عينات مقيدة الأدلة والجذور المنطقية، فصل خطأ الاسترجاع عن توليد الاستنتاج ثم إصلاح السبب.<br>**القبول:** لا استنتاج كلي من عينة أو خلط شرط لازم وكاف؛ الادعاء المخالف يصحح أو يسحب ولا ينجح بصحة الرابط. | CHAT-04, REASON-01, SKILL-007, SKILL-008, SKILL-051, AI-06-T05, AI-09-T01, AI-09-T02, AI-09-T03, AI-09-T04, AI-09-T05 |
| GAP-009 | حداثة البحث واستقلال المصادر وحسم اختلاف الإصدار | P1 / جزئي | تقييم مصادر أصلية حديثة/منسوخة/مختلفة الإصدار مع حفظ وقت الحدث منفصلًا عن النشر والفحص.<br>**القبول:** يفتح الدليل الداعم، لا يعد النسخ تأكيدات مستقلة، ولا يستخدم APIإصدارآخر بلا بيان. | RES-03, RES-06, RES-09, RES-14, SKILL-032, SKILL-033, AI-08-T01, AI-08-T02, AI-08-T03, AI-08-T04, AI-08-T05 |
| GAP-010 | بحث الويب العام والصفحات الديناميكية | P1 / جزئي | إكمال المسار المستقل للقراءة الآمنة؛ تهيئة مزود مسموح عند توفره وإثبات السؤال إلى المصدر إلى الجواب.<br>**القبول:** نتائج البحث لا تكفي دون فتح المصدر؛ redirectsوDNSوالصلاحيات مفحوصة، ولا تنفيذ مصدر غير موثوق. | RES-04, KNOW-01, SKILL-006, SKILL-073, SKILL-074, SKILL-075 |
| GAP-011 | استرجاع المعرفة متعدد اللغات والسياق الانتقائي | P1 / جزئي | تقييم الاسترجاع والاقتطاع وإعادة الترتيب منفصلة، مع الإذن والحذف وقراءة كامل النطاق للحساب.<br>**القبول:** عينة مستقلة حسب اللغة والمصدر؛ لا كشف مشروع آخر أو إجمالي من أعلى المقاطع فقط. | RES-10, RES-11, KNOW-08, KNOW-13, SKILL-009, SKILL-034, SKILL-037, AI-10-T01, AI-10-T02, AI-10-T03, AI-10-T04, AI-10-T05 |
| GAP-012 | OCR وتخطيط العربية والجداول والصور الملتبسة | P1 / جزئي | إعداد موارد مرجعية مستقلة للصفحات والخلايا وترتيب القراءة والرموز غير المقروءة وتصحيح المستخدم.<br>**القبول:** يحفظ 10 و0 و20 وترتيب الأعمدة والحاشية، ويعلّم غموض 10.50 بدل تحويله إلى 1050. | DOC-03, DOC-04, DOC-05, DOC-16, SKILL-013, SKILL-014, SKILL-060, AI-04-T01, AI-04-T02, AI-04-T03, AI-04-T04, AI-04-T05, AI-18-T03, AI-18-T04 |
| GAP-013 | التحقق البصري الأصلي لملفات Office وإعادة الحساب | P1 / جزئي | إرفاق نتيجة العرض والتخطيط النهائي للملفات الجديدة وبصماتها؛ إبقاء عطل Microsoft Office محددًا دون إسقاط نتيجة المحرك البديل.<br>**القبول:** جدول Word قابل للتحرير باتجاه سليم؛ B1 في Excel تساوي 20 ثم 30؛ تطابق الرسم والجدول والملف، وفحص القص والمعرف 001. | SKILL-015, SKILL-016, SKILL-017, SKILL-056, SKILL-058, SKILL-069, AI-17-T02, AI-17-T03 |
| GAP-014 | الصيغ والتحويلات وPDF وميزات الملفات غير المكتملة | P2 / جزئي | ربط كل عملية مطلوبة بعقدها، وتنفيذ النقص المستقل وتقييم اللغة والبنية دون ادعاء دعم كل الصيغ.<br>**القبول:** لا فقد صامت للمحتوى أو إعادة تفسير قيمة. تبقى العمليات غير المتحققة معلنة، ولا تُشغّل الماكرو أو الروابط الخارجية. | SKILL-052, SKILL-053, SKILL-054, SKILL-055, SKILL-057, SKILL-059, SKILL-063, SKILL-064, SKILL-068 |
| GAP-015 | الحساب والتحليل الإحصائي والتوقع وسرد النتائج | P1 / جزئي | فصل الوصف والاستدلال والتوقع، وفحص العينة والتسرب والحساسية وتطابق سرد النتائج مع الأرقام.<br>**القبول:** لا متوسط للمتوسطات دون أوزان، ولا سببية من ارتباط، ولا توقع دقيق دون عينة كافية؛ الظروف والتفاوت معلنان. | SKILL-070, SKILL-071, SKILL-072, AI-12-T01, AI-12-T02, AI-12-T03, AI-12-T04, AI-12-T05, AI-13-T02, AI-13-T03, AI-13-T04 |
| GAP-016 | الصوت العربي وتقييم النطق والترجمة البشرية | P1 / جزئي | تقييم الصمت والضجيج والأسماء والنفي محليًا، واختيار صوت عربي مأذون إن توفر دون تنزيل ضمني.<br>**القبول:** لا كلام مختلق في الصمت، ويحفظ الغموض؛ نطق عربي مسموع صحيح للأرقام والأسماء مع مرجع ومراجعة بشرية معلنة. | MEDIA-02, MEDIA-03, VOI-01, VOI-02, VOI-03, VOI-06, VOI-07, VOI-16, SKILL-023, SKILL-044, AI-18-T01, AI-18-T02 |
| GAP-017 | الصوت المباشر والمتحدثون والتخصيص الصوتي | P2 / غير منفذ | تعريف مسار صغير قابل للتحقق ودعم المحرك. لا تدريب صوتي دون فجوة وبيانات وحقوق وميزانية.<br>**القبول:** أجزاء مرتبطة بالدور وقابلة للإلغاء، وتسميات محايدة للتداخل؛ تخصيص بإصدار مقارن عند التفويض. | VOI-12, VOI-13, VOI-14 |
| GAP-018 | توليد الفيديو والتحرير التوليدي واتساق المشاهد | P2 / غير منفذ | إكمال المحرر المستقل وتجهيز تكامل صغير بمحرك مرخص عند توفر الموارد والتفويض للتنزيل والكلفة.<br>**القبول:** توليد فعلي من وصف أو صورة مع إيصال وتشغيل الفيديو؛ فصل جودة التوليد عن التركيب والتصحيح. | VID-02, VID-07, VID-14, VID-15 |
| GAP-019 | الصور والتحرير الموضعي والأصول والتصدير المتخصص | P2 / جزئي | حصر قدرات المحرك وقياس المشاهد، وإضافة وظائف مستقلة قابلة للتحقق دون توسيع وصف النموذج أو تدريب غير مأذون.<br>**القبول:** قياس الصورة الأولى والتصميم والتصحيح منفصلة؛ نص حرفي بلا قص، ومناطق محفوظة، ورموز قابلة للفك عند تنفيذها. | VIS-02, VIS-06, VIS-09, VIS-10, VIS-11, VIS-12 |
| GAP-020 | بيانات التدريب والتحسن المقاس مقابل تغيير الأوزان | P1 / جزئي | الاحتفاظ بالنموذج الأساسي؛ فحص موافقة البيانات وسلالتها وحذفها والتسرب، وتجهيز قياس جديد فقط عند إثبات فجوة تحتاج تغيير الأوزان.<br>**القبول:** حقوق ومصدر ومراجعة موثوقة من الخادم؛ تقسيم عائلات مستقل ومقارنة وتراجع. لا اعتماد من loss أو verified في ملف مرفوع. | TRAIN-02, TRAIN-03, TRAIN-04, TRAIN-05, LEARN-031, LEARN-032, LEARN-033, LEARN-034 |
| GAP-021 | ربط التعلم الشخصي المصرح مع إبقاء بوابة الأمن | P1 / جزئي | المسار الدائم والموافقة والحذف والنسخ وإبطال السياق مرتبط ومختبر محليًا. تبقى كتابة الذاكرة الشخصية الفعلية محجوبة423 حتى القبول الأمني المستقل؛ نجاح حالات محاكاة قبول مستقبلي لا يفتح البوابة.<br>**القبول:** تعلم مصرح بنطاقه، والتعليمات الحالية تتقدم على الافتراضية؛ حذف وتراجع وعزل. تبقى استجابة 423 حتى قبول مستقل. | DATA-02, LEARN-003, LEARN-004, LEARN-005, LEARN-007, LEARN-011, LEARN-012 |
| GAP-022 | تشخيص ردود الفعل ونزاع المراجعين ومراقبة الجودة | P1 / جزئي | استكمال تصنيف الحالات والنزاع ومؤشرات الجودة بعد الاعتماد؛ لا ترقية اختبار مرتبط إلى قبول كامل للحالة.<br>**القبول:** لا يُعامل الرأي السلبي كحقيقة، وسحب المصدر يبطل الاعتماد؛ خطأ موضوعي لا يخفيه الأسلوب، وفشل حرج يمنع الاعتماد. | LEARN-008, LEARN-009, LEARN-016, LEARN-017, LEARN-018, LEARN-019, LEARN-020, LEARN-022, LEARN-023, LEARN-030, LEARN-036, LEARN-040 |
| GAP-023 | أثر الأدوات الخارجية والبريد والتقاويم الحية | P1 / جزئي | إكمال المستقلات المحلية وتجهيز عقد الأثر والاستعلام بعد حالة unknown، ثم حساب اختبار مأذون دون رسائل لأشخاص حقيقيين.<br>**القبول:** أثر واحد للتكرار المتزامن، ورفض حمولة مختلفة، وعدم تكرار unknown. الإلغاء لا يدعي عكس أثر، وتُفحص الحالة النهائية. | SKILL-079, SKILL-088, SKILL-093, SKILL-095, SKILL-096, SKILL-097, SKILL-116, AI-15-T02, AI-15-T03, AI-15-T04, AI-16-T02 |
| GAP-024 | التخطيط الحر والتذكيرات عند إغلاق التطبيق | P2 / جزئي | تحسين المسار المحلي من متطلبات واقعية، واختبار توقف الخدمة والاستئناف والفوائت دون إرسال خارجي.<br>**القبول:** لا افتراض أن تقويمًا مجهولًا فارغ؛ التوقيت الصيفي والنواقص صريحة، ولا مضاعفة للحدوث، ويكتمل العمل المستقل. | SKILL-076, SKILL-077, SKILL-078, SKILL-080, SKILL-081, SKILL-082, SKILL-083, SKILL-084, SKILL-085, SKILL-086, SKILL-087, SKILL-098, SKILL-099, SKILL-100 |
| GAP-025 | التلخيص والترجمة وصياغة البريد وتقييم المعنى | P1 / جزئي | عينة مستقلة بمصادر مرخصة ونفي وأرقام وأسماء ومخرجات متعددة؛ إصلاح الفشل المقاس بدل زيادة التعليمات العامة.<br>**القبول:** تغطية المحتوى والقيود ولغة المخرج دون اختلاق أو فقد المعنى؛ فصل حفظ النص الميكانيكي عن الجودة اللغوية. | DOC-09, SKILL-022, SKILL-031, SKILL-043, SKILL-089, SKILL-090, SKILL-091, SKILL-092, AI-17-T05 |
| GAP-026 | تنسيق المهام الطويلة والتوازي والمهل والاستئناف | P1 / جزئي | اختبار التوقف الحقيقي بين الحفظ والإقرار، والعاملين والإلغاء والميزانية، مع إعادة استخدام العقود القائمة.<br>**القبول:** لا ازدواج أو فقد عمل؛ يوقف الممكن ويحفظ الجزئي ونقطة المتابعة. إشعار الواجهة وحده لا يثبت الاكتمال. | SKILL-047, SKILL-048, SKILL-112, SKILL-113, SKILL-114, SKILL-117, AI-16-T01, AI-16-T02, AI-16-T03, AI-16-T04, AI-16-T05, AI-20-T03, AI-20-T05 |
| GAP-027 | بدائل النماذج والحدود وميزانية السياق المقاسة | P1 / جزئي | اختبار تعطل فعلي لمزود محلي وبديل متوافق بلا انتقال سحابي، وقياس السياق والزمن والكلفة على عينة ممثلة.<br>**القبول:** يحفظ قيد المعالجة المحلية والمهلة والإلغاء والقيود القديمة؛ متوسطات الكلفة لها مقام معلن. | SKILL-049, SKILL-050, SKILL-107, SKILL-108, SKILL-110, SKILL-111, SKILL-115, AI-20-T01, AI-20-T02, AI-20-T04 |
| GAP-028 | مصدر واحد للمهارات والثقة والنسخ والاعتماديات | P1 / جزئي | أُصلحت جاهزية الاعتماد اللازم وبصمة نسخه وأدلته. يستمر فحص حدود الاعتماد الاختياري والحزم الخارجية والتعارضات مع بقاء النطاق المعلن دون توسعة ثقة أو تثبيت تلقائي.<br>**القبول:** الواجهة والموجه يستخدمان السجل نفسه؛ الاسم القديم لا ينكسر، والاعتماد المفقود لا يعطل المستقل أو ينتج جاهزية كاذبة. | SKILL-004, SKILL-025, SKILL-101, SKILL-102, SKILL-103, SKILL-105 |
| GAP-029 | حزمة Windows الأحدث والرحلة الأصلية المؤجلة | P1 / جزئي | مرشح Windows اجتاز8 حالات للعامل والخدمة والحفظ والاستئناف والمصادقة. يعاد بناء الحزمة من المصدر النهائي بعد التجميد؛ رحلة WebView الأصلية بعد الاقتران تبقى مؤجلة باختيار المستخدم.<br>**القبول:** مصدر محدد وبصمة حزمة ومخزن اختبار؛ تشغيل وفتح ملف واستعادة. لا ادعاء توقيع أو installer أو رحلة أصلية دون شاهد. | PLAT-02, COD-18, VID-17, VOI-15, SKILL-021, SKILL-062, C17-16 |
| GAP-030 | حزم macOS وLinux والتوقيع والتوزيع | P2 / متعذر بسبب متطلب خارجي | تجهيز بناء قابل للمراجعة لكل منصة عند توفر بيئتها والتفويض بمفاتيح التوقيع؛ بناء الويب لا يقوم مقام ذلك.<br>**القبول:** بناء وتشغيل فعلي لكل حزمة، وبيان إصدار وسجل رجوع ضمن التفويض. | PLAT-03 |
| GAP-031 | إتاحة الواجهة والقارئ البشري وتجربة اللغات | P2 / جزئي | اختبار الرحلات الحالية بالتكبير ولوحة المفاتيح واتجاهي النص وقارئ شاشة عند توفره دون إسقاط محتوى.<br>**القبول:** حالات تحميل وفشل واستئناف فعلية، وتركيز وأسماء عناصر وترتيب قراءة صحيح للنص والجداول. | UX-01, UX-02, UX-03, ID-02, C17-13 |
| GAP-032 | فهم المستودعات وتوليد إصلاح مثبت ضمن SDK الفعلي | P1 / جزئي | عينة مستقلة بأخطاء مزروعة وتعديلات مستخدم، واختبار الإصلاح بالإصدار الفعلي دون تشغيل hooks على المضيف.<br>**القبول:** إعادة إنتاج السبب واختبار فاشل ثم ناجح، وفرق يحفظ عمل المستخدم؛ يسجل SDK الغائب دون اختلاق نجاح البناء. | COD-06, COD-11, COD-15, COD-16, SKILL-018, SKILL-019, SKILL-020, SKILL-041, SKILL-042, AI-14-T01, AI-14-T02, AI-14-T03, AI-14-T04, AI-14-T05 |
| GAP-033 | CI وبوابة إصدار وأدلة مرتبطة بالشيفرة | P1 / جزئي | تشغيل محلي جديد وربط بصمته بالنتائج، وتجهيز CI خارجي عند توفر المستودع والمنصة دون نشر إنتاجي.<br>**القبول:** فشل التفويض أو ازدواج الأثر أو فقد العمل مانع مستقل؛ المتخطى ليس ناجحًا، ولا ينقل نجاح قديم إلى شيفرة جديدة. | SKILL-106, SKILL-119, SKILL-120, SKILL-125, C17-15 |
| GAP-034 | المراقبة والتدقيق ومدة الاحتفاظ والإعدادات | P2 / جزئي | اختبار توقف العامل وتراكم الطابور وخطأ التخزين وقدم بيانات المراقبة أو غيابها؛ فحص متغيرات البيئة دون قيم أسرار.<br>**القبول:** لا تبقى اللوحة خضراء بقيمة قديمة؛ المقاييس والوحدات والعتبات معلنة، والتنبيه مجهز دون رسائل غير مأذونة. | SKILL-118, SKILL-121, SKILL-122 |
| GAP-035 | المجموعات المستقلة والمقارنة العادلة والتعميم | P1 / جزئي | تجميع عينات جديدة مأذونة ومراجعة المعنى وضبط النموذج والأدوات والميزانية؛ توثيق تعذر مقارنة منافس غير متاح.<br>**القبول:** فصل الجودة والوقت والكلفة والتدخل؛ لا تسرب للمراجع أو انتقاء أفضل تشغيل أو ادعاء تفوق عام. | OPS-03, RES-18, SKILL-104, SKILL-124, AI-20-T04, LEARN-028, LEARN-029, LEARN-040 |
| GAP-036 | إعادة الفحوص المتخطاة المتاحة حاليًا | P1 / جزئي | اكتملت الفحوص الـ36 المتاحة. يبقى اختبار symlink الفعلي متعذرًا بصلاحية Windows الحالية؛ لا تغيير لحماية النظام أو Developer Mode. يعاد عند توفر بيئة مأذونة تدعم إنشاء الرابط.<br>**القبول:** لكل تخطٍ سبب حالي ونتيجة وحدود؛ لا استبدال اختبار مطلوب بمحاكاة، ولا اعتبار مجرد الرفض إنجازًا للمسار. | تغطية المصدر/التشغيل العام |
| GAP-037 | حجب محتوى التغذية الراجعة عند سحب مصدر الأداة | P0 / قيد الإصلاح | إعادة اختبار source withdrawal الحي والمتأثر بعد تجميد learning/knowledge/guard، ثم تحديث البصمة قبل إغلاق الفجوة.<br>**القبول:** سحب المصدر أو الأصل الأعلى أو المخرج، والدورة أو فساد البصمة، تحجب المحتوى برسالة عامة دون كشف المورد؛ لا تبقى نسخة مخبأة قابلة للتصدير. | LEARN-025, LEARN-027, AI-19-T03, SKILL-024, SKILL-037 |
| GAP-038 | تفويض موارد التصميم والمهام البصرية عند سحب الملكية | P0 / قيد الإصلاح | إعادة اختبارات التفويض والتنظيف على visual authority وdecode المصدر النهائي، وربط النتيجة ببصمات الملفات.<br>**القبول:** سحب الملكية أو الجلسة يمنع المحتوى والكتابة والتسليم، ويوقف العملية الجارية؛ لا تقبل هوية مرسلة من العميل أو صلاحية محفوظة قبل السحب. | AI-19-T02, AI-19-T03, VIS-08 |
| GAP-039 | تفويض جلسات الصوت والحفظ والإلغاء بعد سحب الملكية | P0 / قيد الإصلاح | إعادة فحوص السلطة والجلسات والحزم بعد تثبيت مصدر voice.py وvoice_audio.py؛ لا يكفي نجاح SAPI لإثبات تفويض الجلسة.<br>**القبول:** يمنع سحب الملكية القراءة والبدء والتحديث والحفظ؛ تتوقف الجلسة الخاملة والعامل الجاري ولا ينشر ردًا متأخرًا أو يعيد تشغيل الصوت. | AI-19-T02, AI-19-T03, VOI-05, VOI-09, VOI-10, VOI-11 |
| GAP-040 | تفويض المشروع والملف في المحادثة والمعرفة ومسارات التخزين العامة | P0 / قيد الإصلاح | إكمال اختبارات start/done وsnapshot والاستئناف والمشتقات، وإعادة المجموعة المتأثرة على بصمة نهائية؛ تأكيد أن fixtures لا تتجاوز guard.<br>**القبول:** تختفي الموارد المسحوبة من القوائم والبحث؛ لا قراءة أو تعديل أو تصدير أو استرجاع بعد السحب؛ لا مقطع خاص في start أو delta أو done، ولا استعادة لصلاحية قديمة بعد الاستئناف. | AI-10-T02, AI-10-T05, AI-19-T02, AI-19-T03, LEARN-025, LEARN-027, VOI-08 |

## مصادر المتطلبات المحفوظة

| المصدر | الحالة |
|---|---|
| [README.md](../README.md) | موجود؛ بصمته في JSON |
| [PROJECT_STATE.md](../PROJECT_STATE.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_REQUIREMENTS.md](../docs/RIWAQ_REQUIREMENTS.md) | موجود؛ بصمته في JSON |
| [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_ROADMAP.md](../docs/RIWAQ_ROADMAP.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_AUDIT.md](../docs/RIWAQ_AUDIT.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_COMPLETION_REPORT.md](../docs/RIWAQ_COMPLETION_REPORT.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_ENVIRONMENT_AND_PROGRESS_AUDIT.md](../docs/RIWAQ_ENVIRONMENT_AND_PROGRESS_AUDIT.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_SECURITY.md](../docs/RIWAQ_SECURITY.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_SECURITY_ACCEPTANCE.md](../docs/RIWAQ_SECURITY_ACCEPTANCE.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_GUARD.md](../docs/RIWAQ_GUARD.md) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_READINESS.md](../docs/AI_CORE_READINESS.md) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_BLOCKERS.md](../docs/AI_CORE_BLOCKERS.md) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_COMPONENTS.json](../docs/AI_CORE_COMPONENTS.json) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_REPORT.md](../docs/AI_CORE_REPORT.md) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_DATA_NOTES.md](../docs/AI_CORE_DATA_NOTES.md) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_EVIDENCE_NOTES.md](../docs/AI_CORE_EVIDENCE_NOTES.md) | موجود؛ بصمته في JSON |
| [docs/AI_CORE_REASONING_NOTES.md](../docs/AI_CORE_REASONING_NOTES.md) | موجود؛ بصمته في JSON |
| [docs/MATH_REASONING_STATE.md](../docs/MATH_REASONING_STATE.md) | موجود؛ بصمته في JSON |
| [docs/LEARNING_STATE.md](../docs/LEARNING_STATE.md) | موجود؛ بصمته في JSON |
| [docs/DESKTOP.md](../docs/DESKTOP.md) | موجود؛ بصمته في JSON |
| [docs/DATA_REPORT_REVISIONS.md](../docs/DATA_REPORT_REVISIONS.md) | موجود؛ بصمته في JSON |
| [reports/closure-20261116/personal-memory/RESULTS.md](../reports/closure-20261116/personal-memory/RESULTS.md) | موجود؛ بصمته في JSON |
| [reports/closure-20261116/semantic/NOTES.md](../reports/closure-20261116/semantic/NOTES.md) | موجود؛ بصمته في JSON |
| [reports/closure-20261116/catalog/NOTES.md](../reports/closure-20261116/catalog/NOTES.md) | موجود؛ بصمته في JSON |
| [docs/AUDIO_ISOLATION.md](../docs/AUDIO_ISOLATION.md) | موجود؛ بصمته في JSON |
| [docs/DOCUMENTS.md](../docs/DOCUMENTS.md) | موجود؛ بصمته في JSON |
| [docs/DOCUMENT_DEPENDENCIES.md](../docs/DOCUMENT_DEPENDENCIES.md) | موجود؛ بصمته في JSON |
| [docs/MEDIA.md](../docs/MEDIA.md) | موجود؛ بصمته في JSON |
| [docs/VIDEO.md](../docs/VIDEO.md) | موجود؛ بصمته في JSON |
| [docs/VIDEO_DEPENDENCIES.md](../docs/VIDEO_DEPENDENCIES.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_VOICE.md](../docs/RIWAQ_VOICE.md) | موجود؛ بصمته في JSON |
| [docs/VOICE_DEPENDENCIES.md](../docs/VOICE_DEPENDENCIES.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_RESEARCH_KNOWLEDGE.md](../docs/RIWAQ_RESEARCH_KNOWLEDGE.md) | موجود؛ بصمته في JSON |
| [docs/KNOWLEDGE.md](../docs/KNOWLEDGE.md) | موجود؛ بصمته في JSON |
| [docs/KNOWLEDGE_RESULTS.md](../docs/KNOWLEDGE_RESULTS.md) | موجود؛ بصمته في JSON |
| [docs/RIWAQ_CODING_ASSISTANT.md](../docs/RIWAQ_CODING_ASSISTANT.md) | موجود؛ بصمته في JSON |
| [docs/CODING_SUPPORT.md](../docs/CODING_SUPPORT.md) | موجود؛ بصمته في JSON |
| [docs/CODING_SANDBOX.md](../docs/CODING_SANDBOX.md) | موجود؛ بصمته في JSON |
| [docs/TRAINING_RESULTS.md](../docs/TRAINING_RESULTS.md) | موجود؛ بصمته في JSON |
| [training/README.md](../training/README.md) | موجود؛ بصمته في JSON |
| [docs/IDENTITY.md](../docs/IDENTITY.md) | موجود؛ بصمته في JSON |
| [docs/IDENTITY_RESULTS_2026-11-16.md](../docs/IDENTITY_RESULTS_2026-11-16.md) | موجود؛ بصمته في JSON |
| [docs/SYNC.md](../docs/SYNC.md) | موجود؛ بصمته في JSON |
| [docs/STUDIO.md](../docs/STUDIO.md) | موجود؛ بصمته في JSON |
| [docs/REASONING.md](../docs/REASONING.md) | موجود؛ بصمته في JSON |
| [docs/REASONING_POLICY.md](../docs/REASONING_POLICY.md) | موجود؛ بصمته في JSON |
| [docs/CALCULATOR.md](../docs/CALCULATOR.md) | موجود؛ بصمته في JSON |
| [config/skills-batch-01.json](../config/skills-batch-01.json) | موجود؛ بصمته في JSON |
| [reports/guard-asvs-matrix.json](../reports/guard-asvs-matrix.json) | موجود؛ بصمته في JSON |
| [reports/guard-validation.json](../reports/guard-validation.json) | موجود؛ بصمته في JSON |
| [reports/security-update-validation.json](../reports/security-update-validation.json) | موجود؛ بصمته في JSON |
| [reports/completion-desktop-environment.json](../reports/completion-desktop-environment.json) | موجود؛ بصمته في JSON |
| [reports/completion-native-acceptance.json](../reports/completion-native-acceptance.json) | موجود؛ بصمته في JSON |
| [reports/resume-native-delivery.json](../reports/resume-native-delivery.json) | موجود؛ بصمته في JSON |
| [reports/document-isolation-live-v3.json](../reports/document-isolation-live-v3.json) | موجود؛ بصمته في JSON |
| [reports/research-validation.json](../reports/research-validation.json) | موجود؛ بصمته في JSON |
| [reports/voice-validation.json](../reports/voice-validation.json) | موجود؛ بصمته في JSON |
| [reports/voice-live.json](../reports/voice-live.json) | موجود؛ بصمته في JSON |
| [reports/video-validation.json](../reports/video-validation.json) | موجود؛ بصمته في JSON |
| [reports/video-live.json](../reports/video-live.json) | موجود؛ بصمته في JSON |
| [reports/media-validation.json](../reports/media-validation.json) | موجود؛ بصمته في JSON |
| [reports/documents-validation.json](../reports/documents-validation.json) | موجود؛ بصمته في JSON |
| [reports/training-lora-selection.json](../reports/training-lora-selection.json) | موجود؛ بصمته في JSON |
| [reports/training-lora-heldout.json](../reports/training-lora-heldout.json) | موجود؛ بصمته في JSON |
| [reports/training-lora-run.json](../reports/training-lora-run.json) | موجود؛ بصمته في JSON |
| [reports/training-v3-final.json](../reports/training-v3-final.json) | موجود؛ بصمته في JSON |
| [evaluation/ai_core/AI_CORE_EVAL_CASES.json](../evaluation/ai_core/AI_CORE_EVAL_CASES.json) | موجود؛ بصمته في JSON |
| [evaluation/ai_core/user-criteria-reference.md](../evaluation/ai_core/user-criteria-reference.md) | موجود؛ بصمته في JSON |
| [evaluation/math/MATH_REASONING_EVALS.json](../evaluation/math/MATH_REASONING_EVALS.json) | موجود؛ بصمته في JSON |
| [evaluation/learning/LEARNING_EVAL_CASES.json](../evaluation/learning/LEARNING_EVAL_CASES.json) | موجود؛ بصمته في JSON |
| [reports/learning-20261116/requirements.txt](../reports/learning-20261116/requirements.txt) | موجود؛ بصمته في JSON |
| [reports/ai-core-continuation-20261116/ci-final/AI_CORE_EVAL_RESULTS.json](../reports/ai-core-continuation-20261116/ci-final/AI_CORE_EVAL_RESULTS.json) | موجود؛ بصمته في JSON |
| [reports/math-reasoning-20261116/evaluation/final-v2/MATH_REASONING_RESULTS.json](../reports/math-reasoning-20261116/evaluation/final-v2/MATH_REASONING_RESULTS.json) | موجود؛ بصمته في JSON |
| [reports/learning-20261116/evaluation/final-v4/LEARNING_RESULTS.json](../reports/learning-20261116/evaluation/final-v4/LEARNING_RESULTS.json) | موجود؛ بصمته في JSON |
| [reports/closure-20261116/baseline-manifest.json](../reports/closure-20261116/baseline-manifest.json) | موجود؛ بصمته في JSON |
| [docs/skills/SKILLS_BATCH_01_ACCEPTANCE.md](../docs/skills/SKILLS_BATCH_01_ACCEPTANCE.md) | موجود؛ بصمته في JSON |
| [docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md](../docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md) | موجود؛ بصمته في JSON |
| [docs/skills/SKILLS_BATCH_03_ACCEPTANCE.md](../docs/skills/SKILLS_BATCH_03_ACCEPTANCE.md) | موجود؛ بصمته في JSON |
| [docs/skills/SKILLS_BATCH_04_ACCEPTANCE.md](../docs/skills/SKILLS_BATCH_04_ACCEPTANCE.md) | موجود؛ بصمته في JSON |
| [docs/skills/SKILLS_BATCH_05_ACCEPTANCE.md](../docs/skills/SKILLS_BATCH_05_ACCEPTANCE.md) | موجود؛ بصمته في JSON |

## تحديث السجل

`.venv/Scripts/python.exe reports/closure-20261116/build_closure_register.py`

أي إغلاق يحتاج تحديثًا في `updates` يتضمن الدليل والبيئة وبصمة الشيفرة ونتيجة القبول، ثم تغيير الحالة. تحفظ إعادة التوليد التحديثات الموثقة، ولا تحول العائق الخارجي أو غياب الاختبار إلى نجاح.
