# قبول الدفعة الرابعة — المهارات 76–100

تاريخ الأدلة: 2026-11-16، Windows AMD64 المحلي، Python 3.12.10، Pydantic 2.14.3، SQLite، وtzdata 2026.6 / IANA 2026f. هذا قبول للقدرات المحددة بأدلتها، وليس شهادة إنتاج أو اعتمادًا للمراحل السابقة. ملفات التعليمات وحدها لا تثبت قدرة.

**الفحص الشامل: 1937 ناجحة و33 متجاوزة وصفر فشل؛ المتصفح: 20 حالة فعلية وحالة عقدية محاكية منفصلة، كلها ناجحة.** التعديلات اللاحقة للفحص الواسع أُعيد فحص نطاقها في مجموعتي 72 و32 حالة أدناه. الاختبار المحلي لا يثبت حساب بريد خارجي أو تطبيق Windows الأصلي. ما بعد الاقتران في Windows مؤجل بقرار المستخدم؛ مشاهدات الحزمة السابقة لا تثبت هذه الدفعة. النسخة المسلّمة مربوطة بـ[بصمات المصدر النهائية](../../reports/skills-batch-04-20261116/final-source-manifest.json).

## خط الأساس والمعايير

[لقطة البداية](../../reports/skills-batch-04-20261116/baseline-manifest.json): 519 ملفًا، 5,936,017 بايت، دون Git أو حذف تعديلات سابقة. [اختبارات البداية](../../reports/skills-batch-04-20261116/baseline-tests.xml): 88 ناجحة. لم يكن التقويم أو المجدول أو صندوق البريد منفذًا؛ هذا لا يثبت نجاحًا سابقًا للقدرات الجديدة ولا نسبة تحسن دلالي.

ثُبتت [معايير التخطيط](../../reports/skills-batch-04-20261116/planning/acceptance-baseline.json) قبل مقارنة النتائج: نافذة 09:00–12:00، اجتماع 10:00–11:00 بهامشين 15 دقيقة، فتتبقى 90 دقيقة لمهمتين كل منهما 45؛ تؤجل الثالثة 15، ولا تتسع مهمة متصلة 60. تقاطع المشاركين المرجعي 10:00–10:30 UTC. الصفر غير المجهول؛ النسخة والصلاحية وعدم تكرار الأثر شروط قبول.

العينات عربية وعبرية وإنجليزية في العناوين والنصوص والقوالب، مع معجم محدود للتواريخ والأسطر المعلّمة. لا قياس شامل للهجات أو فهم النص الحر، ولا نسبة ثقة أو هدف p95 اختير بعد النتائج.

## دليل الأدلة

| الرمز | الدليل والنتيجة | نوع الاختبار وحدوده |
|---|---|---|
| B | [baseline-tests.xml](../../reports/skills-batch-04-20261116/baseline-tests.xml): 88 ناجحة | مكونات سابقة؛ ليست اختبارًا للقدرات الجديدة. |
| P | [planning/tests-final.xml](../../reports/skills-batch-04-20261116/planning/tests-final.xml): 66 ناجحة؛ [التقرير](../../reports/skills-batch-04-20261116/planning/REPORT.md) | حساب فعلي بقواعد IANA المثبتة، بيانات اصطناعية؛ لا مزود أو تقويم خارجي. |
| S | [schedule/final-kernel-tests.xml](../../reports/skills-batch-04-20261116/schedule/final-kernel-tests.xml): 25 ناجحة؛ [التقرير](../../reports/skills-batch-04-20261116/schedule/IMPLEMENTATION.md) | SQLite فعلية، عمليتان متزامنتان، إعادة process، سحب/تعطل تحقق وساعات مضبوطة. |
| M | [productivity-final.xml](../../reports/skills-batch-04-20261116/productivity-final.xml): 72 ناجحة تشمل البريد/API و10 تكامل و9 مراجعة؛ [62 بعد الفلترة](../../reports/skills-batch-04-20261116/mail-after-filter-final.xml) محفوظة تاريخيًا | بريد ومسودات محلية فعلية. [SMTP المملوك](../../reports/skills-batch-04-20261116/mail/owned-smtp.json) استقبل رسالة على loopback، مع نقاط اتصال/DNS/TLS بديلة للاختبار. ليست TLS حية لدى مزود. |
| I | [entry-final.xml](../../reports/skills-batch-04-20261116/entry-final.xml): 32 ناجحة، منها 11 تكامل و21 مدخل منتج/توجيه؛ [9 السابقة](../../reports/skills-batch-04-20261116/integration-second.xml) تاريخية | FastAPI والتخزين وعامل التطبيق الحقيقيان؛ يتضمن الفحص الأخير أن المدعوين المحليين لا ينتجون نجاح دعوة خارجية. |
| R | 9 مراجعة في M؛ [planning/review-after.xml](../../reports/skills-batch-04-20261116/planning/review-after.xml) يحفظ أول 8 ناجحة | API مستقل: عزل وحذف وتبديل مصدر وإلغاء وفلترة مسودة غير متاحة. حقن توقف بعد commit في حالتي الإلغاء فقط؛ الأثر والإلغاء والقراءة فعلية. |
| T | [routing-integration.xml](../../reports/skills-batch-04-20261116/routing-integration.xml): 53 ناجحة | توجيه وعقود وتكامل، يتداخل مع مجموعات أخرى. |
| A | 21 حالة في I؛ [product-paths.xml](../../reports/skills-batch-04-20261116/product-paths.xml) دليل الجولة السابقة | 8 عمليات تخطيط عبر API، 12 عينة توجيه ar/he/en، وثبات أول 75 حزمة مع سجل 100. |
| F | [regression-full.xml](../../reports/skills-batch-04-20261116/regression-full.xml): 1937 ناجحة، 33 متجاوزة، صفر فشل، 186.95 ثانية إجمالي pytest | سبق تعديلات المفاتيح والفلترة وبعض تفاصيل الواجهة/حالة الدعوة؛ نطاق التغيير اللاحق أعيد في M وI وK وU. ليس تشغيلًا كاملًا مكررًا على آخر تعديل. |
| K | [credentials.xml](../../reports/skills-batch-04-20261116/credentials.xml): 6 ناجحة | تحقق عقد أداة إعداد المفتاح ومخزن النظام؛ لا إدخال سر حقيقي. |
| D | [prior-data.xml](../../reports/skills-batch-04-20261116/prior-data.xml): حالة ناجحة مستقلة | أعيد مسار CSV/تنظيف/إحصاء/join/chart عبر API والعامل Docker المثبت من الدفعة السابقة؛ ليس إعادة كل اختبارات opt-in. |
| U | [report.json](../../reports/skills-batch-04-20261116/ui/2026-11-16T10-24-28.069Z/report.json): 20 حالة فعلية ناجحة + حالة عقدية محاكية ناجحة | Chrome 157.0.8103.52 على Windows، خادم المصدر المحلي وبيانات اصطناعية؛ 0 page errors و0 طلب متصفح خارجي. حالة unknown-send وحدها استبدلت إيصال الخادم؛ لا SMTP خارجي أو Windows أصلي. |
| V | [frontend-validation.json](../../reports/skills-batch-04-20261116/frontend-validation.json)، [سجل TypeScript](../../reports/skills-batch-04-20261116/ui-typescript-final.log)، [دليل الجولة](../../reports/skills-batch-04-20261116/ui/README.md) | TypeScript exit 0؛ بناء Vite المثبت 7.3.8 نجح في 2.63 ثانية، مع تحذير حجم حزمة أكبر من 500 kB. النطاق المعلن في package.json هو ^7.2.0؛ نجاح البناء منفصل عن U. |

المجموعات متداخلة؛ لا تجمع أعدادها كحصيلة اختبارات فريدة. قبول SMTP المملوك لا يثبت التسليم أو القراءة. الاتصال الخارجي الحي غير متحقق.

تفصيل الـ33 المتجاوزة في F: 17 اختبار مستندات opt-in، و4 نموذج محلي/Docker، و2 عامل مركزي، و2 شبكة عامة، واختبار واحد لكل من coding Docker وBatch03 data الحي ورندر مستند وOCR لنموذج AcroForm ومحلل ويب فعلي وPOST مملوك وGET رسمي وsymlink بصلاحية Windows. غياب متغير opt-in ليس نجاحًا ولا عطلًا مثبتًا؛ أعيد اختبار بيانات واحد في D بصورة مستقلة. عدم قدرة حساب Windows على إنشاء symlink يبقى قيدًا؛ لا تعطل الحماية لمعالجته.

## مواضع التشغيل

الواجهة [SkillProductivity.tsx](../../frontend/src/SkillProductivity.tsx) داخل [SkillsWorkbench.tsx](../../frontend/src/SkillsWorkbench.tsx)، والعميل [productivity-api.ts](../../frontend/src/productivity-api.ts). نقطة المهمة POST /api/projects/{projectId}/skill-runs، ثم GET /{runId} أو POST /{runId}/cancel. الربط [skill_batch04.py](../../backend/skill_batch04.py)، والعقد [skill_core.py](../../backend/skill_core.py)، والتنفيذ [skills.py](../../backend/skills.py)، و[السجل الحالي](../../config/skills-batch-01.json) الموسع إلى 100؛ لا سجل موازٍ. تعريفات المهارات داخل skills/<slug>/SKILL.md ولا تمنح صلاحيات.

اختصارات ملفات/أدوات الجدول:
- **P**: productivity-planning؛ [productivity_planning.py](../../backend/productivity_planning.py) و[productivity_time.py](../../backend/productivity_time.py).
- **S**: productivity-schedule؛ [productivity_schedule.py](../../backend/productivity_schedule.py).
- **M**: productivity-mail؛ [productivity_mail.py](../../backend/productivity_mail.py) و[productivity_mail_transport.py](../../backend/productivity_mail_transport.py).
- **N**: productivity-meetings؛ [productivity_meetings.py](../../backend/productivity_meetings.py).
- جميعها تستخدم Guard والجلسة والمهام القائمة؛ القراءة الإضافية في [productivity_api.py](../../backend/productivity_api.py).

## المصفوفة الحالية — 25 صفًا

«عامل ضمن نطاق محدد» يعني الحساب/المسار المذكور الذي اختُبر، لا اكتمال كل التفسيرات الممكنة لاسم المهارة. منصة P/S/M/I/R/A هي Windows Python؛ U مستقل. اللغات المذكورة عينات وليست سقف قبول إدخال أو ادعاء شمول.

واجهة الجولة النهائية تحتوي هوامش الانشغال الصريحة، ونقل/راحة الوردية مع الفصل بين الفراغ المجهول والصفر، واختيار تبعيات بنود agenda. اختبر U إرسال هذه القيم وحفظ التبعية، إضافة إلى الاستمرارية بعد إعادة فتح المتصفح؛ لا يثبت ذلك كامل عقد API على كل جهاز أو لغة.

| # / الاسم التقني | الاسم والحالة | المدخل/موضع التنفيذ → المخرج | الاعتماد والنطاق | معيار القبول والدليل | الحد أو المتبقي |
|---|---|---|---|---|---|---|
| 76 daily-planning | تخطيط اليوم — عامل ومتحقق ضمن قيود صريحة | planningOperation=daily، P: يوم/فترات/مهام → مجدول ومؤجل وأسباب؛ S plan.save/get يحفظ الاقتراح | منطقة ومدد مؤكدة، IANA؛ لا حجز تلقائي | 90 دقيقة، 45+45، عجز 15، المتصل 60 لا يتسع؛ P/I/A/U، سحب الخطة R | لا تحسين شامل أو استنتاج جميع القيود. |
| 77 weekly-planning | تخطيط الأسبوع — عامل ضمن فترات معلنة | operation=weekly، P: بداية وفترات عمل → خطة واعتماديات ومؤجل | لا افتراض أسبوع عمل؛ حد 32 يومًا | بداية/تبعية/مهلة، صفر مقابل مجهول؛ P/A | لا قراءة تقويم خارجي أو إثبات أمثلية. |
| 78 shift-schedule-planning | التخطيط حسب الورديات — جزئي؛ حساب محلي | P shift: تاريخ وساعتان وعبور اليوم ونقل/راحة → فترات وفراغ وتحذيرات | endNextDay صريح؛ المجهول لا يصبح صفرًا مؤكدًا | 18→01 = 420 دقيقة، DST ونقص النقل/الراحة؛ P/A | لا قراءة صورة ورديات أو تخمين سياسة راحة. |
| 79 calendar-event-creation | أحداث التقويم — جزئي؛ تقويم محلي دائم | scheduleOperation، S: calendar/event CRUD → معرف ونسخة وحدث مقروء | SQLite والمالك/المشروع/التقويم؛ CAS ومفتاح عملية | إنشاء وقراءة وعدم تكرار، all-day وعزل؛ S/I | لا Google/Outlook أو دعوات؛ بيانات المدعوين محلية. |
| 80 calendar-conflict-check | كشف التعارض — عامل ضمن فترات مصرح بها | P conflicts أو S event.freebusy → تداخل فعلي أو buffer وفترات مشغولة | فترات نصف مفتوحة؛ ACL قبل القراءة | تماس الأطراف لا يتعارض، فحص ذري، freebusy بلا عناوين؛ P/S/A | التقويم المجهول ليس فارغًا؛ لا مزود حي. |
| 81 timezone-scheduling | المناطق الزمنية — عامل ضمن IANA والمدخل الصريح | P timezone: times/displayZones → UTC ومحلي أو gap/fold ambiguity | TimeInput، tzdata 2026.6 / IANA 2026f | gap يرفض، fold ساعة، recurrence 23/25 ساعة، date غير instant؛ P/A | المعجم النسبي محدود؛ تحديث القواعد يحتاج إعادة تحقق. |
| 82 reminder-creation | التذكيرات — جزئي؛ نشر داخلي حقيقي | S reminder CRUD/pause/resume → حدوث وحالة؛ notification.list/read | grant محدود وعامل lifespan | tick مستحق، restart، منع التكرار والسحب؛ S/I | الخدمة يجب أن تعمل؛ لا OS/push/wake أو إثبات المشاهدة. |
| 83 recurring-task-setup | التكرار — جزئي؛ تذكيرات محدودة | S reminder مع recurrence وفائت/نهاية → حدوثات وقرارات الفائت | أقصى 366 حدوثًا/يومًا؛ افتراضي 30 إن غابت النهاية ويُعلن | DST، عاملان، late job، latest/skip/catch-up؛ S | ليس تنفيذ نموذج أو بريد دوري أو daemon عند إغلاق الخدمة. |
| 84 task-inbox-triage | فرز المهام — جزئي؛ أسطر صريحة | meetingOperation=triage، N → سجلات ومهام اختيارية وissues | labels ar/he/en ومعرف ثابت وحفظ صريح | review لا يخلق مهمة، أمر المصدر لا ينفذ؛ I/R | لا فرز دلالي عام أو صندوق خارجي. |
| 85 time-blocking | فترات التركيز — عامل كاقتراح وحجز محلي صريح | P time-blocking → اقتراح؛ S calendar.book بنسخة وكتل → أحداث ذرية | تفويض قائم وإعادة فحص التعارض | فشل الكتلة الثانية يرجع الكل؛ لا قطع مهمة متصلة؛ P/S/A | الاقتراح لا يحجز وحده، ولا حجز خارجي. |
| 86 meeting-time-search | وقت الاجتماع — عامل لتقاطع المعلن | P meeting-search: participants/known/windows/busy → تقاطع وعرض محلي | لا افتراض unknown متاحًا | A9–11/B10–12/C9:30–10:30 → 10–10:30؛ busy المنظم؛ P/A | لا دعوة/hold أو وصول خفي لتقاويم. |
| 87 contingency-scheduling | إعادة التخطيط — عامل ضمن سيناريو صريح | P replan → فروق قبل/بعد؛ S calendar.reschedule بنسخ → تعديل ذري | completed/fixed محفوظان؛ المدعو لا يُحرك آليًا | انقطاع وتبعيات، completed، swap ذري؛ P/S/R/A | لا استنتاج طارئ أو تعديل دون فعل صريح. |
| 88 email-search | بحث البريد — جزئي؛ مستورد محلي | M email-search: account/query → مقتطفات ورسائل وروابط/revisions | RFC822 وعزل الحساب والمشروع؛ بحث حرفي | ar/he/en وسحب المصدر يبطل النتيجة؛ M | ليس بحث IMAP/Gmail/Outlook حيًا. |
| 89 email-thread-summary | تلخيص السلسلة — جزئي؛ مقتطفات مؤرخة | M messageIds → authored excerpts وترتيب وتغييرات وتغطية | Message-ID/References، فصل الاقتباس والتوقيع | الابن قبل الأصل، غياب تاريخ، رفض/جواب جزئي وميزانية ظاهرة؛ M | لا تلخيص دلالي حر كامل. |
| 90 email-action-extraction | إجراءات البريد — جزئي؛ مرشحون موثقون | M messageIds → quote/موضع/حالة، tasks_created=0 | علامات صريحة ومحتوى غير مخول | استبعاد quoted/signature؛ حفظ 50 وليس 500؛ M | لا قبول التزام أو إسناد/تنفيذ تلقائي. |
| 91 email-drafting | صياغة البريد — جزئي؛ مسودة قابلة للتحرير | M create/update/list/delete: نص أو compose بالهدف والحقائق → draft/revision/hash | قالب محلي ar/he/en، CAS ومصادر ثابتة | حفظ الحقائق، إعادة فتح/تعديل، حذف/سحب، إلغاء بعد commit؛ M/I/R | القالب لا يتحقق استقلالًا أو يكتب استدلالًا حرًا؛ لا مزود مسودات. |
| 92 email-reply-context | سياق الرد — جزئي؛ مراجعة بمصدر | M messageIds/replyAll → سياق ومستلمون مقترحون ومراجع reply | رؤوس الرسالة ليست إثبات هوية | Reply-All ظاهر، لا إرسال أو منح هوية من From؛ M | لا رد حر مولد أو إرسال تلقائي. |
| 93 recipient-resolution | حسم المستلمين — جزئي؛ جهات محلية | M names أو contactId/address → resolved/ambiguous/not_found | contacts/revision؛ غير المدعوم لا يحول صامتًا | تشابه أسماء، تغير contact، SMTPUTF8 يرفض؛ M | لا دليل مؤسسي خارجي أو اختراع عنوان. |
| 94 attachment-verification | فحص المرفقات — جزئي؛ مرجع وسياسة | M SourceRef(kind,id,version,sha256) → وجود/نسخة/حجم/بصمة/ACL | skill_files، Guard وسياسة مشتقات المهمة | حذف/تغيير/local-only يرفض؛ المشتق لا يكتسب مشاركة؛ M/R | ليس ضمان خلو برمجيات خبيثة أو منح مشاركة. |
| 95 authorized-email-send | الإرسال — جزئي؛ بروتوكول مملوك مختبر | POST .../productivity/mail/send بمسودة ونسخة وبصمة ومفتاح → محاولة وإيصال | SMTP مشغل وOS keyring ووجهة/مستلم وشبكة مصرحون | رسالة fixture مرة واحدة، Bcc غائب، DATA مجهول لا يعاد؛ M/I | لا حساب خارجي أو TLS إنتاجية مثبتة؛ قبول ليس وصولًا/قراءة. |
| 96 email-followup-tracking | متابعة الردود — جزئي؛ فحص عند الطلب | M track/get/list/delete: submission/dueAt → مشاهدة ودليل محلي ونقص | قبول SMTP سابق وموعد بإزاحة، imported replies | يستبعد auto-reply/DSN، الجزئي لا يكمل كل التزامات، السحب؛ M | لا polling مستمر أو follow-up تلقائي. |
| 97 bounce-analysis | فشل التسليم — جزئي؛ دليل مستورد | M submissionId/dsnMessageId → تطابق المستلم/معرف الرسالة/الرمز | DSN غير مخول وقابل للتزوير | تطابق دقيق ورمز متسق، لا resend أو تغيير authoritative؛ M | لا تحقق webhook/provider حي؛ التطابق لا يثبت الأصالة. |
| 98 meeting-agenda | جدول الاجتماع — عامل بسعة وتبعيات | P agenda: items/minutes/dependsOn/capacity → admitted/deferred/requested | unknown منفصل؛ owners أسماء فقط | allocated ≤ capacity، لا تقصير، دورة/اعتماد مؤجل؛ P/A | لا استنتاج أولويات أو دعوات أو محضر من الأجندة. |
| 99 meeting-minutes | محضر الاجتماع — جزئي؛ ملاحظات مصنفة | N minutes review/save: text أو documentIds وتاريخ → records/lines/issues/version | مصدر بصلاحية وتاريخ اجتماع مرجعي | فصل Proposal/Rejected/Decision/Objection والغموض؛ I/R | ليس تفريغًا صوتيًا أو فهمًا حرًا كاملًا؛ نقص المصدر ظاهر. |
| 100 meeting-actions | إجراءات الاجتماع — جزئي؛ مهام محلية | N actions save، GET/PATCH tasks مع expectedRevision → tasks | source/version/policy ومشروع وحفظ صريح | restart بلا مضاعفة، تعديل/حذف/سحب، completed/reassignment محفوظان؛ I/R | لا تكليف خارجي أو اختراع مسؤول/موعد؛ إعادة التعيين للمراجعة. |

## الإصلاحات المثبتة

ثلاثة أسباب أُثبتت عبر API ثم أصلحت: قائمة محفوظة أعادت عنوان مسودة محذوفة؛ تحديث محضر من ملف جديد لم ينقل سياسة مصدره؛ إلغاء بعد commit أخفى الأثر المحلي. [المراجعة قبل الإصلاح](../../reports/skills-batch-04-20261116/planning/review-before.xml): فشلان/3 نجاح. [الإلغاء قبل الإصلاح](../../reports/skills-batch-04-20261116/planning/review-cancellation-before.xml): فشلان create/update. [بعد الإصلاح](../../reports/skills-batch-04-20261116/planning/review-after.xml): 8 نجاح، منها GET مباشر للقائمة بعد حذف مصدر reply. تبقى أدلة الإخفاق محفوظة.

ثم أثبت [draft-filter-before.xml](../../reports/skills-batch-04-20261116/draft-filter-before.xml) عيب إتاحة: مسودة فقدت مصدرها عطلت عرض مسودة مستقلة مصرح بها. صار listing يفلتر الصف غير المتاح ويعيد البقية بعد التحقق، دون كشف بيانات المسودة المسحوبة. اجتازت مجموعة الفلترة 62 حالة، ثم شملها تحقق M الأحدث ذو 72 حالة؛ هذا العيب الرابع لا يصنف تسريبًا.

أصلحت أيضًا سعة agenda وقواعد DST/busy، ومنحة جديدة كان logout جلسة قديمة يبطلها، وموعدًا يتجاوز حد 366 يومًا. قبل/بعد في تقريري P وS. لا هجمات على خدمة غير مملوكة أو أسرار حقيقية.

## إعادة الفحص والبوابة

    .venv\Scripts\python.exe -m pytest tests/test_skill_batch04_planning.py -q
    .venv\Scripts\python.exe -m pytest tests/test_skill_batch04_schedule.py -q
    .venv\Scripts\python.exe -m pytest tests/test_skill_batch04_mail.py tests/test_skill_batch04_mail_api.py tests/test_skill_batch04_integration.py tests/test_skill_batch04_review.py -q
    .venv\Scripts\python.exe -m pytest tests/test_skill_batch04_integration.py tests/test_skill_batch04_paths.py tests/test_skill_batch04_credentials.py -q
    .venv\Scripts\python.exe reports/skills-batch-04-20261116/planning/measure.py
    npm --prefix frontend run build

Fixtures تستخدم قواعد مؤقتة وSMTP محليًا مملوكًا؛ لا مفتاح مزود مطلوب. قياس 200 تكرار للدالة الخالصة يستبعد الشبكة/DB/UI ولا يمثل وعد زمن المنتج. نجاح build لا يثبت التفاعل أو الإرسال.

البوابة الحالية تخص النطاق المحلي المثبت أعلاه: 8 قدرات عاملة ضمن مدخلات محددة و17 قدرة جزئية بحدودها المبينة. U ناجح في بيئته المحددة؛ الخارجي وWindows الحالي غير معتمدين. الفحص الواسع وعمليات إعادة الفحص المحددة لا تشمل الاختبارات المتجاوزة. لا قبول لمسار فيه فشل عزل أو تفويض أو تكرار أثر. التشغيل والإعداد والرجوع في [REPORT](SKILLS_BATCH_04_REPORT.md)، والبنية في [SPEC](SKILLS_BATCH_04_SPEC.md).
