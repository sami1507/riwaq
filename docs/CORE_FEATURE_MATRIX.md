# مصفوفة أساس رواق وواجهاته — 18 نوفمبر 2026

الأولوية من تكليف تثبيت المنتج الأخير: إكمال الأساس القائم قبل أي توسع تخصصي. لا يلغى سجل المتطلبات أو معرّفات المراحل السابقة. هذه **لقطة جرد ساكن أولي**، وليست تقرير قبول: لم يشغّل هذا الجرد اختبارات التطبيق. كل رحلة أدناه حالتها الاختبارية **NOT RUN للرحلة الكاملة على مرشح مرحلة التثبيت** حتى يربط تقرير القبول تشغيلًا فعليًا وبصمة مصدر بها. توجد أدلة سابقة جزئية ولا تنسب تلقائيًا إلى هذا المرشح.

مرجع آلي كامل: [inventory.json](../reports/core-stabilization-20261118/inventory.json)، ويحتوي خصائص36 رحلة، التخزين والصلاحيات والاعتماديات والفشل، وبصمات المصدر، واكتشاف مواضع عناصر JSX ومسارات API. اكتشاف العنصر لا يثبت تشغيله. السجل العام [CLOSURE_REGISTER](CLOSURE_REGISTER.md) يبقى منفصلًا؛ لا يعاد حساب نسبه من هذه الرحلات.

الفئات: **أساسية** لازمة لقبول هذه المرحلة؛ **مشروطة بقدرة/خدمة/منصة** تبقى مطلوبة وغير مكتملة حتى يتحقق اعتمادها؛ **امتداد تخصصي** محفوظ في خريطة الاستئناف؛ **اقتراح خارج النطاق** لا يدخل مقام القبول. تعطيل زر لا ينجز قدرة مطلوبة. لا وضع سحابي أو تدريب أو شراء جديد ضمن هذه المرحلة.

## فجوات مثبتة عند الجرد

- القراءة: `App.tsx` يستدعي `scrollIntoView` عند كل تحديث رسائل أو توليد؛ لا يحترم ابتعاد القارئ عن الأسفل.
- المسودة: النص والحاسبة والمسألة في حالة React تبدأ فارغة؛ لا استعادة لمسودة غير مرسلة بعد تحديث الصفحة. لا تستخدم تخزينًا عامًا يسرّب مسودة بين مالكين لحلها.
- تنظيم المحادثة: لم يظهر مسار إعادة تسمية/أرشفة/نقل في `app.py` أو واجهة المحادثة عند الجرد. `CORE-020` غير منفذ، ولا يسقط لأنه غير ظاهر.

هذه النتائج مربوطة ببصمات ومواضع اللقطة داخل JSON؛ عند الإصلاح يضاف الدليل الجديد ولا يمحى التشخيص.

## الرحلات ومعيار الإغلاق

المقام ثابت36 رحلة؛ صفر رحلة أُغلقت بواسطة **الجرد نفسه**. الحالات التنفيذية أدناه مستقلة عن درجة الاختبار. عبارة «منفذ وغير متحقق» لا تنفي الاختبارات التاريخية، وإنما تمنع نسبة قبول18نوفمبر دون فحص نسخته.

| الرحلة | الخاصية / الفئة | الوصول والمنفذ | السلوك المطلوب والفشل | الحالة الأولية وما بقي | الفحص المرجعي |
|---|---|---|---|---|---|
| CORE-001 | أول تشغيل بلا مزود / أساسية | PairingGate / App / Settings → app.status / providers | تهيئة واقعية والآلة الحاسبة مستقلة | منفذ وغير متحقق؛ غياب المزود لا يحجب إنشاء المشروع والحساب؛ لا رد وهمي | `tests/test_execution_capability_status.py`؛ `tests/test_app.py` |
| CORE-002 | كل إجراء ظاهر / أساسية | جميع الشاشات في جدول الإجراءات → واجهات API أدناه | تعطيل مفسر وخطأ قابل للتصرف | جزئي؛ جرد ثابت موجود؛ المرور التفاعلي الكامل لم ينفذ على هذه النسخة | `frontend/scripts/ui-integration.mjs` |
| CORE-003 | واجهة ضيقة ومحرر مفتوح / أساسية | App / ProjectPanel / Dialog → لا خدمة جديدة | الإرسال والإلغاء والتركيز لا تختفي | منفذ وغير متحقق؛ اختبر 390px ولوحة مفاتيح محاكية ومرفق ولوحة مفتوحة؛ اللقطات السابقة لا تكفي | `frontend/scripts/identity-v2-qa.mjs` |
| CORE-004 | عربية ونص مختلط / أساسية | Markdown / MathExplanation / export → conversation export / math worker | رموز قابلة للنسخ دون قلب اتجاهها | جزئي؛ اجتازت رحلة UI بتاريخ18نوفمبر15حالة؛ يبقى قبول الجدول والنسخ والتصدير في CORE كاملًا غير مثبت | `frontend/scripts/math-medical-ui.mjs`؛ `tests/test_output_contracts.py` |
| CORE-005 | الوصول بلوحة المفاتيح / أساسية | PairingGate / App / Dialog → نفس API | Escape وTab وتسميات قابلة للفهم | جزئي؛ Dialog يحصر التركيز؛ قبول قارئ الشاشة والرحلة الكاملة غير متحقق | `frontend/src/ui.tsx`؛ `frontend/scripts/identity-v2-qa.mjs` |
| CORE-006 | إعدادات مستقرة ومؤثرة / أساسية | Settings / PersonalMemoryPanel → settings / personal_memory_api | فشل الحفظ ظاهر؛ القيم لا تتجاوز المالك | جزئي؛ المظهر واللغة محفوظان بالمتصفح؛ مستوى الشرح الجديد مؤقت وليس تفضيلًا دائمًا | `tests/test_personal_memory_api.py`؛ `frontend/src/preferences.ts` |
| CORE-007 | محادثة حية محفوظة / أساسية | App composer / message list → POST generate / providers.stream | بث وpartial/interrupted وحفظ النموذج الحقيقي | جزئي؛ أدلة حية سابقة؛ يوجد auto-scroll قسري يحتاج إصلاحًا/تحققًا | `tests/test_generation_reliability.py`؛ `frontend/scripts/live-workflows-ui.mjs` |
| CORE-008 | إيقاف التوليد / أساسية | Stop / Escape / retry → POST stop / ACTIVE event | لا delta متأخر ناجح؛ الجزئية مميزة | منفذ وغير متحقق؛ كرر بثًا فعليًا وإيقافًا ثم إعادة فتح النسخة المجمدة | `tests/test_generation_reliability.py`؛ `tests/test_project_authority_boundaries.py` |
| CORE-009 | فقد الشبكة وإعادة الإرسال / أساسية | Composer / retry → generate / generation transaction | المدخل الأصلي لا يضيع والمحاولة لا تتضاعف | جزئي؛ مسودات النص والحساب غير محفوظة عند reload في لقطة الجرد | `tests/test_generation_reliability.py`؛ `frontend/src/client-session.ts` |
| CORE-010 | تعديل وتفرع / أساسية | Message edit / retry → generate edit_message_id / retry_message_id | حفظ الفرع الأصل ورفض التعارض | منفذ وغير متحقق؛ التفرع موجود؛ لا تخلطه بتنظيم/إعادة تسمية/أرشفة المحادثة الناقصة | `tests/test_research_chat.py`؛ `tests/test_generation_reliability.py` |
| CORE-011 | قدرة نموذج غير مدعومة / أساسية | Model selector / media capability panels → providers / media / packaged_workers | لا إرسال صورة إلى text-only ولا fallback خفي | منفذ وغير متحقق؛ اختبر نموذجًا فعليًا غير داعم ورسالة القيد في UI | `tests/test_execution_capability_status.py`؛ `tests/test_media.py` |
| CORE-012 | خصوصية التحويل الاحتياطي / أساسية | Settings local/hybrid → providers / guard | فشل محلي صادق؛ صفر خروج غير مخول | منفذ وغير متحقق؛ رفض معماري واختبارات سابقة؛ لا تفعيل سحابي لتجاوز الفشل | `tests/test_guard_policy.py`؛ `tests/test_skill_batch05_provider_regressions.py` |
| CORE-013 | رفع واستخراج / أساسية | ProjectPanel / DocumentWorkbench → knowledge / documents / document_page worker | رفع ومعالجة وتحذيرات جزئية منفصلة | منفذ وغير متحقق؛ نص/OCR مساران موجودان؛ يلزم شاهد UI حديث مع قراءة موضع معلوم | `tests/test_documents.py`؛ `tests/test_document_isolation_lifecycle.py` |
| CORE-014 | إجابة من ملف / أساسية | Use files / citations / DocumentWorkbench → grounding / research_answer / retrieval | لا مصدر وهمي؛ فتح موضع الادعاء | جزئي؛ النطاق الدلالي محدود؛ الاستشهاد وحده لا يثبت الدعم | `tests/test_knowledge_chat.py`؛ `tests/test_ai_core_grounded_chat.py`؛ `tests/test_claim_hebrew_correction.py` |
| CORE-015 | حساب جدول كامل / أساسية | Document table / Skills analysis → table-calculate / skill_data | حدود الصفوف معلنة؛ null مختلف عن0 | منفذ وغير متحقق؛ 100صف/12عمود في التقرير المرتبط؛ لا تعميم للحساب من جدول أكبر من الحد | `tests/test_skill_data_revision.py`؛ `tests/test_documents.py` |
| CORE-016 | تحديث ملف / أساسية | DocumentWorkbench upload/correct → documents upload / patch element | لا استعمال نسخة قديمة كأنها الأحدث | جزئي؛ رفع محتوى جديد بالاسم نفسه ينشئ file id جديدًا مع version؛ لا replace ثابت الهوية صريح في المكتبة | `tests/test_documents.py`؛ `tests/test_document_snapshots.py` |
| CORE-017 | فشل رفع وإلغاء / أساسية | DocumentWorkbench / ProjectPanel → upload / cancel / resume | رفض مفهوم وتنظيف دون ready وهمي | جزئي؛ مساحة المستندات لها cancel؛ رافع ProjectPanel القديم يحتاج فحص cancellation مستقل | `tests/test_document_isolation_lifecycle.py`؛ `tests/test_documents.py` |
| CORE-018 | تنزيل بعد استعادة / أساسية | Artifacts / skills files / downloads → artifact export / skill_files / snapshots | فشل حفظ لا يعطي رابط نجاح | منفذ وغير متحقق؛ أدلة Office فعلية موجودة؛ قبول restart وتنزيل للنسخة الحالية مطلوب | `tests/test_skill_artifacts.py`؛ `tests/test_snapshot_studio_revocation.py` |
| CORE-019 | سياقان لمشروعين / أساسية | Project sidebar / files / instructions → require_project / context_lineage | تبديل المشروع يبطل العرض المتأخر | منفذ وغير متحقق؛ 338فحصStudio/snapshot ذات صلة محفوظ؛ ليس قبول كل UI | `tests/test_project_authority_boundaries.py`؛ `tests/test_grounding_context.py` |
| CORE-020 | نقل محادثة / أساسية | غير موجود في لقطة الجرد → لا PATCH/move route للمحادثة في app.py | لا نقل ضمني للصلاحيات أو المرفقات | غير منفذ؛ نفذ عقد نقل بمصدر/وجهة/version ومعيار أهلية؛ لا تسقط المتطلب لأنه زر غير موجود | `backend/app.py`؛ `backend/db.py`؛ `frontend/src/App.tsx` |
| CORE-021 | تذكر وتصحيح / مشروطة بقدرة أو بوابة | Memory / PersonalMemory / Learning → memory / learning / personal_memory | الطلب الحالي مقدم؛ لا حفظ خفي | جزئي؛ المسار الهندسي موجود؛ لا فتح بوابة شخصية بموافقة الاختبار الاصطناعي | `tests/test_personal_memory_api.py`؛ `tests/test_project_memory_versions.py` |
| CORE-022 | نسيان وعامل متأخر / أساسية | Memory / Learning delete → personal_memory / learning / lineage | لا إحياء أو استرجاع مشتق مسحوب | منفذ وغير متحقق؛ راجع التنزيل والاستعادة أيضًا؛ tombstoneليس محوWALآمن | `tests/test_project_memory_versions.py`؛ `tests/test_studio_lineage_review.py` |
| CORE-023 | وضع مؤقت / أساسية | chat options / medical ephemeral dialog → chat persists; medical explicit ephemeral | الوصف يطابق ما يخزن فعلًا | جزئي؛ لا وضع محادثة مؤقت عام مثبت؛ عدم اختيار الذاكرة لا يعني عدم حفظ سجل المحادثة | `tests/test_medical_education.py`؛ `frontend/src/MedicalWorkbench.tsx` |
| CORE-024 | وصول غير مخول / أساسية | كل رابط/ملف/نتيجة → guard.require_project / file_policy | حجب القوائم والبحث والأثر والتصدير | منفذ وغير متحقق؛ إصلاحاتGAP037–040 موجودة؛ اعتماد شامل بعدregressionمجمد | `tests/test_project_authority_boundaries.py`؛ `tests/test_visual_authority.py`؛ `tests/test_voice_authority.py` |
| CORE-025 | تعديل يدوي أثناء التوليد / أساسية | ProjectPanel / Studio editor → artifact expected_version / studio versions | 409 يبقي مسودة المستخدم | منفذ وغير متحقق؛ الحفظ صريح وليس autosave؛ اختبر الرد المتأخر من الواجهة | `tests/test_studio.py`؛ `tests/test_studio_lineage_review.py` |
| CORE-026 | تعديل محدد وتصدير / أساسية | Studio / Coding diff / export → studio / coding apply undo / artifacts | تغييرالنطاق/فرق/رجوع/تنزيل مطابق | جزئي؛ المستند النصي يحرر ويحفظ؛ تعديلجزءمقترحبالنموذج ليس شاملًا لكلنوع | `tests/test_coding_edits.py`؛ `tests/test_studio.py` |
| CORE-027 | أداة حساب فعلية / أساسية | Calculator / Supported math / ledger → fixed checker / reasoning_math | أخطاءالمحرك لا تعني لاحل | منفذ وغير متحقق؛ RM20/20 و334pytestمحفوظة؛ لا ادعاءتحسننموذج أوOCR | `tests/test_math_api.py`؛ `tests/test_math_extension_api.py` |
| CORE-028 | محتوى يغيّر الصلاحيات / أساسية | File/research/preview path → grounding / guard / fixed workers | المحتوى غيرالموثوق لا ينفذ أو يمنحadmin | منفذ وغير متحقق؛ إعادةالفحوصعلىالنسخةالمجمدة؛ لا شهادةعزلOSلكلworker | `tests/test_guard_policy.py`؛ `tests/test_skill_runtime_security.py`؛ `tests/test_research_security.py` |
| CORE-029 | مصدر بحث غير متاح / أساسية | SearchDialog / ResearchReceipt → research / knowledge_fetch / Brave search | لا اقتباس أو قراءةمزعومة عندالفشل | منفذ وغير متحقق؛ المسارالحاليكتالوجمحدود؛ البحثالعاميتطلبمفتاحًاوبوابةمعلومة | `tests/test_research_chat.py`؛ `tests/test_research_answer.py` |
| CORE-030 | إعادة محاولة أداة / أساسية | Skills / job retry → skill runtime / isolation journal | تعارضمفتاح/حمولة يرفض؛ لا أثرمكرر | منفذ وغير متحقق؛ لا إعادة إرسالخارجيحقيقيخلالاختبارإعادةالتشغيل | `tests/test_skill_batch02_lifecycle.py`؛ `tests/test_isolated_journal.py` |
| CORE-031 | قدرة صور / مشروطة بقدرة أو خدمة | VisualWorkbench / MediaDialog → visuals / visual_jobs / media | نتيجةفعليةأوخدمةغيرمتاحة | جزئي؛ VIS12 آخرUI 5/5مثبت؛ صورمولدةبجودةعامةوتحريرموضعيمازالامتدادًا | `tests/test_visual_perspective_print.py`؛ `tests/test_image_normalize_isolation.py` |
| CORE-032 | الميكروفون والتوقف / مشروطة بقدرة أو منصة | VoiceWorkbench → voice / media_decode / voice_audio | رفضإذن وإيقاف tracks وعدمإحياءردمتأخر | منفذ وغير متحقق؛ المطلبالأحدثيعيدالتحقق؛ استخدمfake audio/permission denial دونتسجيلصوتالمستخدم | `tests/test_voice_sessions.py`؛ `tests/test_voice_authority.py`؛ `frontend/scripts/voice-ui.mjs` |
| CORE-033 | مهمة طويلة بعدإغلاقUI / أساسية | Skills jobs / Documents jobs / schedule → background jobs / productivity scheduler | إغلاقنافذة لا يساوي اكتمالًا أوإلغاء | جزئي؛ أنواعالمهاممختلفة؛ لا توجدقائمةموحدةلكلعمالالمنتج | `tests/test_schedule_standalone.py`؛ `tests/test_coding_recovery.py`؛ `tests/test_document_isolation_lifecycle.py` |
| CORE-034 | ويب وسطح مكتب / مشروطة بمنصة مطلوبة | App + Tauri shell → packaged launcher / fixed workers | حفظوفتحوتنزيلعلىكلمنصة | جزئي؛ v3تاريخية؛ الجديدغيرمعبأ/غير متحققأصليًا؛ CORE الأحدثيعيدشرطالفحصالأصلي | `scripts/verify_packaged_runtime.py`؛ `tests/test_packaged_dispatch.py` |
| CORE-035 | استعادةوجلسة / أساسية | Sync/backup / logout / pairing → snapshots / local_auth / recovery_reconciliation | لااستعادةبياناتأوصلاحياتمسحوبة | جزئي؛ snapshotمنطقيذونطاقمحدود؛ fullmedia/RTO/RPOومراجعةمستقلةباقية | `tests/test_snapshot_studio_revocation.py`؛ `tests/test_recovery_reconciliation.py`؛ `tests/test_guard_auth.py` |
| CORE-036 | بوابةالأساسواستئنافالمراحل / أساسية | docs readiness handoff → matrix + phase map + report | كل رحلةحالةمستقلةوالمراحللايعادترقيمها | قيد الإصلاح؛ هذاالجرديفتحالمرحلةولايسجلاجتيازالقبول | `docs/CORE_FEATURE_MATRIX.md`؛ `docs/PHASE_RESUME_MAP.md` |

## الشاشات والإجراءات وربطها

كل قائمة أدناه تجمع الإجراءات المتشابهة؛ `inventory.json` يحفظ المواضع المنفردة المكتشفة في الشيفرة. جرد سلوك كل حالة انتظار/خطأ/صلاحية في المتصفح يبقى جزءًا من CORE-002، لا نجاحًا ناتجًا عن هذا الجدول.

| الشاشة / المكوّن | الأزرار والقوائم والنماذج الفعلية | الخدمة والتخزين | الصلاحية/الفشل والحالة |
|---|---|---|---|
| PairingGate | اقتران، فحص جديد، لغة، مظهر | `/auth/status/login`؛ cookie HttpOnly ومالك DPAPI | رمز مؤقت/فشل ظاهر؛ لا تحميل بيانات قبل الاقتران |
| App sidebar/topbar | مشروع جديد/تعديل، محادثة جديدة/بحث/فتح/حذف/تصدير، فتح الأدوات، خروج | projects/conversations/export؛ SQLite | مالك من الخادم؛ rename/archive/move للمحادثة ناقصة في لقطة البداية |
| Composer | نص متعدد الأسطر، إرسال/إيقاف، مرفق، أوضاع المساعد/الحساب/المسألة، مصادر وعمق ومخطط JSON | generate/stop؛ messages/runs/fact bindings | انتظار/إلغاء/خطأ؛ المسودات وموضع القراءة فجوتان مثبتتان |
| رسالة | نسخ، تحرير/تفرع، إعادة محاولة، ملاحظة، من الفكرة إلى ملف، فتح مرجع، سجل الأداة | generate/studio/learning/evidence | المرجع ونسخته لا ينتجان صلاحية؛ منع متأخر بعد السحب |
| Settings | نموذج/وضع، مفاتيح/اختبار اتصال بحسبالموصل، لغة/مظهر/أرقام/تاريخ، تفاصيلحماية | `/settings` وOS keyring | تفعيلcloud/ذاكرةشخصيةتحكمهبوابة؛ لا تعرضمفتاحًا |
| ProjectPanel | حالة/ملفات/مخرجات، تسجيلحدث، رفع، فتحالمستندات، إنشاء/فتح/تعديل/معاينة/حفظ/نسخ/رجوع/تصدير | events/files/artifacts/versions | CAS وحفظصريح؛ رفضتعارض؛ الرفعالقديملايغطيكلإجراءاتالمكتبة |
| DocumentWorkbench | رفع، اختيارصفحة/عنصر، تصحيحوتاريخ، بحث/اسأل/حسابجدول/مقارنة، معاينة/تنزيل/تصدير/حذف، إلغاء/استئناف | documents/jobs/originals/elements/index | مالكوبصمةملف ومراجعمتجددة؛ خطأالمحللليسنجاحًا |
| StudioPanel | مصطلحات/قرارات؛ إضافة/تحرير/بحث/حذف/نسخ/رجوع، إنشاءمستندمنرد | studio entries/versions/source receipts | مصدرمفقودبلاreceiptيحجب؛ importerلايمنحتفويضًا |
| FactLedger | اختيارحقائق، إضافة/تحرير/نسخ/إبطال | facts/versions/sources | صلاحيةالمصدر؛ المتعارضلايصبححقيقةبتصويت |
| Memory / PersonalMemory | عرض/إضافة/تعديل/نسيان/تعطيل حسبالنطاق | memories/personal_memory/consent/tombstones | بوابة423للشخصية؛ لا إحياءبعاملقديم |
| LearningPanel | مفيد/غيرمفيد/تصحيح، درس/اختبار/اعتماد/رجوع/نزاع/حذف | feedback/error/candidate/evaluation/release | إشعارالاستلامليساعتمادًا؛ مراجعةوقبولحسبنوعالقاعدة |
| KnowledgeDialog | مصادر/استيراد/تحديث/بحث/إيقاف/مراجع/إعداد/استخدامنتيجة | public_knowledge/jobs/versions/index | حقوق/حصص/نسخة؛ المحليلايرسلللخارج |
| Research controls/receipt/evidence | اختيارنطاق/لغة/نسخة، فتحالمقطع، إلغاء | research/knowledge/file_scope | لا تعنيcitationوجوددعم؛ فشل المصدرظاهر |
| SearchDialog | بحث، فتحإعدادات، رابطنتيجة، إضافتهاكمسودة | `/search/web`؛ لا حفظلقراءةمزعومة | Braveمحتاجإعداد؛ النتائجsnippetsلمتقرأالصفحة |
| WorkspaceDialog | اختيارنطاقمجلد، ملفات/بحث/معاينة/إضافةللمسودة | workspace/grants | روابطومساراتأسرارمرفوضة؛ لا وصولخارجgrant |
| CodingWorkbench | ربطمجلد/سياق، اقتراحتعديل، diff/apply/undo، فحصنسخة/إلغاء | coding/workspace/checks/leases | CAS وgrant؛ لا تنفيذكودعلى المضيفعندغيابDocker |
| SkillsWorkbench | كتالوج/بحث/اختيار، حقولكلمنفذ، تشغيل/حالة/إلغاء/إعادة/استئناف/مراجعة، ملفات/تنزيل/تصحيحتقرير | skill registry/runtime/jobs/files/recipes | idempotency/ميزانية/تفويض؛ ملفSKILLوحدهليسقدرة |
| VisualWorkbench | أصول/رفع/توليد/تصميم، طبقاتونصوقصوموضعوأربعزوايا، كودQR/Code128، نسخ/حفظ/تصدير/DPI/إلغاء | visuals/jobs/assets/design versions | capabilityمنالخادم؛ PNGsRGB/DPIليساعتمادCMYK/مطبعة |
| VideoWorkbench | أصول/لقطات/كتابة/ترجمةبتوقيت/صوت/تصدير/إلغاء | video/jobs/timeline/assets | تركيبمحلي؛ التوليدالمتقدمغيرمهيألايعرضنجاحًا |
| VoiceWorkbench / MediaDialog | إذن/تسجيل/إيقاف/تفريغ/تصحيح/محادثة/ترجمة/حفظصريح/اختيارصوت | voice/session/media_decode/Whisper/SAPI | epoch/token/ملكية وإبطال؛ أصواتالعربيةغيرمثبتة |
| MedicalWorkbench | موضوع/مستوى/حقول/عرضشرح/إلغاء/تصحيح/مراجع | medical_api؛ مؤقتدونحفظأومزود | نطاقتعليمي؛ لا جرعةأوتشخيصأومراجعةسريريةمزعومة |
| MathInputHelp / MathExplanation | أمثلة، مبسط/مفصل/تقني، تمثيلآخر/مثالإضافي/تحقق/نقاطالرسم | fixedchecker→runreceipt؛ مستوى الشرحمحليمؤقت | الشروطثابتة؛ فتحالفحصلاأنهإعادةحساب؛ المعادلةوالرسممبنيانمننتيجةواحدة |
| SyncPanel | تصدير/تشفير/استيراد/استعادة/إعدادات | snapshots/sync/keyring | استعادةحجرومطابقةصلاحيةحالّية؛ لا صلاحيةمستوردة |
| GuardPanel | تشخيص/سياسات/إذنمحدد/سجلات/تجربةحماية | guard_api/leases/audit | الحجبليسنجاحتنفيذ؛ لاتجاوزلبوابةالأمن |

## ما يحفظ للمراحل اللاحقة

التوسع إلى رياضيات عامة وOCR معادلات وبرهان رسمي؛ أحكام طبية/حسابات سريرية/تحديثدوائي حي ومراجعة مختصة؛ تدريبأوزان جديد؛ توليدفيديو متقدم؛ صوت عربيولهجات معتمدة؛ ومعاييرمنصاتغيرمتاحة: **امتدادات تخصصية محفوظة** مع أصلها في [خريطة الاستئناف](PHASE_RESUME_MAP.md). إصلاح حارس مشترك أو ربط قدرة موجودة يحسب مرة واحدة في الأساس ويربط بالمرحلة المستفيدة.

تقليد كل خصائص ChatGPT/Claude، إنشاء اشتراكات/أسعار، بناء نموذج جديد أو نظامحساباتسحابي جديد: **اقتراحات خارج هذه المرحلة** ما لم يوجد متطلب مستقل مخول. أما البحث/النقل/التنزيل/الخصوصية المطلوبة فلا تتحول إلى اختيارية بسبب تعذرها.

## قرار البوابة

**لم تجتز بوابة الأساس بعد.** توجد فجوات تنفيذ محلية وأدلة نهائية لم تُشغّل بعد، إضافة إلى قدرات مشروطة. التقرير النهائي يجب أن يربط كل رحلة بنتيجة PASS/FAIL/SKIPPED/BLOCKED/NOT RUN، والأمر والبيئة وبصمةالشيفرة. الاختبارات المتداخلة لا تجمع كنسبةجودة، ونطاق المتصفح لا يثبت نافذة Tauri أو مراجعة بشرية.
