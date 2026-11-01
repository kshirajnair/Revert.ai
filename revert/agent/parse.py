"""Deterministic parsing of what dealers write: amounts, dates, invoice numbers, payment references.

Used twice: by the offline rules classifier, and to check what an LLM extracted. An amount the
LLM returns is accepted only if this parser also finds it in the message, so the agent never acts
on a figure the dealer did not write.

Handles English, romanised Hindi/Kannada and the native scripts for the common words:
"50k", "₹50,000", "1.5 lakh", "50 hazar", "Friday", "kal", "ನಾಳೆ", "15th", "15 Nov", "month end".
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

# --- invoice numbers and payment references -------------------------------------------------

_INV_WORD_RE = re.compile(r"\b(?:inv(?:oice)?|bill)\s*(?:no\.?|number|#)?\s*[-#:./]?\s*(\d{3,6})\b", re.I)
_INV_PREFIX_RE = re.compile(r"\binv[-/ ]?(\d{3,6})\b", re.I)
_REF_WORD_RE = re.compile(
    r"\b(?:utr|ref(?:erence)?|txn|transaction|upi|neft|rtgs|imps)\b\s*(?:no\.?|number|id|ref)?\s*[:#-]?\s*"
    r"([A-Z0-9]{8,22})\b", re.I)
_REF_BARE_RE = re.compile(r"\b([A-Z]{4}\d{8,18}|\d{12,22})\b")


def invoice_refs(text: str) -> list[str]:
    """Digits of invoice numbers mentioned, e.g. 'INV-1043' or 'bill 1043' -> ['1043']."""
    found = [m.group(1) for r in (_INV_WORD_RE, _INV_PREFIX_RE) for m in r.finditer(text)]
    return list(dict.fromkeys(found))


def payment_refs(text: str) -> list[str]:
    """UTR / UPI / transaction references (8+ characters with at least 6 digits)."""
    found = [m.group(1) for r in (_REF_WORD_RE, _REF_BARE_RE) for m in r.finditer(text)]
    found = [f.upper() for f in found if sum(c.isdigit() for c in f) >= 6]
    return list(dict.fromkeys(found))


# --- dates ----------------------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_ORD = r"(?:st|nd|rd|th)"

# word -> days from today; matched as whole words (latin) or substrings (Devanagari/Kannada)
_RELATIVE = {
    0: ["today", "now", "right away", "aaj", "abhi", "ivattu", "indu", "iga", "आज", "अभी", "ಇವತ್ತು", "ಇಂದು", "ಈಗ"],
    1: ["tomorrow", "tmrw", "kal", "naale", "nale", "कल", "ನಾಳೆ"],
    2: ["day after tomorrow", "parso", "parson", "naadiddu", "nadiddu", "परसों", "ನಾಡಿದ್ದು"],
}
_WEEKDAYS = [  # Monday first, like date.weekday()
    ["monday", "mon", "somvar", "somavara", "सोमवार", "ಸೋಮವಾರ"],
    ["tuesday", "tue", "tues", "mangalvar", "mangalavara", "मंगलवार", "ಮಂಗಳವಾರ"],
    ["wednesday", "wed", "budhvar", "budhavara", "बुधवार", "ಬುಧವಾರ"],
    ["thursday", "thu", "thurs", "guruvar", "guruvara", "गुरुवार", "ಗುರುವಾರ"],
    ["friday", "fri", "shukravar", "shukravara", "शुक्रवार", "ಶುಕ್ರವಾರ"],
    ["saturday", "sat", "shanivar", "shanivara", "शनिवार", "ಶನಿವಾರ"],
    ["sunday", "sun", "ravivar", "itvaar", "bhanuvara", "रविवार", "ಭಾನುವಾರ"],
]
_NEXT_WEEK = ["next week", "agle hafte", "agle week", "mundina vaara", "अगले हफ्ते", "अगले सप्ताह", "ಮುಂದಿನ ವಾರ"]
_MONTH_END = ["month end", "end of the month", "end of month", "month-end", "mahine ke end", "mahine ke aakhir",
              "महीने के अंत", "महीने के आखिर", "ತಿಂಗಳ ಕೊನೆ"]
_DAY_WORD = r"(?:tarikh|tareekh|tareek|तारीख|ತಾರೀಖು|ನೇ ತಾರೀಖು)"


@dataclass(frozen=True)
class Found:
    value: object  # date or paise
    start: int
    end: int


def _is_latin(word: str) -> bool:
    return all(ord(c) < 128 for c in word)


def _find_words(text: str, words: list[str]) -> list[tuple[int, int]]:
    low = text.lower()
    spans = []
    for w in sorted(words, key=len, reverse=True):
        pattern = rf"\b{re.escape(w)}\b" if _is_latin(w) else re.escape(w)
        for m in re.finditer(pattern, low):
            if not any(s <= m.start() < e for s, e in spans):  # longest phrase wins
                spans.append((m.start(), m.end()))
    return spans


def _day_of_month(day: int, today: date) -> date | None:
    """'15th' means the next 15th: this month if not yet past, else next month."""
    y, m = today.year, today.month
    for _ in range(2):
        if day <= calendar.monthrange(y, m)[1]:
            d = date(y, m, day)
            if d >= today:
                return d
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None


def _day_month(day: int, month: int, today: date, year: int | None = None) -> date | None:
    y = year or today.year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if year is None and d < today - timedelta(days=7):  # "5 Jan" said in December means next year
        try:
            d = date(y + 1, month, day)
        except ValueError:
            return None
    return d


def find_dates(text: str, today: date) -> list[Found]:
    low = text.lower()
    out: list[Found] = []

    def add(d: date | None, s: int, e: int) -> None:
        if d and not any(f.start <= s < f.end or s <= f.start < e for f in out):
            out.append(Found(d, s, e))

    for m in re.finditer(rf"\b(\d{{1,2}}){_ORD}?\s*(?:of\s+)?{_MONTH}\b", low):
        add(_day_month(int(m.group(1)), MONTHS[m.group(2)[:3]], today), m.start(), m.end())
    for m in re.finditer(rf"\b{_MONTH}\s*(\d{{1,2}}){_ORD}?\b", low):
        add(_day_month(int(m.group(2)), MONTHS[m.group(1)[:3]], today), m.start(), m.end())
    # 15/11, 15-11-26, 15.11.2026 (a dot only with a year, so "1.5 lakh" stays an amount)
    for m in re.finditer(r"\b(\d{1,2})(?:[/-](\d{1,2})(?:[/-](\d{2,4}))?|\.(\d{1,2})\.(\d{2,4}))\b", low):
        month = int(m.group(2) or m.group(4))
        year = m.group(3) or m.group(5)
        year = int(year) + (2000 if len(year) == 2 else 0) if year else None
        add(_day_month(int(m.group(1)), month, today, year) if 1 <= month <= 12 else None, m.start(), m.end())
    for m in re.finditer(rf"\b(\d{{1,2}})(?:{_ORD}\b|\s*{_DAY_WORD})", low):
        add(_day_of_month(int(m.group(1)), today), m.start(), m.end())
    for m in re.finditer(r"\b(?:on|by|before)\s+(?:the\s+)?(\d{1,2})\b(?!\s*(?:k|%|days?|units?|nos|pcs))", low):
        add(_day_of_month(int(m.group(1)), today), m.start(1), m.end(1))

    for days, words in sorted(_RELATIVE.items(), reverse=True):  # "day after tomorrow" before "tomorrow"
        for s, e in _find_words(text, words):
            add(today + timedelta(days=days), s, e)
    for wd, words in enumerate(_WEEKDAYS):
        for s, e in _find_words(text, words):
            add(today + timedelta(days=(wd - today.weekday()) % 7), s, e)
    for s, e in _find_words(text, _NEXT_WEEK):
        add(today + timedelta(days=7 - today.weekday()), s, e)  # next Monday
    for s, e in _find_words(text, _MONTH_END):
        add(date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]), s, e)
    return sorted(out, key=lambda f: f.start)


# --- amounts --------------------------------------------------------------------------------

_MULT = {"k": 1_000, "thousand": 1_000, "hazar": 1_000, "hazaar": 1_000, "hajar": 1_000, "हजार": 1_000,
         "हज़ार": 1_000, "ಸಾವಿರ": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
         "लाख": 100_000, "ಲಕ್ಷ": 100_000}
_AMOUNT_RE = re.compile(
    r"(?P<cur>₹|\brs\.?|\binr\b)?\s*(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*"
    r"(?P<mult>k\b|thousand\b|hazaa?r\b|hajar\b|हज़ार|हजार|ಸಾವಿರ|lakhs?\b|lacs?\b|लाख|ಲಕ್ಷ)?"
    r"(?P<cur2>\s*(?:rs\b|rupees?\b|/-|रुपये|ರೂಪಾಯಿ))?", re.I)
_REST = ["rest", "remaining", "balance", "baaki", "baki", "bakaya", "बाकी", "बाक़ी", "ಉಳಿದ", "ಬಾಕಿ"]


def find_amounts(text: str, today: date | None = None) -> list[Found]:
    """Money amounts in paise. Plain numbers count only from ₹100 up, and invoice numbers,
    references, dates and quantities are masked first."""
    masked = list(text)

    def mask(s: int, e: int) -> None:
        masked[s:e] = " " * (e - s)

    for r in (_INV_WORD_RE, _INV_PREFIX_RE, _REF_WORD_RE, _REF_BARE_RE):
        for m in r.finditer(text):
            mask(m.start(), m.end())
    for f in find_dates(text, today or date.today()):
        mask(f.start, f.end)
    for m in re.finditer(r"\b\d+\s*(?:pcs|pieces|nos|units?|coils?|boxes|qty)\b", text, re.I):
        mask(m.start(), m.end())
    clean = "".join(masked)

    out = []
    for m in _AMOUNT_RE.finditer(clean):
        num = m.group("num").replace(",", "")
        mult = _MULT.get((m.group("mult") or "").lower(), 1)
        value = float(num) * mult
        marked = m.group("cur") or m.group("cur2") or m.group("mult")
        if not marked and value < 100:
            continue
        out.append(Found(int(round(value * 100)), m.start("num"), m.end()))
    return out


def rest_spans(text: str) -> list[tuple[int, int]]:
    return _find_words(text, _REST)


# --- promise installments -------------------------------------------------------------------

@dataclass(frozen=True)
class Installment:
    amount_paise: int | None  # None: the whole pending amount (or what remains, if `rest`)
    due: date | None
    rest: bool = False


_SPLIT_RE = re.compile(r"[,;]|\band\b|\bthen\b|\baur\b|\bphir\b|और|फिर|ಮತ್ತು|ಆಮೇಲೆ|\n", re.I)


def installments(text: str, today: date) -> list[Installment]:
    """'50k now, rest on 15th' -> [(50000, today), (rest, 15th)]. Dates missing stay None."""
    bounds, last = [], 0
    for m in _SPLIT_RE.finditer(text):
        bounds.append((last, m.start()))
        last = m.end()
    bounds.append((last, len(text)))

    amounts, dates, rests = find_amounts(text, today), find_dates(text, today), rest_spans(text)
    out = []
    for s, e in bounds:
        a = next((f.value for f in amounts if s <= f.start < e), None)
        d = next((f.value for f in dates if s <= f.start < e), None)
        r = any(s <= rs < e for rs, _ in rests)
        if a is not None or d is not None or r:
            out.append(Installment(a, d, rest=r and a is None))
    # "will pay 20000 ... by friday" split oddly: one amount and one date overall belong together
    if len(amounts) == 1 and len(dates) == 1 and len(out) == 2 and not rests:
        return [Installment(amounts[0].value, dates[0].value)]
    return out
