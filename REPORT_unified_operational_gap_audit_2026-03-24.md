# التقرير الموحد لفجوات التشغيل والإغلاق

تاريخ التقرير: `2026-03-24`

## الملخص التنفيذي

المشروع وصل إلى مستوى وظيفي عالٍ، لكنه لم يصل بعد إلى مستوى “استقرار إنتاجي مغلق” لسبب واحد رئيسي:

- توجد مرونة زائدة في الحدود بين `frontend runtime` و`sync queue` و`backend replica`.

هذا يجعل النظام قادرًا على التعافي في كثير من الحالات، لكنه ما زال معرضًا لتكرار أخطاء تشغيلية في:

- المزامنة.
- الاتساق بين السجل/الرئيسية/التقارير.
- إغلاق الميزات التشغيلية على نحو موحد.

## الحكم الهندسي الحالي

- **الفرونت**: قوي من حيث التغطية والميزات، لكنه يحمل تعقيدًا تشغيليًا مرتفعًا.
- **الباكند**: منظم كـ `sync replica + reports backend`، لكنه ليس بعد runtime authority كاملة.
- **المخاطرة الأساسية**: النظام ما زال `corrective-heavy` أكثر من كونه `preventive-by-design`.

## الأدلة الأساسية

### الفرونت

- self-heal للمزامنة أصبح جزءًا من boot/hydrator والطابور:
  - `hisabi-your-smart-wallet/src/lib/syncEngine.ts:45`
  - `hisabi-your-smart-wallet/src/pages/SyncQueuePage.tsx:140`
- منطق repair/rebuild في الطابور كبير جدًا:
  - `hisabi-your-smart-wallet/src/lib/syncQueueService.ts:1457`
- وثيقة الإغلاق الحالية تعلن أن فجوات `P0` مغلقة، لكن الواقع التشغيلي الحالي يدل أن hardening ما زال مطلوبًا:
  - `hisabi-your-smart-wallet/REPORT_project_closure_gap_plan.md:228`

### الباكند

- المعمارية المعلنة: backend هو `sync replica + remote report provider` وليس runtime source of truth:
  - `hisabi_backend/docs/ARCHITECTURE_V1.md:4`
- قواعد التكامل والنسخ والإصدار موجودة:
  - `hisabi_backend/docs/ARCHITECTURE_V1.md:19`
- محرك إعادة الحساب موجود ويغطي الحسابات/الميزانيات/الأهداف/الديون:
  - `hisabi_backend/domain/recalc_engine.py:91`
- توجد فجوات تعاقدية موثقة بين الفرونت والباكند:
  - `hisabi_backend/REPORT_gap_matrix.md:10`
  - `hisabi_backend/REPORT_gap_matrix.md:25`
  - `hisabi_backend/REPORT_gap_matrix.md:33`

## الفجوات الحرجة

### P0 — فلسفة التشغيل غير محسومة بالكامل

- النظام يعمل فعليًا كـ `local-first financial runtime` مع مزامنة لاحقة.
- لكن سلوك بعض الشاشات والمستخدمين يتوقع `online-first direct cloud`.
- النتيجة: ارتفاع تعقيد repair paths وتعذر تفسير بعض حالات الفشل للمستخدم.

**الإغلاق المطلوب**

- تثبيت قرار معماري نهائي مكتوب:
  - `Local-first with strict outbox`
  - أو `Online-first with offline cache`
- ثم مراجعة كل feature على هذا القرار.

### P0 — طبقة المزامنة تحمل منطقًا علاجيًا أكثر من اللازم

- وجود `repair/quarantine/rebuild/retry/self-heal` بهذا الحجم يعني أن صحة الصف قبل enqueue ليست مضمونة بالكامل.
- أي مشروع مالي يجب أن يقلل sharply من الصفوف غير الصالحة التي تصل أصلًا إلى queue.

**الإغلاق المطلوب**

- جعل enqueue contract صارمًا:
  - لا row تدخل queue ما لم تكن:
    - wallet-scoped
    - entity-complete
    - dependency-safe
    - operation-valid
- نقل كل repair التاريخي إلى background maintenance layer وليس UI.

### P0 — مصدر الحقيقة للاشتقاقات ليس موحدًا نهائيًا

- الاتساق بين:
  - `ledger`
  - `dashboard`
  - `reports`
  - `account balances`
  ما زال يعتمد على تناغم عدة طبقات.

**الإغلاق المطلوب**

- بناء contract موحد للاشتقاقات:
  - إما `local projection engine` واحد authoritative.
  - أو `backend authoritative projections` مع invalidation واضح.

### P0 — parity الميزات السحابية غير مغلقة بالكامل

من تقرير gap matrix:

- الحسابات والفئات والمعاملات والديون لديها field mapping gaps:
  - `hisabi_backend/REPORT_gap_matrix.md:10`
  - `hisabi_backend/REPORT_gap_matrix.md:11`
  - `hisabi_backend/REPORT_gap_matrix.md:12`
  - `hisabi_backend/REPORT_gap_matrix.md:18`
- تم حسم تغطية الكيانات التشغيلية المتبقية:
  - `Hisabi Attachment` أصبح backend-backed ضمن عقد v1.
  - `Subscriptions` و`Split bills` و`Family` أصبحت `local-only` صريحة ضمن حدود v1 الحالية.

**الإغلاق المطلوب**

- أي كيان ظاهر للمستخدم يجب أن يصنف صراحة:
  - `cloud-operational`
  - أو `local-only`
- ومنع أي وضع ثالث “نصف سحابي”.

## الفجوات المهمة

### P1 — صفحات واجهة ضخمة جدًا

الصفحات التشغيلية الكبيرة تعني أن جزءًا من orchestration ما زال داخل الـ UI:

- `AddTransactionPage`
- `ImportPreviewPage`
- `HomePage`
- `DebtsPage`

**الأثر**

- صعوبة اختبار السلوك المركب.
- صعوبة حماية invariants.
- بطء التطوير وارتفاع regressions.

**الإغلاق المطلوب**

- استخراج use-cases/domain coordinators من الصفحات إلى:
  - `repos`
  - `services`
  - `runtime coordinators`

### P1 — realtime والـ sync ما زالا متداخلين

- في الوضع المثالي:
  - `local write`
  - `projection rebuild`
  - `runtime notification`
  - `background sync`
  يجب أن تكون مراحل منفصلة وواضحة.

**الإغلاق المطلوب**

- فصل واضح بين:
  - write pipeline
  - projection pipeline
  - transport pipeline

### P1 — backend reports ليست بعد contract مغلق مع الفرونت

من التقرير الحالي:

- budget report mismatch:
  - `hisabi_backend/REPORT_gap_matrix.md:28`
- goals report mismatch:
  - `hisabi_backend/REPORT_gap_matrix.md:29`
- bucket rules mismatch:
  - `hisabi_backend/REPORT_gap_matrix.md:31`

**الإغلاق المطلوب**

- إقفال response contracts رسميًا باختبارات contract ثابتة لكل endpoint report.

## الفجوات المتوسطة

### P2 — قابلية المراقبة ما زالت مركزة على شاشة الطابور

- هناك حاجة إلى observability تشغيلية أعلى من مجرد queue page.

**الإغلاق المطلوب**

- dashboard تشغيلية موحدة تشمل:
  - pending
  - ready
  - sent
  - failed
  - poisoned
  - stale projections
  - rebuild lag

### P2 — الوثائق تعلن إغلاقًا أسرع من الواقع

- وثيقة الإغلاق الحالية تشير إلى غلق كل `P0`:
  - `hisabi-your-smart-wallet/REPORT_project_closure_gap_plan.md:244`
- بينما الواقع التشغيلي الأخير يثبت أن sync hardening ما زال مستمرًا.

**الإغلاق المطلوب**

- فصل “release ready” عن “architectural closure”.

## ما يجب إغلاقه أولًا

### Wave 1 — Sync Hardening Final

1. منع أي enqueue invalid.
2. نقل self-heal بالكامل إلى background boot/hydrator.
3. منع queue UI من لعب دور طبقة إصلاح أساسية.
4. إقفال dependency queueing للحسابات/الفئات/الديون.

### Wave 2 — Projection Authority

1. تعريف authoritative source لـ:
   - home summary
   - reports summary
   - ledger totals
   - account balances
2. إزالة أي مسارات حساب موازية غير موثقة.

### Wave 3 — Feature Classification

1. تصنيف كل feature:
   - cloud-operational
   - local-only
2. إغلاق parity matrix على هذا الأساس.

### Wave 4 — Backend Contract Closure

1. توحيد field mappings.
2. إغلاق report response contracts.
3. إكمال أو تعطيل الكيانات غير المدعومة سحابيًا رسميًا.

## قائمة الإغلاق العملية

### يجب إغلاقها قبل إعلان استقرار إنتاجي

- `sync queue` يجب أن تصبح mostly preventive.
- `dashboard/report/ledger` يجب أن تعمل على projection contract واحد.
- تم حسم `Attachments / Subscriptions / Split Bills / Family`:
  - `Attachments` backend-backed ضمن عقد v1 الحالي.
  - `Subscriptions / Split Bills / Family` حدودها `local-only` صريحة في v1.
- كل mismatch موثق في `REPORT_gap_matrix.md` يجب إما إصلاحه أو وسمه رسميًا `local-only / deferred`.

### يمكن تأجيلها بعد الإطلاق الحذر

- تحسينات DX.
- تبسيط إضافي للصفحات الكبيرة.
- dashboards تشغيلية أوسع للإدارة.

## القرار الحالي

- **المشروع ليس في حالة “architectural closure” بعد.**
- **لكنه قابل للوصول إلى guarded production stability** إذا أغلقت موجتا:
  - `Sync Hardening Final`
  - `Projection Authority`

## التوصية النهائية

- لا أنصح باعتبار المشروع “منتهيًا نهائيًا” قبل إغلاق:
  1. `sync preventive contract`
  2. `projection authority contract`
  3. `feature classification closure`

- أنصح بأن تكون الخطوة التنفيذية التالية:
  - إنشاء `Operational Parity Matrix v2`
  - ثم `Sync Hardening Final Checklist`
  - ثم ربط كل feature بقرار معماري نهائي: `cloud-operational` أو `local-only`
