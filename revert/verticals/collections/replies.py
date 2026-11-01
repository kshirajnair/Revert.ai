"""Agent reply texts in English, Hindi and Kannada.

Rules (checked by tests and by the agent's guard on every reply):
- every reply is signed as "<Business>'s assistant" in the reply's language;
- no digits in the template text itself: every number is a placeholder filled from a tool result;
- money and stock replies end with the data's last-synced time;
- no threats, no legal words, in any language.

The Hindi and Kannada texts are plain, polite business language. Have a native speaker review
them before going live.
"""
from __future__ import annotations

ASSISTANT = {
    "en": "{company}'s assistant",
    "hi": "{company} का असिस्टेंट",
    "kn": "{company} ಸಂಸ್ಥೆಯ ಸಹಾಯಕ",
}

T: dict[str, dict[str, str]] = {
    "hello": {
        "en": "Namaste {buyer},",
        "hi": "नमस्ते {buyer} जी,",
        "kn": "ನಮಸ್ಕಾರ {buyer} ಅವರೇ,",
    },
    "sign": {"en": "— {assistant}", "hi": "— {assistant}", "kn": "— {assistant}"},
    "synced": {
        "en": "Amounts as per our books, updated {synced}.",
        "hi": "राशि हमारे खातों के अनुसार, {synced} तक अपडेट।",
        "kn": "ಮೊತ್ತಗಳು ನಮ್ಮ ಲೆಕ್ಕದ ಪ್ರಕಾರ, {synced} ರವರೆಗೆ ಅಪ್‌ಡೇಟ್ ಆಗಿದೆ.",
    },
    "stock_synced": {
        "en": "Stock as per our books, updated {synced}.",
        "hi": "स्टॉक हमारे रिकॉर्ड के अनुसार, {synced} तक अपडेट।",
        "kn": "ಸ್ಟಾಕ್ ನಮ್ಮ ದಾಖಲೆಯ ಪ್ರಕಾರ, {synced} ರವರೆಗೆ ಅಪ್‌ಡೇಟ್ ಆಗಿದೆ.",
    },
    "upset": {
        "en": "Sorry for the trouble.",
        "hi": "असुविधा के लिए क्षमा करें।",
        "kn": "ತೊಂದರೆಗಾಗಿ ಕ್ಷಮಿಸಿ.",
    },
    "handoff": {
        "en": "Thank you for your message. I've passed it to the team and someone will get back to you shortly.",
        "hi": "आपके संदेश के लिए धन्यवाद। मैंने इसे टीम को भेज दिया है, जल्द ही कोई आपसे संपर्क करेगा।",
        "kn": "ನಿಮ್ಮ ಸಂದೇಶಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ಇದನ್ನು ನಮ್ಮ ತಂಡಕ್ಕೆ ತಿಳಿಸಿದ್ದೇನೆ, ಶೀಘ್ರದಲ್ಲೇ ಯಾರಾದರೂ ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ.",
    },
    # --- already paid ---
    "paid_found": {
        "en": "Thank you! We have received these payments from you:",
        "hi": "धन्यवाद! आपसे ये भुगतान हमें मिल गए हैं:",
        "kn": "ಧನ್ಯವಾದಗಳು! ನಿಮ್ಮಿಂದ ಈ ಪಾವತಿಗಳು ನಮಗೆ ತಲುಪಿವೆ:",
    },
    "receipt_line": {
        "en": "• {amount} on {date} ({ref})",
        "hi": "• {date} को {amount} ({ref})",
        "kn": "• {date} ರಂದು {amount} ({ref})",
    },
    "still_pending": {
        "en": "Still pending: {total}.",
        "hi": "अभी बाकी: {total}।",
        "kn": "ಇನ್ನೂ ಬಾಕಿ: {total}.",
    },
    "nothing_pending": {
        "en": "Nothing is pending from your side now. Thank you!",
        "hi": "अब आपकी ओर से कुछ भी बाकी नहीं है। धन्यवाद!",
        "kn": "ಈಗ ನಿಮ್ಮ ಕಡೆಯಿಂದ ಏನೂ ಬಾಕಿ ಇಲ್ಲ. ಧನ್ಯವಾದಗಳು!",
    },
    "paid_not_found": {
        "en": "Thank you for letting us know. I can't see this payment in our books yet. Could you please share "
              "the UTR / transaction reference number or a screenshot of the payment? I've also informed the team "
              "so they can check, and reminders are on hold meanwhile.",
        "hi": "बताने के लिए धन्यवाद। यह भुगतान अभी हमारे खातों में नहीं दिख रहा है। कृपया भुगतान का UTR / ट्रांज़ैक्शन "
              "रेफरेंस नंबर या स्क्रीनशॉट भेजें। मैंने टीम को भी बता दिया है ताकि वे जाँच कर सकें, तब तक रिमाइंडर रोक "
              "दिए गए हैं।",
        "kn": "ತಿಳಿಸಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ಈ ಪಾವತಿ ಇನ್ನೂ ನಮ್ಮ ಲೆಕ್ಕದಲ್ಲಿ ಕಾಣುತ್ತಿಲ್ಲ. ದಯವಿಟ್ಟು ಪಾವತಿಯ UTR / ವಹಿವಾಟು "
              "ರೆಫರೆನ್ಸ್ ಸಂಖ್ಯೆ ಅಥವಾ ಸ್ಕ್ರೀನ್‌ಶಾಟ್ ಕಳುಹಿಸಿ. ಪರಿಶೀಲಿಸಲು ತಂಡಕ್ಕೂ ತಿಳಿಸಿದ್ದೇನೆ, ಅಲ್ಲಿಯವರೆಗೆ ರಿಮೈಂಡರ್‌ಗಳನ್ನು "
              "ನಿಲ್ಲಿಸಲಾಗಿದೆ.",
    },
    "ref_not_found": {
        "en": "Thank you for the reference {ref}. It isn't in our books yet; payments can take a little time to "
              "show up. I've passed it to the team to check, and reminders are on hold meanwhile.",
        "hi": "रेफरेंस {ref} के लिए धन्यवाद। यह अभी हमारे खातों में नहीं दिखा है; भुगतान दिखने में थोड़ा समय लग सकता "
              "है। मैंने इसे जाँच के लिए टीम को भेज दिया है, तब तक रिमाइंडर रोक दिए गए हैं।",
        "kn": "ರೆಫರೆನ್ಸ್ {ref} ಕಳುಹಿಸಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ಇದು ಇನ್ನೂ ನಮ್ಮ ಲೆಕ್ಕದಲ್ಲಿ ಕಾಣುತ್ತಿಲ್ಲ; ಪಾವತಿ ಕಾಣಲು ಸ್ವಲ್ಪ "
              "ಸಮಯ ಆಗಬಹುದು. ಪರಿಶೀಲನೆಗಾಗಿ ತಂಡಕ್ಕೆ ಕಳುಹಿಸಿದ್ದೇನೆ, ಅಲ್ಲಿಯವರೆಗೆ ರಿಮೈಂಡರ್‌ಗಳನ್ನು ನಿಲ್ಲಿಸಲಾಗಿದೆ.",
    },
    # --- invoices, statement, balance ---
    "invoice_head": {
        "en": "Invoice {number} dated {date}:",
        "hi": "बिल {number}, दिनांक {date}:",
        "kn": "ಬಿಲ್ {number}, ದಿನಾಂಕ {date}:",
    },
    "invoice_line": {
        "en": "• {item}: {qty} {unit} × {rate} = {amount}",
        "hi": "• {item}: {qty} {unit} × {rate} = {amount}",
        "kn": "• {item}: {qty} {unit} × {rate} = {amount}",
    },
    "invoice_totals": {
        "en": "Subtotal {sub}, GST {tax}, Total {total}.",
        "hi": "उप-योग {sub}, GST {tax}, कुल {total}।",
        "kn": "ಉಪ-ಮೊತ್ತ {sub}, GST {tax}, ಒಟ್ಟು {total}.",
    },
    "invoice_paid": {
        "en": "This invoice is fully paid.",
        "hi": "यह बिल पूरा चुका दिया गया है।",
        "kn": "ಈ ಬಿಲ್ ಪೂರ್ತಿ ಪಾವತಿಯಾಗಿದೆ.",
    },
    "invoice_open": {
        "en": "Paid so far {paid}; pending {balance}, due on {due}.",
        "hi": "अब तक भुगतान {paid}; बाकी {balance}, देय तिथि {due}।",
        "kn": "ಇಲ್ಲಿಯವರೆಗೆ ಪಾವತಿ {paid}; ಬಾಕಿ {balance}, ಪಾವತಿ ದಿನಾಂಕ {due}.",
    },
    "invoice_not_found": {
        "en": "I couldn't find that invoice number in your account.",
        "hi": "आपके खाते में यह बिल नंबर नहीं मिला।",
        "kn": "ನಿಮ್ಮ ಖಾತೆಯಲ್ಲಿ ಈ ಬಿಲ್ ಸಂಖ್ಯೆ ಸಿಗಲಿಲ್ಲ.",
    },
    "statement_head": {
        "en": "Your open invoices:",
        "hi": "आपके बकाया बिल:",
        "kn": "ನಿಮ್ಮ ಬಾಕಿ ಬಿಲ್‌ಗಳು:",
    },
    "due_line": {
        "en": "• {number} dated {date}: {balance}, due {due}",
        "hi": "• {number}, दिनांक {date}: {balance}, देय तिथि {due}",
        "kn": "• {number}, ದಿನಾಂಕ {date}: {balance}, ಪಾವತಿ ದಿನಾಂಕ {due}",
    },
    "total": {
        "en": "Total pending: {total}",
        "hi": "कुल बाकी: {total}",
        "kn": "ಒಟ್ಟು ಬಾಕಿ: {total}",
    },
    "last_payments": {
        "en": "Last payments received:",
        "hi": "पिछले प्राप्त भुगतान:",
        "kn": "ಇತ್ತೀಚೆಗೆ ಬಂದ ಪಾವತಿಗಳು:",
    },
    "no_dues": {
        "en": "You have no pending invoices with us. Thank you!",
        "hi": "आपका कोई भी बिल बाकी नहीं है। धन्यवाद!",
        "kn": "ನಿಮ್ಮ ಯಾವುದೇ ಬಿಲ್ ಬಾಕಿ ಇಲ್ಲ. ಧನ್ಯವಾದಗಳು!",
    },
    # --- promises ---
    "promise_logged": {
        "en": "Thank you, noted:",
        "hi": "धन्यवाद, नोट कर लिया है:",
        "kn": "ಧನ್ಯವಾದಗಳು, ಗಮನಿಸಿಕೊಂಡಿದ್ದೇನೆ:",
    },
    "promise_amount": {
        "en": "• {amount} by {date}",
        "hi": "• {date} तक {amount}",
        "kn": "• {date} ರೊಳಗೆ {amount}",
    },
    "promise_full": {
        "en": "• the full pending amount, {amount}, by {date}",
        "hi": "• पूरी बाकी राशि {amount}, {date} तक",
        "kn": "• ಪೂರ್ತಿ ಬಾಕಿ ಮೊತ್ತ {amount}, {date} ರೊಳಗೆ",
    },
    "promise_rest": {
        "en": "• the remaining {amount} by {date}",
        "hi": "• बाकी {amount}, {date} तक",
        "kn": "• ಉಳಿದ {amount}, {date} ರೊಳಗೆ",
    },
    "promise_hold": {
        "en": "We won't send reminders until then.",
        "hi": "तब तक हम रिमाइंडर नहीं भेजेंगे।",
        "kn": "ಅಲ್ಲಿಯವರೆಗೆ ರಿಮೈಂಡರ್ ಕಳುಹಿಸುವುದಿಲ್ಲ.",
    },
    "promise_need_date": {
        "en": "Thank you! By which date can we expect the payment?",
        "hi": "धन्यवाद! भुगतान किस तारीख तक हो पाएगा?",
        "kn": "ಧನ್ಯವಾದಗಳು! ಯಾವ ದಿನಾಂಕದೊಳಗೆ ಪಾವತಿ ಮಾಡುತ್ತೀರಿ?",
    },
    "promise_followup": {
        "en": "You had mentioned paying {amount} by {date}. We haven't received it in our books yet. Could you "
              "share an update, or the payment reference if you have already paid?",
        "hi": "आपने {date} तक {amount} भुगतान करने की बात कही थी। यह अभी हमारे खातों में नहीं आया है। कृपया बताएँ कि "
              "भुगतान कब तक होगा, या अगर कर दिया है तो उसका रेफरेंस नंबर भेजें।",
        "kn": "{date} ರೊಳಗೆ {amount} ಪಾವತಿಸುವುದಾಗಿ ತಿಳಿಸಿದ್ದಿರಿ. ಅದು ಇನ್ನೂ ನಮ್ಮ ಲೆಕ್ಕದಲ್ಲಿ ಬಂದಿಲ್ಲ. ದಯವಿಟ್ಟು ಪಾವತಿ "
              "ಯಾವಾಗ ಆಗುತ್ತದೆ ಎಂದು ತಿಳಿಸಿ, ಅಥವಾ ಈಗಾಗಲೇ ಪಾವತಿಸಿದ್ದರೆ ಅದರ ರೆಫರೆನ್ಸ್ ಸಂಖ್ಯೆ ಕಳುಹಿಸಿ.",
    },
    # --- disputes ---
    "dispute_invoice": {
        "en": "Sorry about this. I've recorded your concern about {number} and passed it to the team to check. "
              "We won't send reminders for {number} until it is sorted out.",
        "hi": "इसके लिए खेद है। मैंने {number} के बारे में आपकी शिकायत दर्ज कर ली है और टीम को जाँच के लिए भेज दी है। "
              "जब तक यह सुलझ नहीं जाता, {number} के लिए रिमाइंडर नहीं भेजे जाएँगे।",
        "kn": "ಇದಕ್ಕಾಗಿ ವಿಷಾದಿಸುತ್ತೇವೆ. {number} ಬಗ್ಗೆ ನಿಮ್ಮ ದೂರನ್ನು ದಾಖಲಿಸಿ, ಪರಿಶೀಲನೆಗಾಗಿ ತಂಡಕ್ಕೆ ಕಳುಹಿಸಿದ್ದೇನೆ. "
              "ಇದು ಬಗೆಹರಿಯುವವರೆಗೆ {number} ಗೆ ರಿಮೈಂಡರ್ ಕಳುಹಿಸುವುದಿಲ್ಲ.",
    },
    "dispute_general": {
        "en": "Sorry about this. I've recorded your concern and passed it to the team to check. We won't send you "
              "payment reminders until it is sorted out.",
        "hi": "इसके लिए खेद है। मैंने आपकी शिकायत दर्ज कर ली है और टीम को जाँच के लिए भेज दी है। जब तक यह सुलझ नहीं "
              "जाता, आपको भुगतान रिमाइंडर नहीं भेजे जाएँगे।",
        "kn": "ಇದಕ್ಕಾಗಿ ವಿಷಾದಿಸುತ್ತೇವೆ. ನಿಮ್ಮ ದೂರನ್ನು ದಾಖಲಿಸಿ, ಪರಿಶೀಲನೆಗಾಗಿ ತಂಡಕ್ಕೆ ಕಳುಹಿಸಿದ್ದೇನೆ. ಇದು ಬಗೆಹರಿಯುವವರೆಗೆ "
              "ನಿಮಗೆ ಪಾವತಿ ರಿಮೈಂಡರ್ ಕಳುಹಿಸುವುದಿಲ್ಲ.",
    },
    # --- stock and orders ---
    "stock_head": {
        "en": "Here's what we have:",
        "hi": "हमारे पास उपलब्ध:",
        "kn": "ನಮ್ಮಲ್ಲಿ ಲಭ್ಯವಿರುವುದು:",
    },
    "stock_line": {
        "en": "• {item}: {qty} {unit} in stock, rate {rate}/{unit} + GST",
        "hi": "• {item}: स्टॉक में {qty} {unit}, रेट {rate}/{unit} + GST",
        "kn": "• {item}: ಸ್ಟಾಕ್‌ನಲ್ಲಿ {qty} {unit}, ದರ {rate}/{unit} + GST",
    },
    "stock_out": {
        "en": "• {item}: currently out of stock (rate {rate}/{unit} + GST)",
        "hi": "• {item}: अभी स्टॉक में नहीं है (रेट {rate}/{unit} + GST)",
        "kn": "• {item}: ಸದ್ಯಕ್ಕೆ ಸ್ಟಾಕ್ ಇಲ್ಲ (ದರ {rate}/{unit} + GST)",
    },
    "stock_not_found": {
        "en": "I couldn't find that item in our stock list. I've asked the team to reply.",
        "hi": "यह आइटम हमारी स्टॉक सूची में नहीं मिला। मैंने टीम से जवाब देने को कहा है।",
        "kn": "ಈ ಐಟಂ ನಮ್ಮ ಸ್ಟಾಕ್ ಪಟ್ಟಿಯಲ್ಲಿ ಸಿಗಲಿಲ್ಲ. ಉತ್ತರಿಸಲು ತಂಡಕ್ಕೆ ತಿಳಿಸಿದ್ದೇನೆ.",
    },
    "order_noted": {
        "en": "Thank you for the order. I've passed it to the team to confirm quantity, price and dispatch.",
        "hi": "ऑर्डर के लिए धन्यवाद। मात्रा, रेट और डिस्पैच की पुष्टि के लिए मैंने इसे टीम को भेज दिया है।",
        "kn": "ಆರ್ಡರ್‌ಗಾಗಿ ಧನ್ಯವಾದಗಳು. ಪ್ರಮಾಣ, ದರ ಮತ್ತು ರವಾನೆ ಖಚಿತಪಡಿಸಲು ತಂಡಕ್ಕೆ ಕಳುಹಿಸಿದ್ದೇನೆ.",
    },
    # --- other ---
    "opt_out": {
        "en": "Understood. I've stopped automatic payment reminders to you and informed the team.",
        "hi": "समझ गए। मैंने आपको स्वचालित भुगतान रिमाइंडर भेजना बंद कर दिया है और टीम को बता दिया है।",
        "kn": "ಅರ್ಥವಾಯಿತು. ನಿಮಗೆ ಸ್ವಯಂಚಾಲಿತ ಪಾವತಿ ರಿಮೈಂಡರ್‌ಗಳನ್ನು ನಿಲ್ಲಿಸಿ, ತಂಡಕ್ಕೆ ತಿಳಿಸಿದ್ದೇನೆ.",
    },
    "smalltalk": {
        "en": "Thank you! I can help with your pending invoices, statement, payment updates, and stock or rates. "
              "Just type your question.",
        "hi": "धन्यवाद! मैं आपके बकाया बिल, स्टेटमेंट, भुगतान की जानकारी और स्टॉक या रेट में मदद कर सकता हूँ। बस अपना "
              "सवाल लिखें।",
        "kn": "ಧನ್ಯವಾದಗಳು! ನಿಮ್ಮ ಬಾಕಿ ಬಿಲ್‌ಗಳು, ಸ್ಟೇಟ್‌ಮೆಂಟ್, ಪಾವತಿ ಮಾಹಿತಿ ಮತ್ತು ಸ್ಟಾಕ್ ಅಥವಾ ದರದ ಬಗ್ಗೆ ಸಹಾಯ "
              "ಮಾಡಬಲ್ಲೆ. ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ.",
    },
    "identity": {
        "en": "I'm {company}'s automated assistant, not a person. For anything I can't handle, the team will reply "
              "to you directly.",
        "hi": "मैं {company} का स्वचालित असिस्टेंट हूँ, कोई व्यक्ति नहीं। जो मैं न कर सकूँ, उसके लिए टीम आपको सीधे "
              "जवाब देगी।",
        "kn": "ನಾನು {company} ಸಂಸ್ಥೆಯ ಸ್ವಯಂಚಾಲಿತ ಸಹಾಯಕ, ವ್ಯಕ್ತಿಯಲ್ಲ. ನನಗೆ ಮಾಡಲಾಗದ ವಿಷಯಗಳಿಗೆ ತಂಡವೇ ನೇರವಾಗಿ "
              "ಉತ್ತರಿಸುತ್ತದೆ.",
    },
}


class Composer:
    """Builds one reply: greeting, optional apology, body lines, synced line, signature."""

    def __init__(self, language: str, company: str, buyer: str, upset: bool = False):
        self.lang = language if language in ASSISTANT else "en"
        self.company, self.buyer = company, buyer
        self.lines: list[str] = [self.t("hello", buyer=buyer)]
        if upset:
            self.lines.append(self.t("upset"))
        self.synced_line = ""

    def t(self, key: str, **kw) -> str:
        return T[key][self.lang].format(**kw)

    def say(self, key: str, **kw) -> Composer:
        self.lines.append(self.t(key, **kw))
        return self

    def raw(self, text: str) -> Composer:
        self.lines.append(text)
        return self

    def synced(self, synced: str, stock: bool = False) -> Composer:
        self.synced_line = self.t("stock_synced" if stock else "synced", synced=synced)
        return self

    @property
    def assistant(self) -> str:
        return ASSISTANT[self.lang].format(company=self.company)

    def text(self) -> str:
        parts = list(self.lines)
        if self.synced_line:
            parts.append(self.synced_line)
        parts.append(self.t("sign", assistant=self.assistant))
        return "\n".join(parts)
