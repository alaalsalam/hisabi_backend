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
        return {
            "enable_ai": bool(s.enable_ai),
            "preferred_provider": s.preferred_provider or "openai",
            "openai_api_key": s.openai_api_key or "",
            "openai_base_url": s.openai_base_url or "https://api.openai.com/v1",
            "openai_text_model": s.openai_text_model or "gpt-4o-mini",
            "gemini_api_key": s.gemini_api_key or "",
            "gemini_base_url": s.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta",
            "gemini_text_model": s.gemini_text_model or "gemini-1.5-flash",
        }
    except Exception:
        return {
            "enable_ai": False,
            "preferred_provider": "openai",
            "openai_api_key": "",
            "gemini_api_key": "",
        }


def _call_ai(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int = 2000) -> str:
    """Call OpenAI or Gemini and return the assistant's text response."""
    provider = settings.get("preferred_provider", "openai")

    if provider == "gemini" and settings.get("gemini_api_key"):
        return _call_gemini(messages, settings, max_tokens)
    elif settings.get("openai_api_key"):
        return _call_openai(messages, settings, max_tokens)
    else:
        frappe.throw(
            _("No AI provider configured. Please set API keys in Hisabi AI Settings."),
            frappe.ValidationError,
        )


def _call_openai(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int) -> str:
    """Call OpenAI Chat Completions API."""
    import requests

    url = f"{settings['openai_base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings['openai_api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.get("openai_text_model", "gpt-4o-mini"),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _call_gemini(messages: List[Dict[str, str]], settings: Dict[str, Any], max_tokens: int) -> str:
    """Call Google Gemini API."""
    import requests

    model = settings.get("gemini_text_model", "gemini-1.5-flash")
    url = f"{settings['gemini_base_url'].rstrip('/')}/models/{model}:generateContent"
    params = {"key": settings["gemini_api_key"]}

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

    response = requests.post(url, params=params, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _get_wallet_financial_context(wallet_id: str, user: str) -> Dict[str, Any]:
    """Build a financial context summary for a wallet (used in AI prompts)."""
    today = getdate()
    thirty_days_ago = add_days(today, -30)

    base_currency = frappe.db.get_value("Hisabi Wallet", wallet_id, "base_currency") or "SAR"

    tx_summary = frappe.db.sql("""
        SELECT transaction_type,
               SUM(COALESCE(amount_base, amount)) as total
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
        SELECT category, SUM(COALESCE(amount_base, amount)) as total
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
        fields=["debt_name", "remaining_amount", "currency", "direction"],
        limit_page_length=5,
    )

    return {
        "base_currency": base_currency,
        "income_30d": flt(income_30d),
        "expense_30d": flt(expense_30d),
        "net_30d": flt(income_30d) - flt(expense_30d),
        "top_categories": [{"name": r.category, "total": flt(r.total)} for r in top_cats],
        "active_goals": [
            {
                "name": g.goal_name,
                "target": flt(g.target_amount),
                "current": flt(g.current_amount),
                "currency": g.currency,
            }
            for g in goals
        ],
        "debts": [
            {
                "name": d.debt_name,
                "remaining": flt(d.remaining_amount),
                "currency": d.currency,
                "direction": d.direction,
            }
            for d in debts
        ],
    }


def _build_financial_advisor_system_prompt(context: Dict[str, Any]) -> str:
    """Build the system prompt for the financial advisor."""
    top_cats = "\n".join(
        f"  - {c['name']}: {c['total']:,.2f} {context['base_currency']}"
        for c in context["top_categories"]
    ) or "  (لا توجد بيانات كافية)"

    goals_text = "\n".join(
        f"  - {g['name']}: {g['current']:,.2f} / {g['target']:,.2f} {g['currency']}"
        for g in context["active_goals"]
    ) or "  لا توجد أهداف نشطة"

    debts_text = "\n".join(
        f"  - {d['name']}: {d['remaining']:,.2f} {d['currency']} "
        f"({'مديون' if d['direction'] == 'owe' else 'دائن'})"
        for d in context["debts"]
    ) or "  لا توجد ديون نشطة"

    return f"""أنت مستشار مالي شخصي لمستخدم تطبيق حسابي. إليك بياناته الحقيقية:

📊 الوضع المالي (آخر 30 يوم):
- العملة الأساسية: {context['base_currency']}
- إجمالي الدخل: {context['income_30d']:,.2f} {context['base_currency']}
- إجمالي المصروفات: {context['expense_30d']:,.2f} {context['base_currency']}
- الصافي: {context['net_30d']:,.2f} {context['base_currency']}

🗂️ أكثر فئات المصروفات:
{top_cats}

🎯 الأهداف النشطة:
{goals_text}

💳 الديون:
{debts_text}

قواعد المستشار:
- ردَّ دائماً بالعربية الفصيحة المبسطة
- قدِّم توصيات ملموسة وقابلة للتنفيذ بناءً على الأرقام الحقيقية
- عندما تقترح تعديلاً على البيانات (تصحيح فئة، دمج مدخلات مكررة)، أرجع JSON action في نهاية ردك بالشكل:
  ACTION::{{"type":"correction","description":"وصف التعديل","data":{{}}}}
- لا تخترع أرقاماً — استخدم البيانات المُعطاة فقط
- إذا لم يكن لديك بيانات كافية، أخبر المستخدم بذلك بصراحة"""


def _extract_json_block(text: str, array: bool = False) -> str:
    """Extract a JSON object or array from a text that may contain markdown fences."""
    opener, closer = ("[", "]") if array else ("{", "}")
    pattern = rf"```(?:json)?\s*(\{re.escape(opener)}.*?\{re.escape(closer)})\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    return text


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
def parse_transactions(
    text: str,
    wallet_id: str,
    context: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse natural-language text into structured transactions."""
    user, _device = require_device_token_auth()
    wallet_id = validate_client_id(wallet_id)
    require_wallet_member(wallet_id, user, min_role="member")

    if not text or not text.strip():
        frappe.throw(_("text is required"), frappe.ValidationError)

    settings = _get_ai_settings()
    if not settings.get("enable_ai"):
        frappe.throw(_("AI is not enabled. Please configure Hisabi AI Settings."), frappe.ValidationError)

    categories = frappe.get_all(
        "Hisabi Category",
        filters={"wallet_id": wallet_id, "is_deleted": 0},
        fields=["category_name", "category_type"],
        limit_page_length=50,
    )
    cat_list = ", ".join(f"{c.category_name} ({c.category_type})" for c in categories[:20])

    system_prompt = f"""أنت محلل مالي. استخرج المعاملات المالية من النص التالي وأرجعها كـ JSON.

الفئات المتاحة: {cat_list or 'غير محدد'}

أرجع ONLY JSON بالشكل التالي (لا نص إضافي):
{{
  "transactions": [
    {{
      "amount": رقم,
      "currency": "SAR",
      "transaction_type": "income" أو "expense",
      "description": "وصف",
      "date": "YYYY-MM-DD" أو null,
      "category": "اسم الفئة" أو null,
      "confidence": رقم بين 0 و 1
    }}
  ]
}}"""

    try:
        ai_response = _call_ai([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ], settings, max_tokens=1000)

        response_text = _extract_json_block(ai_response.strip())
        parsed = json.loads(response_text)
        return {
            "status": "ok",
            "transactions": parsed.get("transactions", []),
            "server_time": now_datetime().isoformat(),
        }

    except Exception as e:
        frappe.log_error("ai_advisor.parse_transactions: AI call failed", frappe.get_traceback())
        return {
            "status": "fallback",
            "transactions": [],
            "error": str(e),
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
        fields=["category_name", "category_type"],
        limit_page_length=50,
    )
    cat_list = "\n".join(f"- {c.category_name} ({c.category_type})" for c in categories[:20])

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
                cls = class_map.get(j, {})
                enriched_rows.append({
                    **row,
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
) -> Dict[str, Any]:
    """Send a message to the AI financial advisor."""
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
    system_prompt = _build_financial_advisor_system_prompt(context)

    messages = [{"role": "system", "content": system_prompt}]
    for msg in existing_messages[-20:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": message})

    try:
        ai_response = _call_ai(messages, settings, max_tokens=1500)
    except Exception as e:
        frappe.log_error("ai_advisor.chat: AI call failed", frappe.get_traceback())
        return {
            "status": "error",
            "error": str(e),
            "session_id": session_id,
            "server_time": now_datetime().isoformat(),
        }

    action = None
    response_text = ai_response
    if "ACTION::" in ai_response:
        parts = ai_response.split("ACTION::", 1)
        response_text = parts[0].strip()
        try:
            action = json.loads(parts[1].strip())
        except Exception:
            pass

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
        "action_type": action.get("type") if action else None,
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
        limit_page_length=cint(limit) or 20,
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

        owned = frappe.get_all(
            "Hisabi Transaction",
            filters={"name": ["in", tx_ids], "wallet_id": wallet_id, "is_deleted": 0},
            pluck="name",
        )
        for name in owned:
            frappe.db.set_value("Hisabi Transaction", name, "category", new_category)
        return {"status": "ok", "action_type": action_type, "updated": len(owned)}

    return {"status": "ok", "action_type": action_type, "message": "no action taken"}
