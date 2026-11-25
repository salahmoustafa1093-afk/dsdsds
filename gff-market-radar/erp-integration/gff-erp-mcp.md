# توصيل رادار بنظام GFF ERP

## الوضع الحالي (من مراجعة الكود في `gff-backend`)
- الباك إند NestJS + Prisma + PostgreSQL، وكل المسارات محتاجة JWT وصلاحيات.
- **الواجهة كلها بيانات وهمية** (mock)، و`POST /ai/query` بيطابق كلمات بقواعد ثابتة ومفيهوش ذكاء اصطناعي فعلي.
- اللي ينفع يتقرا النهارده:
  - `GET /sales/invoices` (فلترة بالتاريخ والفرع والقناة والمندوب، ومعاه بنود الفاتورة)
  - `GET /sales/reports/by-product` و `/by-customer`
  - `GET /products/analytics/top-selling`
  - `GET /customers` (فلترة بالبحث والفئة بس)
  - `GET /branches`
- تاريخ إطلاق المنتج مش موجود، والأقرب ليه `Product.createdAt`.
- **مفيش حاجة تفرّق الأسرة عن المزرعة الصغيرة**، ولا نوع الحيوان ولا العدد. الحل في `schema-additions.prisma`.

## المرحلة 1 (من غير أي تطوير): ملفات أسبوعية
- مسؤول الـERP يطلّع كل أسبوع CSV بالأعمدة المكتوبة في `agents/gff-market-radar/data/tracking/README.md`.
- يرفعه في مخزن الذاكرة تحت `/tracking/inbox/`، وأقصى حجم للملف الواحد حوالي 100KB (لو أكبر يتقسم).
- ممكن تتعمل بسكريبت صغير بأمر `ant beta:memory-stores:memories create`.
- **الملف مايكونش فيه أسماء أو تليفونات أو أرقام قومية.**

## المرحلة 3: سيرفر MCP للقراية بس
سيرفر صغير (ممكن NestJS module جديد أو خدمة منفصلة) بيعرض أدوات قراية فوق قاعدة البيانات أو فوق read replica، وبيرجّع بيانات مجمّعة أو بكود العميل بس.

| الأداة | المدخلات | بترجّع |
|---|---|---|
| `list_new_products` | `since` (تاريخ) | المنتجات اللي `launchDate` أو `isPilot` بتاعها بعد التاريخ |
| `product_adoption` | `product_code`، `from`، `to` | مشترين جدد، تكرار الشراء، متوسط الفترة، التوزيع حسب الفرع والشريحة |
| `sales_lines` | `from`، `to`، `branch_code?`، `segment?`، `product_code?` | بنود الفواتير بكود العميل (بحد أقصى عدد سطور، ومعاه pagination) |
| `customer_segments_summary` | `branch_code?` | عدد العملاء حسب الشريحة والنوع والمرحلة |
| `customer_profile` | `customer_code` | الشريحة، القطيع، آخر 10 مشتريات، آخر 5 تفاعلات، **من غير بيانات اتصال أو مالية** |
| `complaints_and_returns` | `from`، `to`، `product_code?` | الأسباب والأعداد |
| `survey_responses` | `survey_id` | الإجابات بكود العميل |
| `market_prices` | `item_type`، `from`، `governorate?` | سجل الأسعار |
| `record_market_price` | بيانات السعر والمصدر | **كتابة** في `MarketPrice` (مصدر = AGENT) |
| `draft_survey` | العنوان والأسئلة والشريحة | **كتابة** استبيان بحالة DRAFT، والمشرف بيوافق في الـERP |
| `draft_field_task` | المندوب والتعليمات والموعد | **كتابة** مهمة ميدانية محتاجة موافقة المشرف |
| `upsert_product_idea` | بيانات الكارت | **كتابة** في `ProductIdea` لمراحل IDEA و DESK_RESEARCH بس |

الإعداد في `agent.md` (متعلّم عليه كتعليق دلوقتي):
```yaml
mcp_servers:
  - {type: url, name: gff-erp, url: https://erp.YOUR_DOMAIN/mcp}
tools:
  - type: mcp_toolset
    mcp_server_name: gff-erp
    default_config: {permission_policy: {type: auto}}
```
توكن السيرفر بيتحفظ في vault على منصة Claude (نوع `static_bearer` مربوط بعنوان السيرفر)، ومش بيتكتب في أي ملف.

## الصلاحيات
- يتعمل دور جديد في الـERP اسمه `AI_RESEARCH_AGENT` (مستخدم خدمة ليه JWT خاص بيه).
  - **قراية:** customers:view، sales:view، reports:view، وصلاحيات جديدة market:view و research:view و crm:view. ويستحسن تبقى من خلال view بتخفي الرقم الضريبي وحد الائتمان والرصيد.
  - **كتابة في جداوله بس:** market:write، research:write (أفكار في أول مرحلتين، استبيانات DRAFT، مقاييس)، fieldtask:create (محتاجة موافقة).
  - **ممنوع:** إنشاء أو تعديل فواتير، تعديل عملاء أو حدود ائتمان، تعديل أسعار البيع، المحاسبة، الموارد البشرية، المستخدمين، الإعدادات.
  - كل كتابة تتسجل في AuditLog باسم مستخدم الخدمة.
- يتعمل دور `MARKETING_MANAGER` للمشرف البشري. هو اللي بيوافق على الاستبيانات والمهام الميدانية والحملات وتغيير مراحل الأفكار. ولحد ما يتعين، المدير العام (ADMIN) هو اللي بيوافق، مش SUPER_ADMIN.
- ملحوظة: خدمة العملاء بتفلتر بالشركة بس، فأي حد عنده customers:view بيشوف عملاء كل الفروع. ده محتاج يتصلح بغض النظر عن رادار.

## واتساب - لازم يتصلح قبل أي تواصل مع المربين
راجعت `src/whatsapp/whatsapp.service.ts`:
1. **الردود الآلية بتاعة شركة تانية**: "Green Farm Foods"، "الزرقاء - الأردن"، أرقام 077XXXXXXX، وإيميلات greenfarmfoods.com. لازم تتغير قبل ما أي عميل يشوفها.
2. الـwebhook اللي بيستقبل الرسايل مفيهوش تحقق من التوقيع (X-Hub-Signature-256).
3. الرسايل متخزنة في الذاكرة المؤقتة وبتضيع لما السيرفر يعيد التشغيل، ومش مربوطة بالعميل (الحل: موديل `WhatsAppMessage`).
4. صلاحية الإرسال هي نفس صلاحية إنشاء فاتورة (`sales:create`). محتاجة صلاحية منفصلة `whatsapp:send`.
5. مفيش قوالب تسويق أو استبيان معتمدة من Meta. أي رسالة للعميل بعد 24 ساعة من آخر رسالة منه محتاجة قالب معتمد.

**قاعدة رادار:** مفيش رسالة لأي مربي إلا لو `contactConsent = true` ومفيش `optOutAt`، وبقالب معتمد، وبموافقة المشرف على الحملة.
