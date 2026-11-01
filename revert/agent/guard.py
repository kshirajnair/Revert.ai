"""Last check on every automated reply before it is sent. Any problem blocks the reply and the
message goes to the owner instead.

- Every number in the reply must be a fact a tool read from the DB this turn, or something the
  dealer wrote. Amounts, dates, quantities, invoice numbers, sync times: all of them.
- Digits only in 0-9: a stray Devanagari/Kannada numeral could not be checked, so it is refused.
- No threats or legal words, in English, Hindi or Kannada.
- The reply must be signed as the business's assistant.
"""
from __future__ import annotations

import re

from revert.verticals.collections.replies import ASSISTANT
from revert.verticals.collections.templates import FORBIDDEN_WORDS

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_FOREIGN_DIGITS = re.compile(r"[०-९೦-೯]")
FORBIDDEN_WORDS_INDIC = ("कानूनी", "अदालत", "वकील", "पुलिस", "नोटिस", "मुकदमा", "ಕಾನೂನು", "ನ್ಯಾಯಾಲಯ", "ವಕೀಲ",
                         "ಪೊಲೀಸ್", "ನೋಟಿಸ್", "ದಾವೆ")


def numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM_RE.finditer(text):
        tok = m.group(0).rstrip(",").replace(",", "")
        if tok:
            out.add(tok)
    return out


def check_reply(body: str, facts: list[str], inbound: str, company_name: str, language: str) -> list[str]:
    problems = []
    allowed = numbers(" ".join(facts)) | numbers(inbound) | numbers(company_name)
    unknown = sorted(numbers(body) - allowed)
    if unknown:
        problems.append(f"numbers not from the DB or the dealer: {', '.join(unknown)}")
    if _FOREIGN_DIGITS.search(body):
        problems.append("non-ASCII digits")
    low = body.lower()
    bad = [w for w in FORBIDDEN_WORDS if re.search(rf"\b{re.escape(w)}", low)]
    bad += [w for w in FORBIDDEN_WORDS_INDIC if w in body]
    if bad:
        problems.append(f"forbidden words: {', '.join(bad)}")
    if ASSISTANT.get(language, ASSISTANT["en"]).format(company=company_name) not in body:
        problems.append("not signed as the business's assistant")
    return problems
