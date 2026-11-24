"""AI Advisor APIs — smart input parsing + financial advisor chat.

Replaces the Express /api/ai/* server with proper Frappe endpoints,
using OpenAI or Gemini keys stored in Hisabi AI Settings (Single doctype).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cint, add_days, getdate

from hisabi_backend.utils.security import require_device_token_auth
from hisabi_backend.utils.wallet_acl import require_wallet_member
from hisabi_backend.utils.validators import validate_client_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_ai_settings() -> Dict[str, Any]:
    """Read Hisabi AI Settings Single doctype. Returns safe defaults if not configured."""
    try:
        s = frappe.get_single("Hisabi AI Settings")

        # Normalise Gemini base URL — Frappe Desk strips /v1beta if user saves the raw domain
        gemini_base = s.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta"
        if "generativelanguage.googleapis.com" in gemini_base and "/v1" not in gemini_base:
            gemini_base = gemini_base.rstrip("/") + "/v1beta"

        # Password fields are encrypted: reading the raw attribute (s.openai_api_key) returns a
        # masked '****' string, not the secret. get_password() decrypts the real value.
        openai_key = s.get_password("openai_api_key", raise_exception=False) or ""
        gemini_key = s.get_password("gemini_api_key", raise_exception=False) or ""

        return {
            "enable_ai": bool(s.enable_ai),
            "preferred_provider": s.preferred_provider or "openai",
            "openai_api_key": openai_key,
            "openai_base_url": s.openai_base_url or "https://api.openai.com/v1",
            "openai_text_model": s.openai_text_model or "gpt-4o-mini",
            "openai_audio_model": s.openai_audio_model or "whisper-1",
            "gemini_api_key": gemini_key,
            "gemini_base_url": gemini_base,
            "gemini_text_model": s.gemini_text_model or "gemini-2.5-flash-lite",
        }
    except Exception:
        return {
            "enable_ai": False,
            "preferred_provider": "openai",
            "openai_api_key": "",
            "gemini_api_key": "",
            "openai_base_url": "https://api.openai.com/v1",
            "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
        }


def _call_ai(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int = 2000) -> str:
    """Smart AI dispatcher — calls both providers in parallel when both keys are configured."""
    has_openai = bool(settings.get("openai_api_key"))
    has_gemini = bool(settings.get("gemini_api_key"))

    if has_openai and has_gemini:
        return _call_ai_parallel(messages, settings, max_tokens)
    elif has_openai:
        return _call_openai(messages, settings, max_tokens)
    elif has_gemini:
        return _call_gemini(messages, settings, max_tokens)
    else:
        frappe.throw(
            _("لم يُهيَّأ أي مزود AI. أضف مفاتيح API من Frappe Desk › Hisabi AI Settings."),
            frappe.ValidationError,
        )


def _call_openai(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int) -> str:
    return _openai_completion(messages, settings, max_tokens, temperature=0.3)


def _openai_completion(
    messages: List[Dict[str, Any]], settings: Dict[str, Any], max_tokens: int, *, temperature: float
) -> str:
    """Call OpenAI Chat Completions API.

    Handles both classic models (gpt-4o, gpt-4o-mini …) that use max_tokens + temperature,
    and reasoning models (gpt-5, gpt-6, o1, o3, o4 …) that require max_completion_tokens,
    developer role instead of system, and do not accept temperature.
    """
    import requests

    model = settings.get("openai_text_model") or "gpt-4o-mini"
    base_url = (settings.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings['openai_api_key']}",
        "Content-Type": "application/json",
    }

    # Reasoning / new-format models use max_completion_tokens + developer role
    is_reasoning = model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))

    adapted: List[Dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "system" and is_reasoning:
            adapted.append({"role": "developer", "content": msg["content"]})
        else:
            adapted.append(msg)

    payload: Dict[str, Any] = {"model": model, "messages": adapted}
    if is_reasoning:
        # Give reasoning models extra headroom for internal thinking tokens
        payload["max_completion_tokens"] = max(max_tokens * 3, 5000)
        # "low" effort keeps latency in the ~8-12s range instead of 30-40s, which would
        # otherwise blow past the parallel-dispatch timeout and drop a valid answer.
        payload["reasoning_effort"] = "low"
    else:
        payload["max_tokens"] = max_tokens
        payload["temperature"] = temperature

    response = requests.post(url, headers=headers, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _call_gemini(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int) -> str:
    """Call Google Gemini API."""
    import requests

    model = settings.get("gemini_text_model", "gemini-1.5-flash")
    url = f"{settings['gemini_base_url'].rstrip('/')}/models/{model}:generateContent"
    # Send the key as a header, NOT a query param — a request exception's str() includes the
    # full URL, which would leak the API key into any error surfaced to the client/logs.
    headers = {"x-goog-api-key": settings["gemini_api_key"]}

    contents = []
    system_instruction = None
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system_instruction = msg["content"]
            continue
        contents.append({
            "role": "user" if role == "user" else "model",
            "parts": [{"text": msg["content"]}],
        })

    body: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    # Gemini Flash intermittently returns 503 "high demand" / 429 — retry transient failures once.
    last_exc = None
    for attempt in range(2):
        response = requests.post(url, headers=headers, json=body, timeout=90)
        if response.status_code in (429, 503) and attempt == 0:
            last_exc = requests.HTTPError(f"{response.status_code} transient", response=response)
            import time as _t
            _t.sleep(1.5)
            continue
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise last_exc


def _call_ai_parallel(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int) -> str:
    """Call OpenAI and Gemini simultaneously; return the better response."""
    import concurrent.futures

    results: Dict[str, Optional[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_map = {
            "openai": pool.submit(_call_openai, messages, settings, max_tokens),
            "gemini": pool.submit(_call_gemini, messages, settings, max_tokens),
        }
        # 90s stays within the 120s gunicorn/nginx budget while never discarding a
        # slow-but-valid reasoning-model answer (which the old 30s cap did).
        for provider_name, fut in future_map.items():
            try:
                results[provider_name] = fut.result(timeout=90)
            except Exception as e:
                results[provider_name] = None
                frappe.log_error(
                    f"ai_advisor: {provider_name} call failed",
                    f"{type(e).__name__}: {str(e)[:500]}",
                )

    return _select_best_response(results)


def _select_best_response(results: Dict[str, Optional[str]]) -> str:
    """Pick the higher-quality response. Prefer Gemini when quality is comparable (cheaper)."""
    openai_r = results.get("openai")
    gemini_r = results.get("gemini")

    if not openai_r and not gemini_r:
        frappe.throw(
            _("فشل كلا مزودَي الذكاء الاصطناعي. تحقق من مفاتيح API في Frappe Desk."),
            frappe.ValidationError,
        )
    if not openai_r:
        return gemini_r
    if not gemini_r:
        return openai_r

    def _score(text: str) -> int:
        # Reward a COMPLETE, well-structured, Arabic answer — and PENALIZE verbosity. A concise
        # practical reply must beat a padded one (raw length must never be a reward, or the model
        # is incentivised to ramble).
        if not text:
            return -10_000
        n = len(text)
        is_arabic = 200 if sum(1 for c in text[:400] if "؀" <= c <= "ۿ") > 20 else 0
        structured = 200 if ("**" in text and ("•" in text or "1." in text)) else 0
        action = 300 if ("ACTION::" in text or "```json" in text) else 0
        # Complete up to ~600 chars; beyond ~1100 chars each extra ~3 chars costs a point.
        length_fit = min(n, 600) - max(0, n - 1100) // 3
        return int(length_fit + is_arabic + structured + action)

    g_score = _score(gemini_r)
    o_score = _score(openai_r)
    # Prefer Gemini when its score is within 80% of OpenAI's (cheaper provider)
    return gemini_r if g_score >= o_score * 0.8 else openai_r


def _get_wallet_financial_context(wallet_id: str, user: str) -> Dict[str, Any]:
    """Build a financial context summary for a wallet (used in AI prompts)."""
    today = getdate()
    thirty_days_ago = add_days(today, -30)

    # Hisabi Wallet has no base_currency field — derive from the first account of this wallet
    base_currency = (
        frappe.db.get_value(
            "Hisabi Account",
            {"wallet_id": wallet_id, "is_deleted": 0},
            "currency",
            order_by="creation asc",
        )
        or "SAR"
    )

    # amount_base is SIGNED (negative expense / positive income). Income & expense totals are
    # POSITIVE magnitudes, so aggregate ABS — otherwise expense_30d is negative and net_30d /
    # savings_rate below invert (net becomes income + |expense|, savings_rate exceeds 100%).
    tx_summary = frappe.db.sql("""
        SELECT transaction_type,
               SUM(ABS(COALESCE(amount_base, amount))) as total
        FROM `tabHisabi Transaction`
        WHERE wallet_id = %(wallet_id)s
          AND is_deleted = 0
          AND date_time >= %(from_date)s
          AND transaction_type IN ('income', 'expense')
        GROUP BY transaction_type
    """, {"wallet_id": wallet_id, "from_date": thirty_days_ago}, as_dict=True)

    income_30d = next((r.total for r in tx_summary if r.transaction_type == "income"), 0)
    expense_30d = next((r.total for r in tx_summary if r.transaction_type == "expense"), 0)

    top_cats = frappe.db.sql("""
        SELECT category, SUM(ABS(COALESCE(amount_base, amount))) as total
        FROM `tabHisabi Transaction`
        WHERE wallet_id = %(wallet_id)s
          AND is_deleted = 0
          AND transaction_type = 'expense'
          AND date_time >= %(from_date)s
          AND category IS NOT NULL AND category != ''
        GROUP BY category
        ORDER BY total DESC
        LIMIT 5
    """, {"wallet_id": wallet_id, "from_date": thirty_days_ago}, as_dict=True)

    goals = frappe.get_all(
        "Hisabi Goal",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "status": "active"},
        fields=["goal_name", "target_amount", "current_amount", "currency"],
        limit_page_length=5,
    )

    debts = frappe.get_all(
        "Hisabi Debt",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "status": "active"},
        fields=["debt_name", "counterparty_name", "remaining_amount", "currency", "direction"],
    )

    recent_txs = frappe.get_all(
        "Hisabi Transaction",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["note", "amount", "transaction_type", "category", "date_time"],
        order_by="date_time desc",
        limit_page_length=10,
    )

    # Net worth — sum balances of same-currency accounts (avoids mixing FX in the headline).
    accounts = frappe.get_all(
        "Hisabi Account",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "archived": 0},
        fields=["account_name", "current_balance", "currency"],
        limit_page_length=30,
    )
    total_balance = flt(sum(flt(a.current_balance) for a in accounts if (a.currency or base_currency) == base_currency))

    # Budget adherence (spent vs limit).
    budgets = frappe.get_all(
        "Hisabi Budget",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "archived": 0},
        fields=["budget_name", "category", "amount", "spent_amount", "currency"],
        limit_page_length=10,
    )

    # Recurring commitments (subscriptions / repeating bills).
    recurring = frappe.get_all(
        "Hisabi Recurring Rule",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "is_active": 1},
        fields=["title", "transaction_type", "amount", "currency", "rrule_type"],
        limit_page_length=15,
    )

    # Prior 30-day window (days 30–60 ago) for month-over-month trend.
    sixty_days_ago = add_days(today, -60)
    prev = frappe.db.sql("""
        SELECT transaction_type, SUM(ABS(COALESCE(amount_base, amount))) as total
        FROM `tabHisabi Transaction`
        WHERE wallet_id = %(w)s AND is_deleted = 0
          AND date_time >= %(from_date)s AND date_time < %(to_date)s
          AND transaction_type IN ('income', 'expense')
        GROUP BY transaction_type
    """, {"w": wallet_id, "from_date": sixty_days_ago, "to_date": thirty_days_ago}, as_dict=True)
    income_prev_30d = flt(next((r.total for r in prev if r.transaction_type == "income"), 0))
    expense_prev_30d = flt(next((r.total for r in prev if r.transaction_type == "expense"), 0))
    savings_rate = ((flt(income_30d) - flt(expense_30d)) / flt(income_30d)) if flt(income_30d) > 0 else 0.0

    # Data-recency: when the 30-day window is empty (data is months old), reporting "income 0"
    # reads as a bug. Surface the last transaction date + all-time totals so the model can say
    # "no activity in the last 30 days — latest entry was <date>" instead of a bare zero.
    all_time = frappe.db.sql("""
        SELECT transaction_type, SUM(ABS(COALESCE(amount_base, amount))) as total, COUNT(*) as cnt
        FROM `tabHisabi Transaction`
        WHERE wallet_id = %(w)s AND is_deleted = 0
          AND transaction_type IN ('income', 'expense')
        GROUP BY transaction_type
    """, {"w": wallet_id}, as_dict=True)
    all_time_income = flt(next((r.total for r in all_time if r.transaction_type == "income"), 0))
    all_time_expense = flt(next((r.total for r in all_time if r.transaction_type == "expense"), 0))
    all_time_count = sum(int(r.cnt or 0) for r in all_time)
    last_tx_date = frappe.db.sql("""
        SELECT MAX(date_time) FROM `tabHisabi Transaction`
        WHERE wallet_id = %(w)s AND is_deleted = 0
    """, {"w": wallet_id})[0][0]
    last_tx_date = getdate(last_tx_date).isoformat() if last_tx_date else None
    days_since_last_tx = (getdate(today) - getdate(last_tx_date)).days if last_tx_date else None
    has_recent_activity = bool(flt(income_30d) or flt(expense_30d))

    return {
        "base_currency": base_currency,
        "income_30d": flt(income_30d),
        "expense_30d": flt(expense_30d),
        "net_30d": flt(income_30d) - flt(expense_30d),
        "income_prev_30d": income_prev_30d,
        "expense_prev_30d": expense_prev_30d,
        "savings_rate": round(flt(savings_rate), 4),
        "all_time_income": all_time_income,
        "all_time_expense": all_time_expense,
        "all_time_net": all_time_income - all_time_expense,
        "all_time_count": all_time_count,
        "last_tx_date": last_tx_date,
        "days_since_last_tx": days_since_last_tx,
        "has_recent_activity": has_recent_activity,
        "total_balance": total_balance,
        "accounts_count": len(accounts),
        "budgets": [
            {
                "name": b.budget_name, "category": b.category or "",
                "limit": flt(b.amount), "spent": flt(b.spent_amount), "currency": b.currency or base_currency,
            }
            for b in budgets
        ],
        "recurring": [
            {
                "title": r.title, "amount": flt(r.amount), "type": r.transaction_type or "",
                "currency": r.currency or base_currency, "frequency": r.rrule_type or "",
            }
            for r in recurring
        ],
        "top_categories": [{"name": r.category, "total": flt(r.total)} for r in top_cats],
        "active_goals": [
            {
                "name": g.goal_name,
                "target": flt(g.target_amount),
                "current": flt(g.current_amount),
                "currency": g.currency or base_currency,
            }
            for g in goals
        ],
        "debts": [
            {
                "name": d.debt_name,
                "party_name": d.counterparty_name or "",
                "remaining": flt(d.remaining_amount),
                "currency": d.currency or base_currency,
                "direction": d.direction,
            }
            for d in debts
        ],
        "recent_transactions": [
            {
                "description": t.note or "",
                "amount": flt(t.amount),
                "type": t.transaction_type or "",
                "category": t.category or "",
                "date": t.date_time.strftime("%Y-%m-%d") if t.date_time else "",
            }
            for t in recent_txs
        ],
    }


_PERSONA_TONES = {
    "analyst": (
        "شخصيتك: **خبير مالي تحليلي**. نبرة مهنية دقيقة ومباشرة، تركّز على الأرقام والنِّسب "
        "والاستنتاجات الواضحة، بإيجاز ومن دون حشو."
    ),
    "friendly": (
        "شخصيتك: **رفيق ودود ومحفِّز**. نبرة دافئة مشجّعة وبسيطة، بسّط المصطلحات، استخدم رموزاً "
        "تعبيرية باعتدال، واحتفِ بأي تقدّم مالي للمستخدم بإيجابية."
    ),
    "coach": (
        "شخصيتك: **مدرّب مالي**. نبرة تحفيزية موجّهة للفعل والمساءلة، حوّل النصائح إلى تحدّيات "
        "صغيرة قابلة للتنفيذ، وذكّر المستخدم بأهدافه وخطواته القادمة بحماس."
    ),
}


def _persona_tone(persona: Optional[str]) -> str:
    return _PERSONA_TONES.get((persona or "analyst").strip().lower(), _PERSONA_TONES["analyst"])


def _build_financial_advisor_system_prompt(context: Dict[str, Any], persona: Optional[str] = None) -> str:
    """Build the system prompt for the financial advisor, modulated by the chosen persona tone."""
    persona_line = _persona_tone(persona)
    top_cats = "\n".join(
        f"  - {c['name']}: {c['total']:,.2f} {context['base_currency']}"
        for c in context["top_categories"]
    ) or "  (لا توجد بيانات كافية)"

    goals_text = "\n".join(
        f"  - {g['name']}: {g['current']:,.2f} / {g['target']:,.2f} {g['currency']}"
        for g in context["active_goals"]
    ) or "  لا توجد أهداف نشطة"

    debts_text = "\n".join(
        f"  - {d.get('party_name') or d['name']}: {d['remaining']:,.2f} {d['currency']} "
        f"({'مديون لك' if d['direction'] == 'owed_to_me' else 'أنت مديون'})"
        for d in context["debts"]
    ) or "  لا توجد ديون نشطة"

    recent_txs_text = "\n".join(
        f"  - {t['date']} | {t['description']} | {t['amount']:,.2f} | {t['type']} | {t.get('category','')}"
        for t in context.get("recent_transactions", [])
    ) or "  (لا توجد معاملات حديثة)"

    budgets_text = "\n".join(
        f"  - {b['name']} ({b['category'] or 'عام'}): مصروف {b['spent']:,.2f} من {b['limit']:,.2f} {b['currency']}"
        + (" ⚠️ تجاوز الحد" if b["limit"] and b["spent"] > b["limit"] else "")
        for b in context.get("budgets", [])
    ) or "  لا توجد ميزانيات"

    recurring_text = "\n".join(
        f"  - {r['title']}: {r['amount']:,.2f} {r['currency']} "
        f"({'دخل' if r['type'] == 'income' else 'مصروف'}, {r['frequency']})"
        for r in context.get("recurring", [])
    ) or "  لا توجد التزامات متكررة"

    _sr = context.get("savings_rate", 0) or 0
    _exp_prev = flt(context.get("expense_prev_30d", 0))
    if _exp_prev > 0:
        _chg = (flt(context["expense_30d"]) - _exp_prev) / _exp_prev * 100
        expense_trend = f"{_chg:+.0f}% مقارنةً بالثلاثين يوماً السابقة"
    else:
        expense_trend = "لا توجد بيانات كافية للمقارنة"

    # Data-recency guard: an empty 30-day window must not be reported as "income = 0" (the exact
    # bug the user hit). Tell the model the numbers are zero *because the data is old*, and give it
    # the last-entry date + all-time totals to cite instead.
    _bc = context["base_currency"]
    if context.get("has_recent_activity"):
        recency_note = ""
    elif context.get("last_tx_date"):
        _days = context.get("days_since_last_tx")
        _ago = f" (قبل {_days} يوماً)" if _days is not None else ""
        recency_note = (
            f"\n⚠️ تنبيه: لا توجد عمليات في آخر 30 يوماً — آخر عملية بتاريخ {context['last_tx_date']}{_ago}. "
            f"أرقام آخر 30 يوماً أعلاه أصفار لهذا السبب فقط، وليست خطأً في البيانات.\n"
            f"الإجمالي التاريخي لكل العمليات ({context.get('all_time_count', 0)} عملية): "
            f"دخل {context.get('all_time_income', 0):,.2f} — مصروف {context.get('all_time_expense', 0):,.2f} — "
            f"صافٍ {context.get('all_time_net', 0):,.2f} {_bc}.\n"
            f"إذا سأل المستخدم عن دخله أو مصروفاته: وضّح أنه لا يوجد نشاط حديث، واستشهد بآخر عملية "
            f"وبالإجمالي التاريخي. لا تقل «دخلك صفر» بإطلاق."
        )
    else:
        recency_note = "\n⚠️ لا توجد أي عمليات مُسجّلة في هذه المحفظة بعد."

    return f"""أنت «مستشار حسابي» — مستشار مالي شخصي ومحلل اقتصادي خبير بمستوى محترف (CFA)،
متخصص في التخطيط المالي الشخصي، الميزانيات، إدارة الديون، والادخار. تتحدث بثقة الخبير
ووضوح المعلّم، وتحوّل الأرقام إلى رؤى عملية يفهمها أي شخص.

{persona_line}
(حافظ دائماً على تنسيق الرد المنظّم أدناه مهما كانت الشخصية.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
بيانات المستخدم الحقيقية (استند إليها حصراً)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الوضع المالي (آخر 30 يوم):
- العملة الأساسية: {context['base_currency']}
- إجمالي الدخل: {context['income_30d']:,.2f} {context['base_currency']}
- إجمالي المصروفات: {context['expense_30d']:,.2f} {context['base_currency']}
- الصافي: {context['net_30d']:,.2f} {context['base_currency']}
- معدّل الادخار: {_sr*100:.0f}%
- صافي الأرصدة (الثروة): {context.get('total_balance', 0):,.2f} {context['base_currency']} ({context.get('accounts_count', 0)} حساب)
- اتجاه المصروفات: {expense_trend}
{recency_note}

أكثر فئات المصروفات:
{top_cats}

الميزانيات (المصروف مقابل الحد):
{budgets_text}

الالتزامات المتكررة (اشتراكات/فواتير):
{recurring_text}

الأهداف النشطة:
{goals_text}

الديون:
{debts_text}

آخر 10 معاملات:
{recent_txs_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
أطر التحليل المهني (استخدمها بذكاء عند الصلة)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- **معدّل الادخار**: استهدف ≥ 20% من الدخل؛ علّق دائماً على المعدّل الحالي وقارنه بالهدف.
- **صندوق الطوارئ**: 3–6 أشهر من المصروفات الأساسية؛ قدّر كم يلزم المستخدم بالأرقام.
- **قاعدة 50/30/20**: 50% احتياجات، 30% رغبات، 20% ادخار/سداد ديون — قِس واقع المستخدم عليها.
- **سداد الديون**: رتّب بطريقة «الانهيار» (الأعلى فائدة أولاً) أو «كرة الثلج» (الأصغر أولاً) واقترح الأنسب مع تقدير المدة.
- **الالتزامات المتكررة**: راجع الاشتراكات وحدّد ما يمكن إلغاؤه أو خفضه لتحرير سيولة.
- **الميزانيات**: نبّه على أي ميزانية تجاوزت حدّها واقترح تصحيحاً كمّياً.
- استند حصراً إلى أرقام المستخدم أعلاه، واحسب بدقّة (نِسب، فروقات، الأشهر المتبقية حتى الهدف).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
تنسيق الرد (إلزامي — عملي ومختصر جداً)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
القاعدة الذهبية: **أوجز جواب صحيح ومفيد.** المستخدم يريد إجابة عملية واضحة، لا مقالاً. الاختصار = الاحتراف.

1. ابدأ بجملة واحدة مباشِرة تجيب على السؤال فوراً (بلا عنوان، بلا ديباجة).
2. إن كان السؤال بسيطاً → أجب في سطر أو سطرين وتوقّف. **لا تفرض أقساماً.**
3. للأسئلة التحليلية فقط: قسم واحد موجز بحدّ أقصى **٣ نقاط** جوهرية، ثم **✅ خطوات عملية** (٢–٣ خطوات مرقّمة، كلٌّ محدّدة وقابلة للتنفيذ الآن — بمبلغ/نسبة/فئة).
4. الحدّ الأقصى للرد كلّه: **~١٢٠ كلمة** (حجم شاشة هاتف). إن تجاوزته احذف الأقل أهمية.
5. أبرِز كل رقم/نسبة/مبلغ بين نجمتين: **1,250.00 {context['base_currency']}** أو **15%**. النقاط تبدأ بـ «• ».

ممنوع (حشو يُفسد الرد): إعادة صياغة السؤال، الديباجات، التكرار، التحذيرات الطويلة، وعبارات مثل «أحتاج المزيد من البيانات» — إلا إذا تعذّر الجواب فعلاً، فاطلب البند الناقص الواحد بسطر واحد فقط. لا تُطِل لتبدو خبيراً.

مثال (لاحظ القِصَر المطلوب):
مصروفك الأكبر **الإيجار 110,000.00 {context['base_currency']}**، ووضعك متوتّر (ادخار **0%**).

**✅ خطوات عملية**
1. اضبط تجاوز ميزانية «قات» (**+10,200.00 {context['base_currency']}**) هذا الشهر.
2. ابدأ بسداد أكبر دين (**125,000.00 {context['base_currency']}**).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
قواعد مهنية
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- لا تخترع أرقاماً أبداً — استخدم البيانات المُعطاة فقط. إن نقصت البيانات، قُل ذلك بصراحة.
- كن دقيقاً في الحسابات (النسب، الفروقات، المتوسطات) واعرض كيف وصلت للنتيجة بإيجاز.
- عندما تقترح تعديلاً فعلياً على البيانات (تصحيح فئة أو نوع معاملة) أضِف سطراً أخيراً بـ JSON
  action بهذا الشكل الدقيق (خارج التنسيق أعلاه، في آخر الرد فقط):
  ACTION::{{"type":"correction","label":"تصحيح فئة المعاملة","description":"وصف مختصر للتعديل المقترح","payload":{{"transaction_id":"TX_ID","category":"الفئة الجديدة","transaction_type":"expense"}}}}
- للتعديل الجماعي على معاملات الاستيراد استخدم:
  ACTION::{{"type":"import_batch_fix","label":"تصحيح عمليات الاستيراد","description":"وصف مختصر","payload":{{"corrections":[{{"transaction_id":"TX_ID","category":"فئة","transaction_type":"expense"}}]}}}}
- لا تُرجع ACTION إلا عند اقتراح تعديل فعلي على البيانات."""


_VALID_TRANSACTION_TYPES = {"income", "expense", "transfer"}


def _require_wallet_category(category_name: str, wallet_id: str) -> None:
    """Ensure a category name exists and belongs to this wallet (set_value skips Link checks)."""
    if not frappe.db.exists(
        "Hisabi Category",
        {"name": category_name, "wallet_id": wallet_id, "is_deleted": 0},
    ):
        frappe.throw(_("Invalid category for this wallet"), frappe.ValidationError)


def _extract_json_block(text: str, array: bool = False) -> str:
    """Extract a JSON object or array from a text that may contain markdown fences."""
    opener, closer = ("[", "]") if array else ("{", "}")
    pattern = rf"```(?:json)?\s*(\{re.escape(opener)}.*?\{re.escape(closer)})\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    return text


# ---------------------------------------------------------------------------
# Smart input (natural language / voice → structured transactions)
# ---------------------------------------------------------------------------

def _build_smart_input_system_prompt(
    *,
    base_currency: str,
    income_cats: List[str],
    expense_cats: List[str],
    account_names: List[str],
) -> str:
    """Strong Arabic-first prompt: natural language → structured, wallet-aware transactions."""
    today = getdate().isoformat()
    exp = "، ".join(expense_cats[:40]) or "غير محدد"
    inc = "، ".join(income_cats[:40]) or "غير محدد"
    accts = "، ".join(account_names[:30]) or "غير محدد"
    return f"""أنت محرّك إدخال ذكي لتطبيق مالي عربي. حوّل نص المستخدم إلى معاملات مالية منظّمة بدقّة عالية.

سياق:
- تاريخ اليوم: {today}
- العملة الافتراضية: {base_currency}
- الحسابات المتاحة: {accts}
- فئات المصروفات: {exp}
- فئات الدخل: {inc}

قواعد الاستخراج:
1) قد يحتوي النص على أكثر من معاملة (يفصلها «و» أو «،» أو أسطر) — استخرجها كلّها.
2) transaction_type: "expense" للمصروف، "income" للدخل، "transfer" للتحويل بين الحسابات.
3) amount: رقم موجب فقط. حوّل الأرقام العربية (٠١٢٣٤٥٦٧٨٩) إلى إنجليزية، وافهم «ألف/ألفين/ك» و«مية/مئة».
4) currency: اكتشفها من النص (ريال/ر.س=SAR، دولار/$=USD، درهم=AED، يورو/€=EUR، جنيه=EGP، ريال يمني=YER)؛ وإلا استخدم {base_currency}.
5) category: طابِق أقرب فئة **من القوائم أعلاه فقط** وحسب النوع (مصروف↔فئات المصروفات، دخل↔فئات الدخل). إن لم تجد تطابقاً واضحاً فأرجِع null — لا تخترع فئة جديدة.
6) account: طابِق أقرب حساب من الحسابات المتاحة، أو null.
7) date: حوّل الإشارات النسبية إلى تاريخ فعلي YYYY-MM-DD اعتماداً على تاريخ اليوم (اليوم، أمس، أول أمس، قبل يومين، الأسبوع الماضي). إن لم يُذكر تاريخ فاستخدم null.
8) description: وصف عربي مختصر واضح للمعاملة.
9) confidence: رقم 0..1 حسب وضوح المعاملة.

أعِد **JSON فقط** بلا أي شرح أو نص إضافي، بهذا الشكل بالضبط:
{{"transactions":[{{"amount":0,"currency":"{base_currency}","transaction_type":"expense","description":"","date":null,"category":null,"account":null,"confidence":0.9}}]}}"""


def _match_category(ai_value: Any, candidates: List[str]) -> Optional[str]:
    """Map an AI-suggested category to a REAL wallet category: exact (case-insensitive) →
    substring either direction → token overlap. Returns None if no reasonable match."""
    if ai_value is None:
        return None
    v = str(ai_value).strip().lower()
    if not v:
        return None
    lower_map = {c.strip().lower(): c for c in candidates if c}
    if v in lower_map:
        return lower_map[v]
    for lc, orig in lower_map.items():
        if v in lc or lc in v:
            return orig
    v_tokens = {t for t in re.split(r"[\s/،,-]+", v) if len(t) >= 3}
    best, best_overlap = None, 0
    for lc, orig in lower_map.items():
        c_tokens = {t for t in re.split(r"[\s/،,-]+", lc) if len(t) >= 3}
        overlap = len(v_tokens & c_tokens)
        if overlap > best_overlap:
            best, best_overlap = orig, overlap
    return best if best_overlap > 0 else None


def _normalize_parsed_transactions(
    raw: Any,
    *,
    base_currency: str,
    income_cats: List[str],
    expense_cats: List[str],
    account_names: List[str],
) -> List[Dict[str, Any]]:
    """Coerce/validate AI output so the client always gets clean, safe rows mapped to REAL
    categories/accounts (case-insensitive) — never an invented category."""
    if not isinstance(raw, list):
        return []
    valid_types = {"income", "expense", "transfer"}
    acct_lookup = {a.strip().lower(): a for a in account_names if a}
    out: List[Dict[str, Any]] = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        amount = flt(item.get("amount") or 0)
        if amount <= 0:
            continue
        ttype = str(item.get("transaction_type") or "expense").strip().lower()
        if ttype not in valid_types:
            ttype = "expense"
        currency = (str(item.get("currency") or base_currency).strip().upper() or base_currency)[:8]
        # Match to a REAL category of the right kind — exact first, then fuzzy (substring/token
        # overlap) so "طعام" maps to "طعام ومشروبات"; never invents a category.
        cat_candidates = expense_cats if ttype == "expense" else (
            income_cats if ttype == "income" else (expense_cats + income_cats)
        )
        category = _match_category(item.get("category"), cat_candidates)
        account = item.get("account")
        if account is not None:
            account = acct_lookup.get(str(account).strip().lower())
        date_val = item.get("date")
        if date_val:
            try:
                date_val = getdate(date_val).isoformat()
            except Exception:
                date_val = None
        conf = flt(item.get("confidence") or 0)
        conf = max(0.0, min(1.0, conf)) if conf else 0.8
        out.append({
            "amount": amount,
            "currency": currency,
            "transaction_type": ttype,
            "description": str(item.get("description") or "").strip()[:200],
            "date": date_val or None,
            "category": category,
            "account": account,
            "confidence": conf,
        })
    return out


def _transcribe_openai(audio_bytes: bytes, mime_type: str, settings: Dict[str, Any]) -> str:
    """Transcribe audio via OpenAI's /audio/transcriptions (whisper-1 / gpt-4o-*-transcribe)."""
    import requests

    model = settings.get("openai_audio_model") or "whisper-1"
    base_url = (settings.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings['openai_api_key']}"}
    mt = mime_type or "audio/webm"
    ext = "webm" if "webm" in mt else ("mp4" if "mp4" in mt else ("m4a" if "m4a" in mt else "ogg"))
    files = {"file": (f"audio.{ext}", audio_bytes, mt)}
    data = {"model": model, "language": "ar"}
    response = requests.post(url, headers=headers, files=files, data=data, timeout=90)
    response.raise_for_status()
    return (response.json().get("text") or "").strip()


def _transcribe_gemini(audio_bytes: bytes, mime_type: str, settings: Dict[str, Any]) -> str:
    """Transcribe audio via Gemini (inline audio → generateContent)."""
    import base64 as _b64
    import requests

    model = settings.get("gemini_text_model") or "gemini-1.5-flash"
    url = f"{settings['gemini_base_url'].rstrip('/')}/models/{model}:generateContent"
    headers = {"x-goog-api-key": settings["gemini_api_key"]}
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type or "audio/webm", "data": _b64.b64encode(audio_bytes).decode()}},
                    {"text": "فرّغ هذا الصوت نصّاً حرفياً بالعربية فقط، دون أي إضافات أو شرح."},
                ]
            }
        ]
    }
    response = requests.post(url, headers=headers, json=body, timeout=90)
    response.raise_for_status()
    return (response.json()["candidates"][0]["content"]["parts"][0]["text"] or "").strip()


def _smart_input_context(wallet_id: str, default_currency: Optional[str] = None):
    """Shared wallet context for smart-input parsing (currency + real categories + accounts)."""
    base_currency = (default_currency or "").strip().upper() or (
        frappe.db.get_value(
            "Hisabi Account", {"wallet_id": wallet_id, "is_deleted": 0}, "currency", order_by="creation asc"
        )
        or "SAR"
    )
    cats = frappe.get_all(
        "Hisabi Category", filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["category_name", "kind"], limit_page_length=100,
    )
    income_cats = [c.category_name for c in cats if c.kind == "income"]
    expense_cats = [c.category_name for c in cats if c.kind == "expense"]
    accounts = frappe.get_all(
        "Hisabi Account", filters={"wallet_id": wallet_id, "is_deleted": 0, "archived": 0},
        fields=["account_name"], limit_page_length=50,
    )
    return base_currency, income_cats, expense_cats, [a.account_name for a in accounts]


def _vision_openai(image_bytes: bytes, mime_type: str, prompt: str, settings: Dict[str, Any]) -> str:
    """Extract transactions from a receipt image via OpenAI vision (chat completions + image_url)."""
    import base64 as _b64
    data_url = f"data:{mime_type or 'image/jpeg'};base64,{_b64.b64encode(image_bytes).decode()}"
    content = [
        {"type": "text", "text": prompt + "\n\nاقرأ صورة الإيصال/الفاتورة التالية واستخرج المعاملات منها."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    return _openai_completion(
        [{"role": "user", "content": content}], settings, max_tokens=1500, temperature=0.2
    )


def _vision_gemini(image_bytes: bytes, mime_type: str, prompt: str, settings: Dict[str, Any]) -> str:
    """Extract transactions from a receipt image via Gemini (inline image → generateContent)."""
    import base64 as _b64
    import requests

    model = settings.get("gemini_text_model") or "gemini-1.5-flash"
    url = f"{settings['gemini_base_url'].rstrip('/')}/models/{model}:generateContent"
    headers = {"x-goog-api-key": settings["gemini_api_key"]}
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt + "\n\nاقرأ صورة الإيصال/الفاتورة التالية واستخرج المعاملات منها."},
                    {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": _b64.b64encode(image_bytes).decode()}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.2},
    }
    response = requests.post(url, headers=headers, json=body, timeout=90)
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# Whitelisted endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=False)
def status(device_id: Optional[str] = None) -> Dict[str, Any]:
    """Check if AI is available and configured."""
    user, _device = require_device_token_auth()
    settings = _get_ai_settings()

    provider = settings.get("preferred_provider", "openai")
    has_key = bool(
        settings.get("openai_api_key") if provider == "openai"
        else settings.get("gemini_api_key")
    )

    return {
        "available": settings.get("enable_ai", False) and has_key,
        "provider": provider if has_key else None,
        "features": ["smart_input", "import_categorization", "financial_chat"] if has_key else [],
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def get_ai_status(
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return AI configuration state. Keys are NOT exposed — only presence flags and mode."""
    _user, _device = require_device_token_auth()

    settings = _get_ai_settings()

    try:
        from frappe.utils.password import get_decrypted_password
        raw_openai = get_decrypted_password("Hisabi AI Settings", "Hisabi AI Settings", "openai_api_key") or ""
        has_openai_key = len(raw_openai.strip()) > 5 and raw_openai.strip() != "sk-test-key"
    except Exception:
        has_openai_key = bool(settings.get("openai_api_key"))

    try:
        from frappe.utils.password import get_decrypted_password
        raw_gemini = get_decrypted_password("Hisabi AI Settings", "Hisabi AI Settings", "gemini_api_key") or ""
        has_gemini_key = len(raw_gemini.strip()) > 5
    except Exception:
        has_gemini_key = bool(settings.get("gemini_api_key"))

    if has_openai_key and has_gemini_key:
        mode = "parallel"
    elif has_openai_key:
        mode = "openai_only"
    elif has_gemini_key:
        mode = "gemini_only"
    else:
        mode = "none"

    return {
        "enabled": settings.get("enable_ai", False),
        "mode": mode,
        "has_openai_key": has_openai_key,
        "has_gemini_key": has_gemini_key,
        "openai_model": settings.get("openai_text_model", "gpt-4o-mini"),
        "gemini_model": settings.get("gemini_text_model", "gemini-2.5-flash-lite"),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def save_ai_settings(
    enable_ai: int = 0,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Update AI enable toggle and model names. API keys are managed from Frappe Desk only."""
    user, _device = require_device_token_auth()

    roles = frappe.get_roles(user)
    if "System Manager" not in roles and "Administrator" not in roles and user != "Administrator":
        frappe.throw(_("Only administrators can update AI settings"), frappe.PermissionError)

    updates: Dict[str, Any] = {"enable_ai": cint(enable_ai)}
    if openai_model and openai_model.strip():
        updates["openai_text_model"] = openai_model.strip()
    if gemini_model and gemini_model.strip():
        updates["gemini_text_model"] = gemini_model.strip()

    frappe.db.set_single_value("Hisabi AI Settings", updates)

    return {
        "status": "ok",
        "enabled": bool(cint(enable_ai)),
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def parse_transactions(
    text: str,
    wallet_id: str,
    default_currency: Optional[str] = None,
    context: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse natural-language text into structured transactions via the configured AI provider(s)."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not text or not text.strip():
        frappe.throw(_("text is required"), frappe.ValidationError)

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        frappe.throw(_("AI is not enabled. Please configure Hisabi AI Settings."), frappe.ValidationError)

    base_currency = (default_currency or "").strip().upper() or (
        frappe.db.get_value(
            "Hisabi Account",
            {"wallet_id": wallet_id, "is_deleted": 0},
            "currency",
            order_by="creation asc",
        )
        or "SAR"
    )

    categories = frappe.get_all(
        "Hisabi Category",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["category_name", "kind"],
        limit_page_length=100,
    )
    income_cats = [c.category_name for c in categories if c.kind == "income"]
    expense_cats = [c.category_name for c in categories if c.kind == "expense"]

    accounts = frappe.get_all(
        "Hisabi Account",
        filters={"wallet_id": wallet_id, "is_deleted": 0, "archived": 0},
        fields=["account_name"],
        limit_page_length=50,
    )
    account_names = [a.account_name for a in accounts]

    system_prompt = _build_smart_input_system_prompt(
        base_currency=base_currency,
        income_cats=income_cats,
        expense_cats=expense_cats,
        account_names=account_names,
    )

    try:
        ai_response = _call_ai([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text.strip()},
        ], settings, max_tokens=1500)

        response_text = _extract_json_block(ai_response.strip())
        parsed = json.loads(response_text)
        transactions = _normalize_parsed_transactions(
            parsed.get("transactions", []),
            base_currency=base_currency,
            income_cats=income_cats,
            expense_cats=expense_cats,
            account_names=account_names,
        )
        return {
            "status": "ok",
            "transactions": transactions,
            "server_time": now_datetime().isoformat(),
        }

    except Exception as e:
        frappe.log_error("ai_advisor.parse_transactions: AI call failed", frappe.get_traceback())
        # Never surface the raw exception to the client — it can contain provider URLs/keys or
        # internal detail. The full traceback is logged above.
        return {
            "status": "fallback",
            "transactions": [],
            "error": _("تعذّر تحليل الإدخال بالذكاء الاصطناعي حالياً."),
            "server_time": now_datetime().isoformat(),
        }


@frappe.whitelist(allow_guest=False)
def parse_receipt_image(
    image_base64: str,
    wallet_id: str,
    mime_type: Optional[str] = None,
    default_currency: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract structured transactions from a receipt/invoice photo via the configured AI
    provider's vision model (OpenAI preferred, Gemini fallback). Completes multimodal smart
    input (text + voice + photo) and reuses the same strong prompt + normalization."""
    import base64 as _b64

    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not image_base64 or not str(image_base64).strip():
        frappe.throw(_("image is required"), frappe.ValidationError)

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        frappe.throw(_("AI is not enabled. Please configure Hisabi AI Settings."), frappe.ValidationError)

    raw = str(image_base64)
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        image_bytes = _b64.b64decode(raw)
    except Exception:
        frappe.throw(_("Invalid image data"), frappe.ValidationError)
    if not image_bytes:
        frappe.throw(_("image is required"), frappe.ValidationError)
    if len(image_bytes) > 15 * 1024 * 1024:
        frappe.throw(_("الصورة كبيرة جداً (الحد 15 ميغابايت)."), frappe.ValidationError)

    base_currency, income_cats, expense_cats, account_names = _smart_input_context(wallet_id, default_currency)
    prompt = _build_smart_input_system_prompt(
        base_currency=base_currency, income_cats=income_cats,
        expense_cats=expense_cats, account_names=account_names,
    )

    try:
        if settings.get("openai_api_key"):
            ai_response = _vision_openai(image_bytes, mime_type, prompt, settings)
        elif settings.get("gemini_api_key"):
            ai_response = _vision_gemini(image_bytes, mime_type, prompt, settings)
        else:
            frappe.throw(_("لم يُهيَّأ أي مزود AI يدعم الرؤية. أضف مفتاح API من Frappe Desk."), frappe.ValidationError)
        parsed = json.loads(_extract_json_block(ai_response.strip()))
        transactions = _normalize_parsed_transactions(
            parsed.get("transactions", []),
            base_currency=base_currency, income_cats=income_cats,
            expense_cats=expense_cats, account_names=account_names,
        )
        return {
            "status": "ok",
            "transactions": transactions,
            "server_time": now_datetime().isoformat(),
        }
    except frappe.ValidationError:
        raise
    except Exception:
        frappe.log_error("ai_advisor.parse_receipt_image: failed", frappe.get_traceback())
        return {
            "status": "fallback",
            "transactions": [],
            "error": _("تعذّر قراءة الإيصال حالياً. حاول بصورة أوضح."),
            "server_time": now_datetime().isoformat(),
        }


@frappe.whitelist(allow_guest=False)
def transcribe_audio(
    audio_base64: str,
    wallet_id: str,
    mime_type: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Transcribe a voice recording to text via the configured AI provider (OpenAI preferred,
    Gemini fallback) — replaces the legacy Supabase voice-to-text function."""
    import base64 as _b64

    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not audio_base64 or not str(audio_base64).strip():
        frappe.throw(_("audio is required"), frappe.ValidationError)

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        frappe.throw(_("AI is not enabled. Please configure Hisabi AI Settings."), frappe.ValidationError)

    raw = str(audio_base64)
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]  # tolerate a data: URL prefix
    try:
        audio_bytes = _b64.b64decode(raw)
    except Exception:
        frappe.throw(_("Invalid audio data"), frappe.ValidationError)
    if not audio_bytes:
        frappe.throw(_("audio is required"), frappe.ValidationError)
    if len(audio_bytes) > 25 * 1024 * 1024:  # OpenAI hard limit is 25MB
        frappe.throw(_("الملف الصوتي كبير جداً (الحد 25 ميغابايت)."), frappe.ValidationError)

    try:
        if settings.get("openai_api_key"):
            transcript = _transcribe_openai(audio_bytes, mime_type, settings)
        elif settings.get("gemini_api_key"):
            transcript = _transcribe_gemini(audio_bytes, mime_type, settings)
        else:
            frappe.throw(_("لم يُهيَّأ أي مزود AI للصوت. أضف مفتاح API من Frappe Desk."), frappe.ValidationError)
        return {
            "status": "ok",
            "transcript": transcript,
            "server_time": now_datetime().isoformat(),
        }
    except frappe.ValidationError:
        raise
    except Exception:
        frappe.log_error("ai_advisor.transcribe_audio: failed", frappe.get_traceback())
        # Never surface the raw exception (may contain provider URLs/keys).
        return {
            "status": "error",
            "transcript": "",
            "error": _("تعذّر تحويل الصوت إلى نص حالياً. حاول مرة أخرى."),
            "server_time": now_datetime().isoformat(),
        }


@frappe.whitelist(allow_guest=False)
def parse_bank_statement(
    rows: str,
    wallet_id: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Categorize bank statement rows using AI."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if isinstance(rows, str):
        rows_list = json.loads(rows)
    else:
        rows_list = rows or []

    if not rows_list:
        return {"status": "ok", "rows": [], "server_time": now_datetime().isoformat()}

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        return {"status": "fallback", "rows": rows_list, "server_time": now_datetime().isoformat()}

    categories = frappe.get_all(
        "Hisabi Category",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["category_name", "kind"],
        limit_page_length=50,
    )
    cat_list = "\n".join(f"- {c.category_name} ({c.kind})" for c in categories[:20])

    batch_size = 20
    enriched_rows: List[Dict[str, Any]] = []

    for i in range(0, len(rows_list), batch_size):
        batch = rows_list[i:i + batch_size]
        rows_json = json.dumps(batch, ensure_ascii=False)

        prompt = f"""صنّف هذه الصفوف من كشف حساب بنكي. الفئات المتاحة:
{cat_list}

الصفوف:
{rows_json}

أرجع ONLY JSON قائمة بنفس الترتيب مع إضافة category و transaction_type و ai_confidence لكل صف:
[{{"row_index": 0, "category": "...", "transaction_type": "income|expense", "ai_confidence": 0.9}}, ...]"""

        try:
            ai_response = _call_ai([{"role": "user", "content": prompt}], settings, max_tokens=1500)
            response_text = _extract_json_block(ai_response.strip(), array=True)
            classifications = json.loads(response_text)
            class_map = {c["row_index"]: c for c in classifications if isinstance(c, dict)}
            for j, row in enumerate(batch):
                row_index = row.get("row_index", i + j)
                cls = class_map.get(row_index, {})
                enriched_rows.append({
                    **row,
                    "row_index": row_index,
                    "category": cls.get("category") or row.get("category"),
                    "transaction_type": cls.get("transaction_type") or row.get("transaction_type", "expense"),
                    "ai_confidence": flt(cls.get("ai_confidence", 0.5)),
                })
        except Exception:
            frappe.log_error("ai_advisor.parse_bank_statement: batch failed", frappe.get_traceback())
            enriched_rows.extend(batch)

    return {"status": "ok", "rows": enriched_rows, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def get_financial_context(
    wallet_id: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a financial context summary for the wallet (no AI tokens used)."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    context = _get_wallet_financial_context(wallet_id, user)
    return {"status": "ok", "context": context, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def chat(
    wallet_id: str,
    message: str,
    session_id: Optional[str] = None,
    device_id: Optional[str] = None,
    persona: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a message to the AI financial advisor. `persona` selects the assistant's tone
    (analyst | friendly | coach)."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not message or not message.strip():
        frappe.throw(_("message is required"), frappe.ValidationError)

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        frappe.throw(_("AI is not enabled. Please configure Hisabi AI Settings."), frappe.ValidationError)

    if session_id and frappe.db.exists("Hisabi AI Chat Session", session_id):
        chat_session = frappe.get_doc("Hisabi AI Chat Session", session_id)
        if chat_session.wallet_id != wallet_id or chat_session.user != user:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    else:
        new_client_id = frappe.generate_hash(length=16)
        chat_session = frappe.new_doc("Hisabi AI Chat Session")
        chat_session.client_id = new_client_id
        chat_session.name = new_client_id
        chat_session.wallet_id = wallet_id
        chat_session.user = user
        chat_session.status = "active"
        chat_session.session_title = message[:80]
        chat_session.message_count = 0
        chat_session.insert(ignore_permissions=True)
        session_id = chat_session.name

    existing_messages = frappe.get_all(
        "Hisabi AI Chat Message",
        filters={"parent": session_id},
        fields=["role", "content"],
        order_by="idx asc",
        limit_page_length=20,
    )

    context = _get_wallet_financial_context(wallet_id, user)
    system_prompt = _build_financial_advisor_system_prompt(context, persona=persona)

    messages = [{"role": "system", "content": system_prompt}]
    for msg in existing_messages[-20:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": message})

    try:
        # Capped to keep advisor replies concise/practical (the prompt targets ~120 words); the
        # ceiling just prevents rambling while leaving a reasoning model enough headroom.
        ai_response = _call_ai(messages, settings, max_tokens=900)
    except Exception as e:
        frappe.log_error("ai_advisor.chat: AI call failed", frappe.get_traceback())
        # Generic message only — the raw exception may contain provider URLs/keys or internals.
        return {
            "status": "error",
            "error": _("تعذّر الحصول على رد من المستشار الذكي حالياً. حاول مرة أخرى."),
            "session_id": session_id,
            "server_time": now_datetime().isoformat(),
        }

    # Only accept a well-formed ACTION whose `type` is a known client action string — a
    # malformed/non-string type (e.g. an object) must never crash the save.
    _CLIENT_ACTION_TYPES = {"correction", "bulk_recategorize", "import_batch_fix"}
    _MSG_ACTION_TYPES = {"correction", "bulk_recategorize", "analysis"}  # Hisabi AI Chat Message Select
    action = None
    response_text = ai_response
    if "ACTION::" in ai_response:
        parts = ai_response.split("ACTION::", 1)
        response_text = parts[0].strip()
        try:
            raw_action = json.loads(parts[1].strip())
            a_type = raw_action.get("type") if isinstance(raw_action, dict) else None
            if isinstance(a_type, str) and a_type.strip() in _CLIENT_ACTION_TYPES:
                payload = raw_action.get("payload")
                action = {
                    "type": a_type.strip(),
                    "label": str(raw_action.get("label") or "إجراء مقترح"),
                    "description": str(raw_action.get("description") or ""),
                    "payload": payload if isinstance(payload, dict) else {},
                }
        except Exception:
            pass

    # Persist action_type only when it's a valid Select option; the full action is always
    # available to the client via action_payload + the response `action`.
    saved_action_type = action["type"] if (action and action["type"] in _MSG_ACTION_TYPES) else ""

    now = now_datetime()
    chat_session.reload()
    chat_session.append("messages", {
        "role": "user",
        "content": message,
        "sent_at": now,
    })
    chat_session.append("messages", {
        "role": "assistant",
        "content": response_text,
        "sent_at": now,
        "has_action": 1 if action else 0,
        "action_type": saved_action_type,
        "action_payload": json.dumps(action, ensure_ascii=False) if action else None,
    })
    chat_session.message_count = (chat_session.message_count or 0) + 2
    chat_session.last_message_at = now
    chat_session.save(ignore_permissions=True)

    return {
        "status": "ok",
        "response": response_text,
        "session_id": session_id,
        "action": action,
        "server_time": now.isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def get_chat_sessions(
    wallet_id: str,
    limit: int = 20,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List AI chat sessions for a wallet."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    sessions = frappe.get_all(
        "Hisabi AI Chat Session",
        filters={"wallet_id": wallet_id, "user": user, "status": "active"},
        fields=["name", "client_id", "session_title", "message_count", "last_message_at", "creation"],
        order_by="last_message_at desc",
        limit_page_length=min(cint(limit) or 20, 100),
    )

    return {"status": "ok", "sessions": sessions, "server_time": now_datetime().isoformat()}


@frappe.whitelist(allow_guest=False)
def get_chat_session(
    session_id: str,
    wallet_id: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a single chat session with all messages."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="viewer")

    if not frappe.db.exists("Hisabi AI Chat Session", session_id):
        frappe.throw(_("Chat session not found"), frappe.DoesNotExistError)

    session = frappe.get_doc("Hisabi AI Chat Session", session_id)
    if session.wallet_id != wallet_id or session.user != user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    msgs = [
        {
            "role": m.role,
            "content": m.content,
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "has_action": bool(m.has_action),
            "action_type": m.action_type,
            "action_payload": json.loads(m.action_payload) if m.action_payload else None,
        }
        for m in (session.messages or [])
    ]

    return {
        "status": "ok",
        "session": {
            "session_id": session.name,
            "session_title": session.session_title,
            "message_count": session.message_count,
            "last_message_at": session.last_message_at,
        },
        "messages": msgs,
        "server_time": now_datetime().isoformat(),
    }


@frappe.whitelist(allow_guest=False)
def apply_ai_action(
    wallet_id: str,
    action_type: str,
    payload: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply an AI-suggested action (category correction, bulk recategorize, etc.)."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if isinstance(payload, str):
        payload_data = json.loads(payload) if payload else {}
    else:
        payload_data = payload or {}

    if action_type == "correction":
        tx_id = payload_data.get("transaction_id")
        if not tx_id:
            frappe.throw(_("transaction_id required for correction"), frappe.ValidationError)

        updates = {}
        if payload_data.get("category"):
            updates["category"] = payload_data["category"]
        if payload_data.get("transaction_type"):
            updates["transaction_type"] = payload_data["transaction_type"]

        if updates and frappe.db.exists("Hisabi Transaction", tx_id):
            tx = frappe.get_doc("Hisabi Transaction", tx_id)
            if getattr(tx, "wallet_id", None) != wallet_id:
                frappe.throw(_("Transaction not in this wallet"), frappe.PermissionError)
            for field, value in updates.items():
                setattr(tx, field, value)
            tx.save(ignore_permissions=True)
            return {"status": "ok", "action_type": action_type, "updated": tx_id}

    elif action_type == "bulk_recategorize":
        tx_ids = payload_data.get("transaction_ids", [])
        new_category = payload_data.get("category")
        if not tx_ids or not new_category:
            frappe.throw(_("transaction_ids and category required"), frappe.ValidationError)
        # set_value bypasses Link validation — verify the category belongs to THIS wallet so a
        # crafted payload can't plant a foreign/arbitrary category reference.
        _require_wallet_category(new_category, wallet_id)

        owned = frappe.get_all(
            "Hisabi Transaction",
            filters={"name": ["in", tx_ids], "wallet_id": wallet_id, "is_deleted": 0},
            pluck="name",
        )
        for name in owned:
            frappe.db.set_value("Hisabi Transaction", name, "category", new_category)
        return {"status": "ok", "action_type": action_type, "updated": len(owned)}

    elif action_type == "import_batch_fix":
        corrections = payload_data.get("corrections", [])
        if not corrections:
            frappe.throw(_("corrections list is required"), frappe.ValidationError)

        tx_ids = [c.get("transaction_id") for c in corrections if c.get("transaction_id")]
        owned = {
            r.name
            for r in frappe.get_all(
                "Hisabi Transaction",
                filters={"name": ["in", tx_ids], "wallet_id": wallet_id, "is_deleted": 0},
                fields=["name"],
            )
        }

        updated = 0
        for correction in corrections:
            tx_id = correction.get("transaction_id")
            if not tx_id or tx_id not in owned:
                continue
            updates = {}
            if correction.get("category"):
                _require_wallet_category(correction["category"], wallet_id)
                updates["category"] = correction["category"]
            if correction.get("transaction_type"):
                if correction["transaction_type"] not in _VALID_TRANSACTION_TYPES:
                    frappe.throw(_("Invalid transaction_type"), frappe.ValidationError)
                updates["transaction_type"] = correction["transaction_type"]
            if updates:
                for field, value in updates.items():
                    frappe.db.set_value("Hisabi Transaction", tx_id, field, value)
                updated += 1

        return {"status": "ok", "action_type": action_type, "updated": updated}

    return {"status": "ok", "action_type": action_type, "message": "no action taken"}


@frappe.whitelist(allow_guest=False)
def review_imports(
    wallet_id: str,
    import_session_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Review import session rows with AI and suggest category/type corrections."""
    import json as _json
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        frappe.throw(_("AI is not enabled. Please configure Hisabi AI Settings."), frappe.ValidationError)

    # Resolve import session
    if not import_session_id:
        recent = frappe.get_all(
            "Hisabi Import Session",
            filters={"wallet_id": wallet_id, "is_deleted": 0},
            fields=["name", "file_name", "status", "total_rows", "confirmed_at"],
            order_by="creation desc",
            limit_page_length=1,
        )
        if not recent:
            return {
                "status": "no_sessions",
                "message": "لا توجد جلسات استيراد لهذه المحفظة",
                "corrections": [],
                "server_time": now_datetime().isoformat(),
            }
        import_session_id = recent[0].name

    if not frappe.db.exists("Hisabi Import Session", import_session_id):
        frappe.throw(_("Import session not found"), frappe.DoesNotExistError)

    session = frappe.get_doc("Hisabi Import Session", import_session_id)
    if session.wallet_id != wallet_id:
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    # Get rows that have been imported (have a transaction_id)
    import_rows = [
        r for r in (session.import_rows or [])
        if r.transaction_id and r.status == "imported"
    ]

    if not import_rows:
        return {
            "status": "no_rows",
            "message": "لا توجد صفوف مستوردة في هذه الجلسة",
            "session_id": import_session_id,
            "corrections": [],
            "server_time": now_datetime().isoformat(),
        }

    # Limit to 50 rows to keep AI prompt manageable
    import_rows = import_rows[:50]

    categories = frappe.get_all(
        "Hisabi Category",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["category_name", "kind"],
        limit_page_length=50,
    )
    cat_list = "\n".join(f"- {c.category_name} ({c.kind})" for c in categories[:20])

    rows_for_ai = [
        {
            "index": i,
            "transaction_id": r.transaction_id,
            "description": r.description or "",
            "amount": flt(r.amount),
            "current_category": r.category or "",
            "current_type": r.transaction_type or "",
            "ai_confidence": flt(r.ai_confidence),
        }
        for i, r in enumerate(import_rows)
    ]

    rows_json = _json.dumps(rows_for_ai, ensure_ascii=False)

    prompt = f"""راجع هذه المعاملات المستوردة من كشف البنك واقترح تصحيحات للفئات والأنواع.

الفئات المتاحة:
{cat_list}

المعاملات:
{rows_json}

أرجع ONLY JSON قائمة بنفس ترتيب index. لكل معاملة تحتاج تصحيحاً أرجع:
{{"index": 0, "transaction_id": "...", "suggested_category": "...", "suggested_type": "income|expense", "reason": "سبب مختصر", "confidence": 0.9}}

أرجع فقط المعاملات التي تحتاج تصحيحاً (لا تُرجع المعاملات الصحيحة). أرجع [] إذا كل شيء صحيح."""

    try:
        ai_response = _call_ai([{"role": "user", "content": prompt}], settings, max_tokens=2000)
        response_text = _extract_json_block(ai_response.strip(), array=True)
        suggestions = _json.loads(response_text)
        if not isinstance(suggestions, list):
            suggestions = []
    except Exception:
        frappe.log_error("ai_advisor.review_imports: AI call failed", frappe.get_traceback())
        suggestions = []

    tx_index_map = {r["index"]: r for r in rows_for_ai}
    corrections = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        idx = s.get("index")
        if idx is None:
            continue
        row = tx_index_map.get(idx)
        if not row:
            continue
        corrections.append({
            "transaction_id": row["transaction_id"],
            "description": row["description"],
            "amount": row["amount"],
            "current_category": row["current_category"],
            "current_type": row["current_type"],
            "suggested_category": s.get("suggested_category") or row["current_category"],
            "suggested_type": s.get("suggested_type") or row["current_type"],
            "reason": s.get("reason", ""),
            "confidence": flt(s.get("confidence", 0.8)),
        })

    return {
        "status": "ok",
        "session_id": import_session_id,
        "file_name": session.file_name or "",
        "total_rows_reviewed": len(import_rows),
        "corrections_count": len(corrections),
        "corrections": corrections,
        "server_time": now_datetime().isoformat(),
    }
