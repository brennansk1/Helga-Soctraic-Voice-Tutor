# Design Spec 09 — Billing & Subscriptions (Stripe)

> Implementation-ready spec for branch **B20** (`B20.1`–`B20.4`). Clean slate — there is **no**
> existing billing code in the repo. Canonical table/column names come from
> `docs/design/01_DATA_MODEL.md` §2 (`parents`, `students`, `enrollments`, `subscriptions`,
> `consent_records`). Seat enforcement at add-student is the join point with the parent dashboard
> (`B19.4`). Webhook/portal endpoints follow the existing Flask proxy + CSRF + session conventions in
> `services/web-ui/app.py`. Sub-store data access follows `services/common/storage.py`
> (`_ThreadLocalDB`, WAL, `_VALID_COLUMNS` whitelist, `INSERT OR REPLACE` upsert).
>
> **Customer context (load-bearing for every section).** Customers are **Utah homeschool families**
> funding tuition with the **Utah Fits All Scholarship** (~$8,000/student/year of state education
> funds). Funds frequently flow through a **state marketplace / reimbursement** rather than a parent's
> personal card. Therefore: (a) **invoices and itemized receipts are first-class**, not an
> afterthought; (b) we must support a **non-card / invoice payment path** alongside Stripe Checkout;
> (c) line items must carry student name + program/subject detail a reimbursement reviewer needs.

---

## 0. Scope, placement, and conventions

- **Service placement.** Billing lives in **`services/web-ui/app.py`** (it is parent-role,
  session-bound, browser-facing, and already owns Flask sessions + CSRF). All Stripe SDK calls,
  webhook receipt, and the entitlement gate live here. The `subscriptions` row is read/written through
  a new `SubscriptionStore` sub-store in `services/common/storage.py` (mirrors `ProgressStore`).
- **Stripe SDK.** `stripe` Python package, pinned (`stripe>=9,<11`). Add to
  `services/web-ui/requirements.txt`.
- **PCI posture.** **No card data ever touches our servers.** We use **Stripe-hosted Checkout** and
  the **Stripe Customer Portal**. Our PCI scope is **SAQ-A** (the lowest). This is the same
  data-minimization posture as the COPPA/FERPA work (spec 08 / B21) — we hold no PAN, no CVV.
- **Secrets.** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*` (price IDs),
  `STRIPE_PORTAL_CONFIG_ID`, `BILLING_PUBLIC_URL` come from environment (`.env`), read with
  `os.environ.get(...)` exactly like `FLASK_SECRET_KEY`/`SERVICES` today. Never commit keys; test keys
  (`sk_test_…`) for dev, live keys injected at deploy (B23.7 secrets management).
- **Auth.** Every billing route except the webhook is **parent-role only** (Flask-Login parent
  session, B15.4). The webhook is **unauthenticated by session** but **authenticated by Stripe
  signature** (§9).
- **Entitlement source of truth.** The **local `subscriptions` row** — never the client, never a
  Stripe API call on the hot path. The webhook keeps the mirror fresh; the gate reads the mirror.

---

## 1. Plan & pricing model (B20 overview)

### 1.1 Recommended structure: **per-family plan with a seat count**

`subscriptions` already models exactly this: one row **per parent** (`parent_id PRIMARY KEY`), with
`plan` (text) + `seats` (int = max active students). We adopt **per-family billing, seat-metered**,
not per-student subscriptions. Rationale:

- One Stripe Customer + one Subscription per family = one invoice, one portal, one webhook stream to
  reconcile. Far simpler than N subscriptions for a 4-kid homeschool family.
- Utah Fits All is **per-student** ($8k/student/yr), but a family typically reimburses **one receipt
  that itemizes each child** — a single itemized invoice with one line per seat maps cleanly to a
  reimbursement claim (§5).
- Seats map 1:1 to `subscriptions.seats`; the dashboard already enforces "active students ≤ seats"
  (B19.4 / §4).

### 1.2 Default plan catalog (recommendation — state your assumptions)

**Assumptions (revisit against spec 10 unit economics):** self-hosted inference on the Mac Mini M4
Pro means **marginal cost per active student is low** (electricity + amortized hardware, no per-token
cloud API), but it is **not zero** and is **GPU-concurrency-bound** (B23.1 fair queue). Pricing must
clear the **fully-loaded cost per student** that spec 10 (`docs/design/10_*`, Unit Economics) defines
— hardware amortization + power + storage + transactional email + support + Stripe fees
(2.9% + $0.30/charge). Until spec 10 lands, assume a fully-loaded cost floor of **~$8–15 / active
student / month** and price with comfortable margin **and** well under the $8k grant ceiling so a
family can fund multiple children from one student's scholarship.

| Plan key (`subscriptions.plan`) | Seats | Price | Stripe price ID env | Notes |
|---|---|---|---|---|
| `family_monthly` | 1 base | **$30 / mo** base + **$15 / mo** per additional seat | `STRIPE_PRICE_FAMILY_MONTHLY_BASE`, `STRIPE_PRICE_SEAT_MONTHLY` | Self-serve card. Metered by seat (Stripe quantity = `seats`). |
| `family_annual` | 1 base | **$300 / yr** base + **$150 / yr** per additional seat | `STRIPE_PRICE_FAMILY_ANNUAL_BASE`, `STRIPE_PRICE_SEAT_ANNUAL` | ~2 months free vs monthly; the **grant-friendly** SKU (annual term matches a school year / one reimbursement). |
| `grant_annual_invoice` | per claim | Invoiced amount, annual | n/a (manual invoice) | **Non-card path** for marketplace/reimbursement (§5). Same entitlement, activated by `invoice.paid` or manual ops activation. |

**Seat modeling in Stripe.** The base + per-seat structure is two subscription **items**: a base item
(quantity 1) and a per-seat item whose **`quantity` = `seats`**. When the family adds/removes seats we
update that item's quantity (proration on by default). The single `subscriptions.seats` mirror tracks
the per-seat item quantity (base seat + additional). **Decision:** keep it simple in v1 — one price per
plan whose **quantity = total seats** (no separate base/add-on items), so `seats` maps directly to
Stripe `quantity`. The base/add-on split above is a pricing presentation; bill it as
`quantity × per-seat price` with the first seat priced into the per-seat rate. Confirm final
list price with spec 10.

### 1.3 What a "seat" entitles

One seat = one `students.status='active'` row under the parent. Archived students
(`students.status='archived'`) do **not** consume a seat. This is the rule §4 enforces.

---

## 2. Stripe integration (B20.1)

### 2.1 Object model and mapping to the local mirror

| Stripe object | Created when | Mirrored into `subscriptions` column | Notes |
|---|---|---|---|
| **Customer** | First checkout (or first portal/invoice) for a parent | `provider_customer_id` | One per `parent_id`. Stash `parent_id` in Customer `metadata.parent_id` and set `email` for receipts. |
| **Checkout Session** (mode=`subscription`) | `POST /api/billing/create-checkout` | — (transient) | Carries `client_reference_id = parent_id` + `metadata.parent_id`, `success_url`, `cancel_url`. |
| **Subscription** | On checkout completion | `provider_sub_id`, `status`, `current_period_end`, `seats` (from item quantity), `plan` (from price→plan map) | The durable billing object. |
| **Price / Product** | Configured once in Stripe dashboard | `plan` (via env price-ID → plan-key map) | We store the **plan key**, not the price ID, in the mirror. |
| **Invoice** | Each billing cycle / on demand | — (fetched live for §5 receipts) | Source for grant receipts; hosted invoice URL + PDF link surfaced to parent. |
| **Billing Portal Session** | `POST /api/billing/portal-link` | — (transient) | Self-serve plan change / seat change / cancel / card update / invoice history. |

`subscriptions` columns being written (from spec 01 §2): `provider` (`'stripe'`),
`provider_customer_id`, `provider_sub_id`, `plan`, `seats`, `status`
(`active|trialing|past_due|canceled|inactive`), `current_period_end`, `updated_at`. `parent_id` is the
PK.

### 2.2 Checkout session creation (server-side)

```python
# services/web-ui/app.py  (sketch — real impl uses BillingService wrapper)
import stripe
stripe.api_key = os.environ['STRIPE_SECRET_KEY']

PLAN_PRICE = {                              # plan key -> Stripe price id (env-injected)
    'family_monthly': os.environ.get('STRIPE_PRICE_FAMILY_MONTHLY'),
    'family_annual':  os.environ.get('STRIPE_PRICE_FAMILY_ANNUAL'),
}
PRICE_PLAN = {v: k for k, v in PLAN_PRICE.items() if v}   # reverse map for webhook

def create_checkout(parent_id, parent_email, plan, seats):
    customer_id = storage.subscriptions.get_or_create_customer(parent_id, parent_email)
    sess = stripe.checkout.Session.create(
        mode='subscription',
        customer=customer_id,
        client_reference_id=parent_id,
        line_items=[{'price': PLAN_PRICE[plan], 'quantity': max(1, seats)}],
        subscription_data={
            'trial_period_days': TRIAL_DAYS,            # §7
            'metadata': {'parent_id': parent_id},
        },
        metadata={'parent_id': parent_id, 'plan': plan},
        allow_promotion_codes=True,
        success_url=f"{BILLING_PUBLIC_URL}/billing/return?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BILLING_PUBLIC_URL}/courses?checkout=cancelled",
    )
    return sess.url            # 303 redirect target for the browser
```

The browser is redirected to `sess.url` (Stripe-hosted). We **do not** activate on the redirect
return; activation is **webhook-driven** (§3) so a closed browser tab never leaves a paid-but-inactive
family. The `success_url` page shows "Finishing up…" and polls `GET /api/billing/subscription-status`
until `status ∈ {active, trialing}`.

### 2.3 Customer portal (self-serve)

```python
def portal_link(parent_id):
    customer_id = storage.subscriptions.require_customer(parent_id)   # 409 if none
    sess = stripe.billing_portal.Session.create(
        customer=customer_id,
        configuration=os.environ.get('STRIPE_PORTAL_CONFIG_ID') or None,
        return_url=f"{BILLING_PUBLIC_URL}/courses",
    )
    return sess.url
```

Portal config (set once in Stripe dashboard) allows: update payment method, change plan, change seat
quantity, view/download invoices, cancel. **Seat/plan changes made in the portal flow back to us via
`customer.subscription.updated`** (§3) — we never re-derive seats from the client.

---

## 3. Webhooks (B20.2)

### 3.1 Receiver

`POST /api/billing/webhook` — **no CSRF** (Stripe is not a browser), **no session auth**, **signature
verified** (§9). Returns `200` fast; all heavy work is an idempotent mirror update + side effects.

```python
@app.route('/api/billing/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()                      # raw bytes — required for signature
    sig = request.headers.get('Stripe-Signature', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig, os.environ['STRIPE_WEBHOOK_SECRET'])
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({'error': 'bad signature'}), 400
    if storage.subscriptions.event_seen(event['id']):   # idempotency (§3.3)
        return ('', 200)
    try:
        handle_billing_event(event)                     # table below
        storage.subscriptions.mark_event(event['id'], event['type'])
    except Exception:
        logger.exception("billing webhook handler failed: %s", event['type'])
        return ('', 500)                                # Stripe retries with backoff
    return ('', 200)
```

### 3.2 Handler table — what each event does to the mirror + side effects

| Stripe event | Mirror update (`subscriptions` by `parent_id`) | Side effects on students | Why |
|---|---|---|---|
| `checkout.session.completed` | Resolve `parent_id` from `client_reference_id`/`metadata`. Set `provider_customer_id`, `provider_sub_id` (`session.subscription`), `plan` (from `metadata.plan`/price map), `seats` (item quantity), `status` (`trialing` if trial else `active`), `current_period_end`. | **Activate** entitlement; no student status change (parent then adds students within seats). | Family just paid/started trial. Primary activation event. |
| `customer.subscription.updated` | Set `status`, `current_period_end`, `seats` (item quantity), `plan` (from price). | If `seats` **decreased** below active-student count → **archive** over-limit students (§4.2). If `status` moved `past_due`→`active` → clear grace flag. | Plan/seat change, renewal, card recovered, trial→active. Authoritative for seats. |
| `customer.subscription.deleted` | `status='canceled'`, keep `current_period_end` (access until period end if Stripe cancels at period end; if immediate, gate blocks now). | On hard cancel: students remain `active` rows but **gated to blocked** by §6 — not deleted/archived (data retained per spec 08 retention). | Cancellation / non-payment terminal. |
| `invoice.paid` | `status='active'`, refresh `current_period_end`; for `grant_annual_invoice` this is the **activation** trigger for the manual/invoice path (§5). | Clear any grace flag; re-activate if previously suspended for non-payment. | Successful charge or grant marketplace payment cleared. |
| `invoice.payment_failed` | `status='past_due'`. | Enter **grace** (§6): students stay readable until grace expiry, then restricted. Send dunning notification (B24.1). | Card declined / payment not received. |
| `customer.subscription.trial_will_end` | (optional) no status change. | — | Fire a "trial ending" notification (B24.1) + nudge to add a payment method. |

Unhandled event types: log at `debug`, return `200` (Stripe sends many we don't care about).

`current_period_end` arrives as a Unix epoch int from Stripe; store as **ISO-8601 UTC TEXT** to match
the data-model convention (`datetime.utcfromtimestamp(ts).isoformat()`).

### 3.3 Idempotency

Stripe delivers at-least-once and retries on non-2xx. Add a tiny ledger table (migration block, §8.2):

```sql
CREATE TABLE IF NOT EXISTS billing_events (
    event_id   TEXT PRIMARY KEY,        -- Stripe evt_...
    type       TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

`event_seen()` = `SELECT 1 FROM billing_events WHERE event_id=?`; `mark_event()` =
`INSERT OR IGNORE`. A duplicate delivery is a no-op `200`. Additionally, all mirror writes are
**INSERT OR REPLACE upserts keyed on `parent_id`** so reprocessing the same state is harmless; we also
**ignore stale events** by comparing the subscription object's `created`/period against the stored row
where ordering matters.

---

## 4. Seat enforcement (B20.3)

### 4.1 The rule

> **active students ≤ `subscriptions.seats`**, where "active" = `students.status = 'active'` under the
> parent. Archived/deleted students do not count.

Enforced in **two places**:

**(a) Add-student (parent dashboard B19.4).** Before inserting a new `students` row (or reactivating
an archived one), the add-student handler calls:

```python
def can_add_student(parent_id) -> tuple[bool, str]:
    sub = storage.subscriptions.get(parent_id)
    if not sub or sub['status'] not in ('active', 'trialing'):
        return False, 'no_active_subscription'
    active = storage.students.count_active(parent_id)
    if active >= sub['seats']:
        return False, 'seat_limit'        # UI: "Add a seat to enroll another student"
    return True, ''
```

On `seat_limit`, the dashboard offers a deep link to the **portal** (or a checkout quantity bump) to
buy a seat; on success the `customer.subscription.updated` webhook raises `seats` and the add
succeeds. **Never** trust a client-supplied seat count — re-read `subscriptions.seats` server-side.

**(b) On downgrade (seat decrease).** Handled in the `customer.subscription.updated` side effect (§3.2).

### 4.2 Downgrade / over-limit policy — **archive, never delete**

When a webhook lowers `seats` below the current active count:

1. Compute overflow = `active_count − new_seats`.
2. Select the **most-recently-created** active students (LIFO — keep the longest-tenured learners) up
   to `overflow` and set `students.status = 'archived'`. **Do not delete.** All progress, FSM
   sessions, flashcards, and exam history are retained (spec 08 retention / FERPA).
3. Archived students: hidden from the active roster, excluded from billing seat count, **blocked from
   sessions** (gate §6 treats archived as no-access). Their data is restorable: if the family later
   buys a seat back, the parent can **un-archive** (status → `active`) up to the new seat limit.
4. Emit a parent notification (B24.1): "Reducing to N seats archived <names>; their progress is saved
   and restores when you add a seat."

**Tie-break / fairness note:** v1 uses recency (LIFO). A future improvement is to let the parent
**choose** which students to archive at downgrade time (portal can't do this, so we'd intercept the
seat-decrease in our own UI before calling Stripe). Open question in §10.

### 4.3 Grace / `past_due` handling vs seat enforcement

`past_due` does **not** archive students. It triggers the access gate's **grace window** (§6). Seats
are only re-shaped on an explicit seat **decrease** (subscription.updated) or terminal cancel.

---

## 5. Grant / invoice flow (B20.4)

Utah Fits All reimbursement reviewers need an itemized expense document tying the charge to **a
student** and **an educational program**. Two payment realities:

### 5.1 Card path (self-serve) — receipt generation

Stripe already produces a hosted invoice + PDF per charge. We **augment** it for grant use:

- Set Stripe Customer + Subscription **metadata**: `parent_id`, `students=<comma stu_ ids>`,
  `program="Helga K-12 Socratic Tutoring"`, `term`.
- Expose a **grant receipt** endpoint, `GET /api/billing/receipt/<invoice_id>` (parent-only, ownership
  checked via `provider_customer_id`), that returns a **grant-formatted PDF/HTML** (not just Stripe's
  default) containing the line items below. Reuse the same PDF tooling as the progress-report export
  (B19.5).

### 5.2 Non-card / marketplace path — invoice path

When the **Utah Fits All marketplace pays** (ACH / check / portal credit) rather than a parent card:

1. Parent (or ops) selects **"Pay by grant / invoice"** at signup → plan `grant_annual_invoice`.
2. We create a Stripe **Invoice** with `collection_method='send_invoice'`, `days_until_due`,
   itemized line items, and a **PO/invoice number** in metadata. Stripe emails a payable invoice with a
   hosted page + PDF the family submits to the marketplace.
3. Entitlement activates on **`invoice.paid`** (when the marketplace remits) — same webhook path
   (§3.2). For grant timing gaps, **ops can manually activate** by setting
   `subscriptions.status='active'` + `current_period_end` (a guarded admin action, audited in
   `audit_log`) so a family isn't blocked while reimbursement clears.
4. Alternatively, for fully out-of-band payments we support a `manual` provider value — but prefer
   routing through Stripe Invoices so receipts and reconciliation stay in one system.

### 5.3 Invoice / receipt contents (required line items & fields)

Every grant receipt/invoice MUST include:

- **Provider:** legal entity name, address, EIN/tax ID, Utah Fits All **provider/vendor ID** (when
  enrolled), contact email.
- **Payer/family:** parent name, email, billing address.
- **Per-student line item(s):** one line **per seat/student** — `student display_name`,
  `grade_band`, **program/subject** ("Helga K-12 Socratic Tutoring — <term>"), unit price, quantity,
  amount. (Student names come from `students`; never expose other families' data.)
- **Term/dates:** service period (`current_period_start`..`current_period_end`), invoice date,
  due date.
- **Numbers:** **invoice number**, **PO number** (if the marketplace issued one),
  Stripe invoice/charge ID for audit.
- **Totals:** subtotal, any discount/promo, tax (likely exempt for education — confirm), total,
  amount paid, payment method/date.
- **Statement of service:** one-line description suitable for reimbursement ("Online standards-aligned
  tutoring subscription, <N> students, <term>").

PO/invoice number storage: add `po_number` + `last_invoice_id` to `subscriptions` (or a small
`billing_invoices` ledger if we need history — §8.2 makes the ledger optional-but-recommended).

---

## 6. Access control coupling (entitlement gate)

`subscriptions.status` gates the product. Define **one** gate used everywhere a student would consume
compute or content.

### 6.1 Status → entitlement mapping

| `subscriptions.status` | Within grace window? | Student access |
|---|---|---|
| `trialing` | — | **Full** (all features; show "trial ends <date>"). |
| `active` | — | **Full**. |
| `past_due` | **yes** (≤ `GRACE_DAYS` after period end, default **7**) | **Full but warned** — dunning banner to parent; student unaffected during grace. |
| `past_due` | **no** (grace expired) | **Restricted**: existing progress/reports **read-only**; **no new tutoring sessions / no exams** (these consume GPU). |
| `canceled` | before `current_period_end` | **Full** until period end (paid through). |
| `canceled` | after period end | **Blocked**: read-only dashboard, no sessions; data retained. |
| `inactive` (never subscribed / lapsed) | — | **Blocked**: onboarding/checkout only. |
| (student `status='archived'`) | — | **Blocked** regardless of subscription (seat downgrade, §4.2). |

"Restricted/read-only" = parent can still view progress and **export** their data (FERPA right; spec
08) and pay to reactivate; the **student** cannot start a Socratic session, exam, or review that costs
inference. This deliberately preserves the data-export/right-to-access guarantees even when blocked.

### 6.2 The gate check (single definition)

```python
def entitlement(parent_id) -> dict:
    """Returns {'access': 'full'|'restricted'|'blocked', 'reason': str, 'sub': row}."""
    sub = storage.subscriptions.get(parent_id)
    now = datetime.utcnow()
    if not sub: return {'access': 'blocked', 'reason': 'no_subscription', 'sub': None}
    st, cpe = sub['status'], _parse_iso(sub.get('current_period_end'))
    if st in ('active', 'trialing'):
        return {'access': 'full', 'reason': st, 'sub': sub}
    if st == 'canceled' and cpe and now < cpe:
        return {'access': 'full', 'reason': 'paid_through', 'sub': sub}
    if st == 'past_due':
        grace_end = (cpe or now) + timedelta(days=GRACE_DAYS)
        return ({'access': 'full', 'reason': 'grace', 'sub': sub} if now < grace_end
                else {'access': 'restricted', 'reason': 'past_due_grace_expired', 'sub': sub})
    return {'access': 'blocked', 'reason': st, 'sub': sub}

def require_active_session(parent_id):                 # decorator/guard for compute endpoints
    e = entitlement(parent_id)
    if e['access'] != 'full':
        abort(402, e['reason'])                        # 402 Payment Required
```

**Where it's enforced (server-side only):**
- Web-UI gates the **session-launch** path: `/api/event` `TEXT_INPUT`, exam start, review start, any
  route that forwards to core/RAG and burns GPU → `require_active_session` (else `402`).
- Add-student is additionally gated by `can_add_student` (§4.1).
- The **archived-student** check is layered on top (student-level), since a family can have access at
  the family level but an individual archived student is blocked.
- The browser may *read* status (to render banners) but **entitlement is never decided client-side**.

`GRACE_DAYS` env-configurable (default 7).

---

## 7. Free trial / onboarding-to-paid funnel

- **Trial type:** **card-less trial preferred** for the homeschool/grant audience (lowers signup
  friction; grant families often don't want to put a personal card down). Default **14-day trial**
  (`TRIAL_DAYS=14`) created via either:
  - Checkout with `subscription_data.trial_period_days` (card collected up front, charged at trial
    end), **or**
  - **No-card trial:** set `subscriptions.status='trialing'` + `current_period_end = now+14d` locally
    at parent signup, *without* a Stripe subscription yet; convert to paid by sending them through
    Checkout before trial end. (Recommended default — confirm with growth/spec 10.)
- **Funnel states:** `inactive` (signed up, no trial chosen) → `trialing` → on Checkout completion
  `active`/`trialing(stripe)` → renewals keep `active`. Trial-ending notification (B24.1) at T-3 days
  and T-1 day; `trial_will_end` webhook backs the Stripe-side trials.
- **Seat default on trial:** start at **1 seat**; adding more students during trial bumps the seat
  count they'll be billed for at conversion (preview the price).
- **Consent ordering:** COPPA/TOS consent (spec 08, `consent_records`) is captured **before** any
  student is created — independent of billing, but the onboarding wizard sequences:
  signup → consent → choose plan/trial → add student(s) within seats → first session.

---

## 8. Endpoints & storage

### 8.1 HTTP endpoints (all in `services/web-ui/app.py`)

| Method & path | Auth | Request | Response | Notes |
|---|---|---|---|---|
| `POST /api/billing/create-checkout` | parent + CSRF | `{plan, seats}` | `{url}` (Stripe Checkout URL) | 303-redirect target. Validates `plan` against `PLAN_PRICE`; `seats≥1`. |
| `POST /api/billing/portal-link` | parent + CSRF | `{}` | `{url}` (portal URL) | `409` if no `provider_customer_id`. |
| `POST /api/billing/webhook` | **Stripe signature** (no session/CSRF) | raw Stripe event | `200`/`400`/`500` | §3. Raw body required for signature. |
| `GET  /api/billing/subscription-status` | parent | — | `{status, plan, seats, active_students, current_period_end, access, reason}` | Drives banners + success-page polling. **Reads the mirror**, no live Stripe call. |
| `GET  /api/billing/receipt/<invoice_id>` | parent (ownership-checked) | — | PDF/HTML grant receipt | §5; ownership via `provider_customer_id`. |
| `POST /api/billing/invoice` *(grant path)* | parent + CSRF | `{plan, seats, po_number?}` | `{hosted_invoice_url, invoice_id}` | Creates `send_invoice` invoice (§5.2). |

Webhook route must be added to the CSRF **exempt** set and must read `request.get_data()` (not
`request.json`, which would consume/alter the body needed for signature verification). Follow the
existing proxy/timeouts/`try/except` + `logger` patterns in `app.py`.

### 8.2 `SubscriptionStore` (new sub-store in `storage.py`)

Mirrors `ProgressStore`: `_ThreadLocalDB`, `_VALID_COLUMNS` whitelist, `INSERT OR REPLACE` upsert
keyed on `parent_id`. Add to the migration system (extends `subscriptions` from spec 01 §2 v4; the
billing-specific columns + ledger ship as a new `schema_version` block, following the existing
`if current_version < N:` idiom at `storage.py:181-203`).

```python
class SubscriptionStore:
    _VALID_COLUMNS = {
        'provider', 'provider_customer_id', 'provider_sub_id', 'plan', 'seats',
        'status', 'current_period_end', 'po_number', 'last_invoice_id', 'updated_at',
    }
    def get(self, parent_id) -> Optional[dict]: ...
    def upsert(self, parent_id, **kwargs):           # whitelist-filter, set updated_at, INSERT OR REPLACE
        ...
    def get_or_create_customer(self, parent_id, email) -> str: ...   # creates Stripe Customer if absent, stores id
    def require_customer(self, parent_id) -> str: ...                # raises if none
    def event_seen(self, event_id) -> bool: ...      # billing_events
    def mark_event(self, event_id, type_): ...
```

Migration block (new version, e.g. v9) adds `po_number TEXT`, `last_invoice_id TEXT` to
`subscriptions`, creates `billing_events`, and (optional-recommended) a `billing_invoices` ledger:

```sql
ALTER TABLE subscriptions ADD COLUMN po_number TEXT;
ALTER TABLE subscriptions ADD COLUMN last_invoice_id TEXT;
CREATE TABLE IF NOT EXISTS billing_events (event_id TEXT PRIMARY KEY, type TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS billing_invoices (         -- optional: receipt history
    id TEXT PRIMARY KEY,                              -- Stripe invoice id
    parent_id TEXT NOT NULL, amount_total INTEGER, currency TEXT,
    status TEXT, po_number TEXT, period_start TEXT, period_end TEXT,
    hosted_url TEXT, pdf_url TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_billing_inv_parent ON billing_invoices(parent_id, created_at);
```

`ALTER TABLE … ADD COLUMN` wrapped in try/except (column-exists is non-fatal), matching the existing
migration style. Add `'student_id'`/new columns to whitelists as the data-model spec requires for
touched stores.

---

## 9. Security

1. **Webhook signature verification (mandatory).** `stripe.Webhook.construct_event(raw_body, sig,
   STRIPE_WEBHOOK_SECRET)`. Reject `400` on `ValueError`/`SignatureVerificationError`. The webhook is
   the **only** unauthenticated billing route — the signature *is* its auth. Raw body bytes required;
   do not parse JSON before verifying.
2. **Never trust the client for entitlement.** All gate decisions (§6), seat counts (§4), and plan/seat
   values come from the **server-side mirror**, refreshed only by signed webhooks. Client can buy/change
   only via Stripe-hosted surfaces; we re-read state from webhooks, never from POST bodies.
3. **Minimized PCI scope (SAQ-A).** Stripe-hosted Checkout + Portal mean **no card data on our
   servers** — no PAN/CVV ever transits or is stored by Helga. This is the same minimization stance as
   the COPPA/FERPA data posture (spec 08 / B21): we hold the least sensitive data possible. Document
   SAQ-A in the compliance posture (B21.6).
4. **Secret management.** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, price IDs, portal config ID in
   env only (`.env` dev, injected secrets in prod, B23.7). Test vs live key separation. Rotate webhook
   secret via Stripe dashboard; never log secrets or full event payloads at info level.
5. **CSRF.** All browser-facing billing POSTs use the existing `@csrf_protect` decorator
   (`app.py:102`). The webhook is **exempt** (Stripe can't send our token) and instead signature-gated.
6. **Authorization / ownership.** Every parent route resolves `parent_id` from the **session**, never a
   request param. Receipt/invoice access checks the invoice's customer == the session parent's
   `provider_customer_id`. Stash `parent_id` in Stripe Customer/Subscription metadata and verify it on
   webhook so a spoofed/foreign event can't mutate the wrong family.
7. **No minor data to Stripe.** Send only what a receipt needs (parent email, student **display
   name**, grade band). No DOB, no school records, no health-strand data to Stripe (spec 08).
8. **Idempotency / replay.** `billing_events` ledger (§3.3) prevents double side-effects on Stripe
   retries/replays.

---

## 10. Test plan / acceptance criteria

### Acceptance criteria (map to B20.1–B20.4)

- **B20.1** Test-mode Checkout (`sk_test_…`) completes and redirects to `success_url`; portal link
  opens for a customer and 409s without one.
- **B20.2** `stripe trigger checkout.session.completed` (Stripe CLI) → mirror row shows
  `status=active|trialing`, `provider_sub_id`, `seats`, `plan`, `current_period_end` populated.
  Duplicate delivery of the same `evt_` is a no-op (idempotency).
- **B20.3** With `seats=2` and 2 active students, **add-student returns seat_limit (402/blocked in
  UI)**. A `customer.subscription.updated` lowering seats to 1 **archives** (not deletes) the
  newest-active student; that student's progress is intact and restorable; archived student is gated
  out.
- **B20.4** Grant invoice (`send_invoice`) is generated with required line items (§5.3) incl.
  per-student line, term, invoice/PO number; `invoice.paid` activates entitlement; receipt endpoint
  returns a grant-formatted document.

### Test matrix

| Test | Setup | Assert |
|---|---|---|
| Checkout → activate | Stripe test card `4242…`, `create-checkout` | webhook sets mirror `active`; gate returns `full` |
| Trial conversion | trial sub, advance clock (test clock) past trial | `customer.subscription.updated`→`active`; first invoice paid |
| Seat enforcement (add) | `seats=1`, 1 active student | second add-student → `seat_limit` (402); not inserted |
| Seat downgrade archive | `seats=2`→`1`, 2 active | newest student `status='archived'`; older still `active`; data retained |
| Un-archive on seat buy | from above, seats→2 | parent can reactivate archived student; gate full |
| Past_due grace | `invoice.payment_failed` | `status='past_due'`; within `GRACE_DAYS` access `full`; after, `restricted` (no new sessions, reports read-only) |
| Cancel paid-through | `customer.subscription.deleted` at period end | access `full` until `current_period_end`, `blocked` after |
| Webhook signature | POST with bad/absent `Stripe-Signature` | `400`; no mirror change |
| Idempotency | deliver same `evt_` twice | single side-effect; second returns `200` no-op |
| Entitlement never client-trusted | POST forged `{status:'active'}` to status endpoint | ignored; mirror unchanged |
| Receipt ownership | parent A requests parent B's invoice | `403/404` |
| Invoice line items | grant invoice for 3 students | 3 student lines + program + term + PO/invoice number present |

Use Stripe **test mode + Stripe CLI** (`stripe listen --forward-to localhost:5050/api/billing/webhook`,
`stripe trigger …`) and **test clocks** for trial/renewal time travel. Unit-test `SubscriptionStore`
and `entitlement()` with fixtures (no network); integration-test webhook handlers with recorded event
JSON.

### Open questions (confirm before build)

1. **Exact Utah Fits All payment mechanics** — does the marketplace pay the provider directly (ACH to
   us) or reimburse the family after they pay a card? This decides whether the **invoice path** (§5.2)
   or the **card-path receipt** (§5.1) is primary. **Action: confirm with Utah Fits All / the
   marketplace operator (ACE / current administrator).**
2. **Provider enrollment / eligibility** — must we be an approved Utah Fits All vendor (vendor ID) to
   appear on receipts? Ties B21.6.
3. **Tax** — is the tutoring subscription education-tax-exempt in Utah? Affects invoice tax line.
4. **Final list price** — pending spec 10 unit economics (fully-loaded cost/student must clear margin
   and sit well under the $8k grant ceiling).
5. **Downgrade selection** — v1 archives newest-active (LIFO); do we let the parent choose which
   student to archive at downgrade (requires intercepting seat-decrease in our UI, not the portal)?
6. **Per-family vs per-student SKU** — confirm families won't need separate invoices per child for the
   marketplace (some reimbursement systems want one claim per student); if so, the invoice generator
   must emit **one invoice per student**, not one itemized invoice.
7. **No-card vs card-required trial** — confirm default (§7) with growth/compliance.
