# الدفعة الرابعة: المهارات 76–100

تاريخ التدقيق: 2026-11-16. المشروع القائم: React 19.2 / TypeScript 5.9 / Vite بالنطاق المعلن ^7.2.0 والمثبت المستخدم للبناء 7.3.8، FastAPI / Pydantic 2.14.3، SQLite محلي، هوية المالك المحلي واقتران المتصفح الحاليان. لم يُعثر على Git أو AGENTS.md؛ أُخذت نسخة خط أساس وبصمات قبل التعديل دون حذف عمل قائم.

## الحالة السابقة ونقطة التكامل

لم يكن هناك مجدول مستمر أو تقويم أو صندوق بريد أو موصل SMTP/IMAP قابل للتشغيل. كانت الدفعات السابقة توفر التوجيه، سجل المهارات، دورة المهمة، الميزانية، الإلغاء، نقاط الاستئناف، Guard، مراجع الملفات وإصداراتها والتسليم. اختبارات خط الأساس المناسبة اجتازت 88/88؛ لا يمثل ذلك نجاحًا سابقًا لهذه القدرات الجديدة.

المسار الحالي: رسالة/نموذج المستخدم ← `SkillRequest` ← اختيار أقل المهارات اللازمة ← تحقق Pydantic وGuard على الخادم ← حجز ميزانية المهمة ومعرف عملية ثابت ← `skill_batch04` ← الخدمات أدناه ← إعادة قراءة الأثر/مصادره ← إيصال المهمة والواجهة. سياق المحادثة يحمل تعليمات المهارات المختارة؛ النص الحواري وحده لا ينفذ ولا يعد بتذكير أو إرسال.

* `productivity_time.py` و`productivity_planning.py`: حسابات حتمية، بلا مزود نموذج أو شبكة أو حفظ تلقائي.
* `productivity_schedule.py`: التقويم الشخصي المحلي، الخطط، التذكيرات، التكرار وصندوق الإشعارات الداخلي في قاعدة البيانات القائمة.
* `productivity_mail.py` و`productivity_mail_transport.py`: رسائل RFC822 مستوردة، جهات اتصال محلية، مسودات، متابعة محددة النطاق، ناقل SMTP اختياري مثبت الوجهة.
* `productivity_meetings.py`: ملاحظات ذات مواضع، تصنيف صريح، مهام محفوظة بهوية المصدر والسجل.
* `productivity_api.py`: استعلامات وإعدادات محلية ونقطة إرسال منفصلة لمسودة راجَعها المستخدم. إنشاء/تعديل الأثر يمر بدورة المهام الحالية.

## العقود والحدود

إضافات `SkillInputs`: `planningOperation` و`scheduleOperation` و`mailOperation` و`meetingOperation`. العقود التطبيقية موجودة في نماذج Pydantic؛ ملفات SKILL.md لا تحاول تفسير حقول الحالة أو الأمان. اختلاف العملية عن المهارة يرفض قبل التنفيذ. الطلب للقراءة فقط لا ينفذ كتابة. الطلب الوارد أو نص البريد أو وصف الحدث ليس تفويضًا.

الهوية تأتي من الجلسة؛ كل جدول مرتبط بالمالك والمشروع، والبريد بالحساب، والحدث بالتقويم. لا وجود لنظام حسابات بديل. الحفظ يستخدم معرفًا ثابتًا ومراجعة متوقعة؛ إعادة الطلب بالمفتاح نفسه لا تنشئ أثرًا جديدًا. المخرجات المشتقة تحتفظ بالتزامات الوصول الحالية. المدخلات الموروثة من مهمة سابقة لا تصبح كتابة مؤجلة أو رسالة جديدة تلقائيًا؛ يلزم إدخال مباشر مراجع في النطاق المدعوم.

التوقيت يعتمد `zoneinfo` في Python 3.12 وقاعدة `tzdata==2026.6` / IANA 2026f. ساعة محلية غير موجودة ترفض، والمتكررة تحتاج اختيار `fold`. اليوم الكامل تاريخ مع نهاية حصرية، لا لحظة منتصف ليل مفترضة. الأسبوع وأيام العمل والورديات والتقديرات تأتي من المدخلات. فترات غير معلنة للنقل والراحة تبقى قيدًا ظاهرًا، ولا تثبت أنها متاحة فعليًا.

التكرار هنا **تذكيرات داخلية محدودة**، وليس تنفيذ تعليمات نموذج أو إرسال بريد دوري. القناة `local:inapp-inbox`؛ العامل يعمل مع خدمة رواق كل خمس ثوانٍ، وتبقى المهمة مخزنة عند الإغلاق. عند إعادة التشغيل تطبق سياسة الفائت المحددة. إنشاء التذكير لا يثبت نشره، والنشر في الصندوق لا يثبت إشعار جهاز أو قراءته. المنح الدائمة محددة بالمهمة والنسخة والوجهة والعدد والانتهاء؛ انتهاء الجلسة الطبيعي لا يلغيها، وتسجيل الخروج الصريح أو سحب المشروع/المنح يوقفها. لا إنشاء تلقائي لخدمة Windows أو مهمة بدء تشغيل.

حدود الجدولة: 366 حدوثًا و366 يومًا كحد أقصى، 100 تذكير نشط، 5000 حدث، 50 حدوثًا لكل دورة. سياسات الفائت `latest` و`skip` و`catch-up` بحد تعويض 10. الحد الافتراضي للتكرار غير المنتهي 30 حدوثًا، ويظهر في الإيصال. لا وعد بتسليم مرة واحدة لدى مزود خارجي؛ منع التكرار هنا مثبت بعملية SQLite ومعرف حدوث فريد.

البريد المحلي لا يزعم اتصال صندوق وارد حي أو حفظ مسودة عند المزود. إرسال SMTP يحتاج وضع الشبكة المسموح في إعداد المنتج، إعداد مشغل محلي، مفتاحًا في مخزن النظام، قائمة وجهات محددة، وعمل إرسال صريح لمسودة مثبتة بالنسخة والبصمة. المنحة قصيرة العمر لا تظهر في نص النموذج أو checkpoint. بعد بدء DATA قد تكون النتيجة مجهولة؛ لا إعادة إرسال تلقائية. قبول SMTP يختلف عن التسليم والقراءة. Bcc يبقى في مظروف النقل ولا يظهر في رأس الرسالة.

المرفقات تستعمل مراجع الملفات الحالية. سماح القراءة المحلي لا يوسع وجهة المصدر: الوثائق الخاصة ومشتقاتها ذات سياسة local-only لا ترسل حتى لو حُسم المستلم. لا روابط عامة بديلة ولا تعديل تلقائي للمشاركة. الفحص البنيوي/الحجمي ليس ضمان فحص أمني شامل.

المحاضر واستخراج الإجراءات في هذه الدفعة حتميان ضمن الأسطر المعلّمة: `Decision:`، `Proposal:`، `Rejected:`، `Objection:`، `Action:`، `Question:` ونظائرها العربية والعبرية. هوية مستقرة مثل `[a1]` تسمح بتحديث المصدر دون خلط المهام. مسؤول/موعد غير مذكور لا يُخترع. `next week` نطاق مؤرخ لا موعد دقيق. النص غير المصنف محفوظ ضمن نتيجة جزئية. هذا لا يثبت فهمًا دلاليًا عامًا للمحادثات الحرة.

حدود الدورة المشتركة السابقة باقية: ثماني مهارات نشطة، 180 ثانية للمهمة، 200 وحدة عمل محلية في اليوم للمشروع، مهمتان متزامنتان، لا تكلفة مالية مؤكدة عند غياب قياس/سعر. SMTP محاولة واحدة بمهلة كلية 60 ثانية؛ لا انتقال إلى موصل أو حساب بديل.

## الإصدارات والتراخيص

لا إضافة إطار أو قاعدة بيانات أو اشتراك. `tzdata` اعتماد أولي من Python Software Foundation، ترخيص Apache-2.0؛ ملف التحميل المفحوص وبصمته وترخيصه في `reports/skills-batch-04-20261116/tzdata-acquisition.json`. حجم العجلة 348210 بايت، وبيانات الفهرس 3613 بايت. التثبيت من العجلة المحلية بـuv دون اعتماديات إضافية. أضيف جمع بيانات التوقيت إلى سكربتات التغليف، دون ادعاء اختبار الحزمة الأصلية الجديدة.

المراجع الرسمية: [Python 3.12 zoneinfo](https://docs.python.org/3.12/library/zoneinfo.html)، [tzdata 2026.6](https://pypi.org/project/tzdata/2026.6/)، [Python 3.12 smtplib](https://docs.python.org/3.12/library/smtplib.html)، [email.parser](https://docs.python.org/3.12/library/email.parser.html). السجل الكامل للقياس والأدلة في ملف ACCEPTANCE، وحدود التسليم في REPORT.

## مصفوفة التدقيق والربط

لم يوجد تنفيذ تخصصي لهذه المهارات في لقطة البداية. الجدول أدناه يحدد العقود والربط البرمجي؛ حالة الاعتماد الحالية ونتائج الواجهة الفعلية وحدودها في ACCEPTANCE.

| الرقم / الفهرس الأصلي | المهارة | المسار المعاد استخدامه والتنفيذ المضاف | المدخل → المخرج | النطاق / الاعتماد |
|---|---|---|---|---|
| 76 / 626 | تخطيط اليوم — daily-planning | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 77 / 627 | تخطيط الأسبوع — weekly-planning | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 78 / 628 | التخطيط حسب الورديات — shift-schedule-planning | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 79 / 629 | إنشاء أحداث التقويم — calendar-event-creation | backend/productivity_schedule.py فوق SkillRequest/Guard | معرف ونسخة ووقت → حالة مخزنة وإيصال | SQLite وعامل الخدمة؛ لا دعوات خارجية |
| 80 / 630 | كشف تعارض المواعيد — calendar-conflict-check | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 81 / 631 | جدولة عبر المناطق الزمنية — timezone-scheduling | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 82 / 632 | إنشاء التذكيرات — reminder-creation | backend/productivity_schedule.py فوق SkillRequest/Guard | معرف ونسخة ووقت → حالة مخزنة وإيصال | SQLite وعامل الخدمة؛ لا دعوات خارجية |
| 83 / 633 | إعداد مهام متكررة — recurring-task-setup | backend/productivity_schedule.py فوق SkillRequest/Guard | معرف ونسخة ووقت → حالة مخزنة وإيصال | SQLite وعامل الخدمة؛ لا دعوات خارجية |
| 84 / 634 | فرز قائمة المهام الواردة — task-inbox-triage | backend/productivity_meetings.py فوق SkillRequest/Guard | ملاحظات ومصدر وتاريخ → محضر/مهام موثقة | أسطر معلّمة؛ لا استنتاج دلالي حر |
| 85 / 635 | حجز فترات التركيز — time-blocking | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 86 / 640 | إيجاد وقت اجتماع — meeting-time-search | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 87 / 650 | إعادة الجدولة عند الطوارئ — contingency-scheduling | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 88 / 651 | البحث في البريد — email-search | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 89 / 652 | تلخيص سلاسل البريد — email-thread-summary | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 90 / 653 | استخراج إجراءات البريد — email-action-extraction | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 91 / 654 | صياغة رسائل البريد — email-drafting | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 92 / 655 | كتابة رد بسياق المحادثة — email-reply-context | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 93 / 656 | التحقق من المستلمين — recipient-resolution | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 94 / 657 | التحقق من المرفقات — attachment-verification | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 95 / 658 | إرسال البريد المصرح — authorized-email-send | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 96 / 660 | متابعة الردود المنتظرة — email-followup-tracking | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 97 / 661 | تحليل رسائل فشل التسليم — bounce-analysis | backend/productivity_mail.py + productivity_mail_transport.py فوق SkillRequest/Guard | حساب ورسائل/مسودة ومراجع → نتيجة محلية أو إيصال SMTP | استيراد محلي؛ SMTP اختياري؛ لا IMAP حي |
| 98 / 662 | إعداد جدول الاجتماع — meeting-agenda | backend/productivity_planning.py + productivity_time.py فوق SkillRequest/Guard | قيود وأوقات مؤكدة → اقتراح محسوب | حساب محلي؛ IANA؛ لا حجز تلقائي |
| 99 / 663 | كتابة محضر الاجتماع — meeting-minutes | backend/productivity_meetings.py فوق SkillRequest/Guard | ملاحظات ومصدر وتاريخ → محضر/مهام موثقة | أسطر معلّمة؛ لا استنتاج دلالي حر |
| 100 / 664 | مهام ما بعد الاجتماع — meeting-actions | backend/productivity_meetings.py فوق SkillRequest/Guard | ملاحظات ومصدر وتاريخ → محضر/مهام موثقة | أسطر معلّمة؛ لا استنتاج دلالي حر |
