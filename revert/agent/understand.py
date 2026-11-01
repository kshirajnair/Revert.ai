"""Turn a dealer's message into an `Understanding`: intent, language, and checked slots.

Two engines produce the same structure:
- an LLM (Gemini by default; Claude or OpenAI via LLM_PROVIDER), which copes with free-form
  Hinglish/Kannada better;
- an offline keyword classifier, used when no API key is set or the LLM call fails, and in tests.

Whatever the LLM returns is validated here: unknown intents become `unclear`, and amounts,
invoice numbers and references must also be found in the message by `parse.py`, or they are
replaced by the parser's own reading. The LLM never supplies a number the dealer didn't write.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from revert.agent import parse
from revert.agent.llm import LLMError, LLMProvider

log = logging.getLogger(__name__)

INTENTS = (
    "already_paid",     # "I paid last week", or a UTR
    "promise_to_pay",   # "will pay Friday", "50k now, rest on 15th"
    "dispute",          # "rate is wrong", "short delivery"
    "send_invoice",     # "send INV-1043"
    "send_statement",   # "send my statement / ledger"
    "balance_query",    # "how much do I owe"
    "stock_query",      # "is 16A MCB available", "RCCB rate?"
    "order_request",    # "need 50 MCB 16A"
    "opt_out",          # "stop messaging me"
    "smalltalk",        # hi / thanks / ok / are you a bot
    "unclear",
)
LANGUAGES = ("en", "hi", "kn")
MAX_PROMISE_DAYS = 120


@dataclass(frozen=True)
class Understanding:
    intent: str
    language: str
    upset: bool = False
    invoice_refs: tuple[str, ...] = ()
    payment_ref: str = ""
    installments: tuple[parse.Installment, ...] = ()
    item_query: str = ""
    qty: int | None = None
    asks_identity: bool = False
    engine: str = "rules"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_meta(self) -> dict:
        return {
            "engine": self.engine, "intent": self.intent, "language": self.language, "upset": self.upset,
            "invoice_refs": list(self.invoice_refs), "payment_ref": self.payment_ref,
            "installments": [{"amount_paise": i.amount_paise, "due": i.due.isoformat() if i.due else None,
                              "rest": i.rest} for i in self.installments],
            "item_query": self.item_query, "qty": self.qty, "notes": list(self.notes),
        }


# --- language -------------------------------------------------------------------------------

_HI_ROMAN = {"hai", "hain", "kya", "kal", "bhej", "bhejo", "bhejiye", "diya", "dunga", "denge", "karunga",
             "paisa", "paise", "nahi", "nahin", "ji", "kitna", "kitne", "chahiye", "galat", "kar", "aap",
             "mera", "meri", "hum", "abhi", "baaki", "baki", "hafte", "tak", "ho", "gaya", "bhai", "sahab"}
_KN_ROMAN = {"illa", "ide", "ideya", "beku", "maadi", "madi", "kodi", "swalpa", "yaavaga", "yavaga", "ivattu",
             "naale", "nale", "saar", "kalisi", "kalsi", "aagide", "agide", "maadtini", "kodtini", "yeshtu",
             "eshtu", "anna", "guru", "ri"}


def detect_language(text: str, fallback: str = "en") -> str:
    if re.search(r"[ಀ-೿]", text):
        return "kn"
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    words = set(re.findall(r"[a-z]+", text.lower()))
    hi, kn = len(words & _HI_ROMAN), len(words & _KN_ROMAN)
    if kn > hi and kn >= 1:
        return "kn"
    if hi >= 2 or (hi == 1 and len(words) <= 4):
        return "hi"
    if words:
        return "en"
    return fallback


# --- offline keyword classifier -------------------------------------------------------------

def _has(text: str, words: list[str]) -> bool:
    return bool(parse._find_words(text, words))


_DISPUTE = ["wrong", "incorrect", "mistake", "short delivery", "short supply", "short", "damaged", "defective",
            "broken", "mismatch", "overcharged", "over charged", "extra charged", "excess", "not ordered",
            "po said", "po rate", "dispute", "galat", "kam aaya", "kam diya", "kharab", "tuta", "गलत", "कम आया",
            "खराब", "टूटा", "ತಪ್ಪು", "ಕಡಿಮೆ ಬಂದಿದೆ", "ಹಾಳಾಗಿದೆ", "tappu", "kammi"]
_PAID = ["already paid", "paid", "payment done", "payment made", "payment sent", "sent the payment", "transferred",
         "cleared", "bhej diya", "bhej diye", "kar diya", "de diya", "ho gaya", "jama kar", "pay kar diya",
         "भेज दिया", "कर दिया", "दे दिया", "हो गया", "जमा कर", "ಕಳುಹಿಸಿದ್ದೇನೆ", "ಪಾವತಿ ಮಾಡಿದ್ದೇನೆ", "ಕೊಟ್ಟಿದ್ದೇನೆ",
         "kalisiddini", "kottiddini", "maadiddini"]
_PROMISE = ["will pay", "i'll pay", "ill pay", "shall pay", "can pay", "pay by", "pay on", "pay you", "will send",
            "will transfer", "will clear", "will do the payment", "pay next", "paying", "dunga", "denge", "de dunga",
            "kar dunga", "kar denge", "karunga", "karenge", "bhej dunga", "bhej denge", "दूंगा", "दूँगा", "देंगे",
            "कर दूंगा", "करेंगे", "भेज दूंगा", "ಕೊಡುತ್ತೇನೆ", "ಪಾವತಿಸುತ್ತೇನೆ", "ಕಳುಹಿಸುತ್ತೇನೆ", "kodtini", "maadtini",
            "kalistini"]
_OPT_OUT = ["stop messaging", "stop sending", "stop these", "don't message", "dont message", "do not message",
            "unsubscribe", "no more messages", "stop reminders", "mat bhejo", "band karo", "मत भेजो", "बंद करो",
            "ಕಳುಹಿಸಬೇಡಿ", "nillisi"]
_INVOICE = ["invoice", "bill", "copy of", "बिल", "ಬಿಲ್"]
_STATEMENT = ["statement", "ledger", "account copy", "soa", "hisab", "hisaab", "khata", "हिसाब", "खाता", "ಲೆಕ್ಕ",
              "ಸ್ಟೇಟ್‌ಮೆಂಟ್", "lekka"]
_SEND = ["send", "share", "forward", "need", "want", "give", "bhejo", "bhejiye", "bhej do", "chahiye", "भेजो",
         "भेजिए", "चाहिए", "ಕಳುಹಿಸಿ", "kalisi", "kalsi", "beku", "ಬೇಕು"]
_BALANCE = ["how much", "outstanding", "balance", "pending", "dues", "due amount", "owe", "kitna", "kitne",
            "baaki", "baki", "bakaya", "कितना", "बाकी", "ಎಷ್ಟು", "ಬಾಕಿ", "eshtu", "yeshtu"]
_ORDER = ["order", "book", "need", "want", "send me", "dispatch", "supply", "chahiye", "bhejo", "चाहिए", "ऑर्डर",
          "ಬೇಕು", "ಆರ್ಡರ್", "beku"]
_SMALLTALK = ["hi", "hello", "hey", "namaste", "namaskara", "thanks", "thank you", "thx", "ok", "okay", "good morning",
              "dhanyavad", "shukriya", "नमस्ते", "धन्यवाद", "शुक्रिया", "ನಮಸ್ಕಾರ", "ಧನ್ಯವಾದ"]
_IDENTITY = ["are you a bot", "are you human", "are you a person", "real person", "who is this", "who are you",
             "is this a bot", "robot", "kaun ho", "कौन", "ಯಾರು"]
_UPSET = ["why are you", "irritat", "harass", "disturb", "fed up", "stop bothering", "nonsense", "pareshan",
          "परेशान", "!!", "ತೊಂದರೆ"]
_ITEM_WORDS = ["mcb", "rccb", "isolator", "distribution board", "db", "switch", "socket", "plate", "wire", "cable",
               "changeover", "coil"]


def classify_rules(text: str, today: date, fallback_language: str = "en") -> Understanding:
    lang = detect_language(text, fallback_language)
    refs = tuple(parse.invoice_refs(text))
    pay_refs = parse.payment_refs(text)
    inst = tuple(parse.installments(text, today))
    mentions_item = _has(text, _ITEM_WORDS)
    qty_m = re.search(r"\b(\d{1,5})\s*(?:pcs|pieces|nos|units?|coils?|no\.?)?\s*(?=[a-z]*\s*(?:mcb|rccb|isolator|"
                      r"distribution|db|switch|socket|plate|wire|cable|changeover))", text, re.I)

    if text.strip().lower().strip(".! ") in ("stop", "unsubscribe", "band karo", "बंद करो"):
        intent = "opt_out"
    elif _has(text, _DISPUTE):
        intent = "dispute"
    elif pay_refs or (_has(text, _PAID) and not _has(text, ["will pay", "dunga", "denge", "karunga"])):
        intent = "already_paid"
    elif _has(text, _PROMISE) or (any(i.due for i in inst) and (any(i.amount_paise for i in inst) or _has(
            text, ["pay", "payment", "paisa", "ಪಾವತಿ", "भुगतान"]))):
        intent = "promise_to_pay"
    elif _has(text, _OPT_OUT):
        intent = "opt_out"
    elif _has(text, _STATEMENT):
        intent = "send_statement"
    elif refs or (_has(text, _INVOICE) and _has(text, _SEND)):
        intent = "send_invoice"
    elif mentions_item and _has(text, _ORDER) and qty_m:
        intent = "order_request"
    elif mentions_item:
        intent = "stock_query"
    elif _has(text, _BALANCE):
        intent = "balance_query"
    elif _has(text, _SMALLTALK) or _has(text, _IDENTITY):
        intent = "smalltalk"
    else:
        intent = "unclear"
    return Understanding(
        intent=intent, language=lang, upset=_has(text, _UPSET), invoice_refs=refs,
        payment_ref=pay_refs[0] if pay_refs else "", installments=inst if intent == "promise_to_pay" else (),
        item_query=text if intent in ("stock_query", "order_request") else "",
        qty=int(qty_m.group(1)) if qty_m and intent == "order_request" else None,
        asks_identity=_has(text, _IDENTITY), engine="rules",
    )


# --- LLM engine -----------------------------------------------------------------------------

SYSTEM_PROMPT = """You read WhatsApp messages that dealers send to an Indian electrical distributor's \
payment assistant, and return ONE JSON object. Messages may be English, Hindi, Kannada, or a mix in Latin script.

Fields:
- intent: one of already_paid, promise_to_pay, dispute, send_invoice, send_statement, balance_query, \
stock_query, order_request, opt_out, smalltalk, unclear
  * already_paid: says they have paid / shares a UTR or reference
  * promise_to_pay: says when they will pay (possibly in parts)
  * dispute: complains about an invoice: wrong rate, short or damaged delivery, not ordered, mismatch with PO
  * send_invoice: wants a copy/details of a specific invoice; send_statement: wants their statement/ledger
  * balance_query: asks how much they owe; stock_query: asks availability or rate of an item
  * order_request: wants to order items; opt_out: asks to stop messages
  * smalltalk: greeting, thanks, ok, or asking whether this is a bot; unclear: anything else
- language: en, hi or kn (the language the dealer wrote in; Hinglish counts as hi)
- upset: true if the dealer sounds angry or annoyed
- invoice_refs: list of invoice numbers mentioned, digits only (e.g. "1043")
- payment_ref: UTR/UPI/transaction reference exactly as written, or ""
- installments: for promise_to_pay only, list of {"amount_rupees": number or null, "date": "YYYY-MM-DD" or null, \
"rest": true if this part is "the rest/remaining"}. amount null means the whole pending amount. \
Resolve words like today, tomorrow, Friday, 15th, month end against TODAY. Never invent an amount the dealer \
did not write.
- item_query: for stock_query/order_request, the item words the dealer used (e.g. "16A MCB"), else ""
- qty: quantity for order_request if given, else null
- asks_identity: true if they ask whether they are talking to a bot or a person
Return only the JSON object."""


def _llm_user_prompt(text: str, today: date) -> str:
    return f"TODAY: {today.isoformat()} ({today:%A})\nMESSAGE:\n{text}"


def _from_llm(data: dict, text: str, today: date, rules: Understanding) -> Understanding:
    notes: list[str] = []
    intent = data.get("intent") if data.get("intent") in INTENTS else "unclear"
    language = data.get("language") if data.get("language") in LANGUAGES else rules.language

    # invoice numbers and references must actually be in the message
    digits_in_text = set(parse.invoice_refs(text)) | set(re.findall(r"\d{3,6}", text))
    refs = tuple(str(r) for r in data.get("invoice_refs") or [] if str(r) in digits_in_text)
    pay_ref = str(data.get("payment_ref") or "").strip()
    if pay_ref and pay_ref.upper() not in text.upper():
        notes.append("dropped payment_ref not in message")
        pay_ref = ""

    inst: tuple[parse.Installment, ...] = ()
    if intent == "promise_to_pay":
        written = {f.value for f in parse.find_amounts(text, today)}
        ok, parsed = True, []
        for raw in data.get("installments") or []:
            amt = raw.get("amount_rupees")
            paise = int(round(float(amt) * 100)) if isinstance(amt, (int, float)) else None
            if paise is not None and paise not in written:
                ok = False
                notes.append(f"LLM amount {amt} not found in message")
            due = None
            if raw.get("date"):
                try:
                    due = date.fromisoformat(str(raw["date"]))
                except ValueError:
                    ok = False
            if due is not None and not (today <= due <= today + timedelta(days=MAX_PROMISE_DAYS)):
                ok = False
                notes.append(f"LLM date {due} out of range")
            parsed.append(parse.Installment(paise, due, rest=bool(raw.get("rest")) and paise is None))
        inst = tuple(parsed) if ok and parsed else rules.installments or tuple(parse.installments(text, today))

    qty = data.get("qty")
    qty = int(qty) if isinstance(qty, (int, float)) and str(int(qty)) in text else None
    return Understanding(
        intent=intent, language=language, upset=bool(data.get("upset")), invoice_refs=refs or rules.invoice_refs,
        payment_ref=pay_ref or rules.payment_ref, installments=inst,
        item_query=str(data.get("item_query") or "")[:100] or rules.item_query, qty=qty,
        asks_identity=bool(data.get("asks_identity")) or rules.asks_identity, engine="llm", notes=tuple(notes),
    )


def understand(text: str, today: date, fallback_language: str = "en",
               llm: LLMProvider | None = None) -> Understanding:
    rules = classify_rules(text, today, fallback_language)
    if llm is None:
        return rules
    try:
        data = llm.complete_json(SYSTEM_PROMPT, _llm_user_prompt(text, today))
        u = _from_llm(data, text, today, rules)
        return replace(u, engine=f"llm:{llm.name}")
    except (LLMError, ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        log.warning("LLM understanding failed, using rules: %s", e)
        return replace(rules, notes=rules.notes + (f"llm failed: {e}",))
