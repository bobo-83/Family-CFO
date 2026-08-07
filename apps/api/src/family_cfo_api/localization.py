"""#10 phase 4: the prose the API itself emits, in the household's language.

Scope, deliberately narrow: strings the SERVER authors and a person reads —
HTTP error details and the data-export README. Two categories stay English on
purpose:

  * Text addressed to the MODEL (tool coverage notes, grounding rules). The
    advisor already answers in the household's language because its system
    prompt says so (#10 phase 1); translating its instructions would only make
    them harder for the model to follow.
  * Values the client owns (account names, merchant strings) — never ours to
    translate.

Mechanism mirrors the web: the English source IS the key, so a call site reads
naturally and an untranslated string degrades to English rather than to a
missing-key placeholder. No gettext, no dependency — a dict is transparent and
this catalog is small by design.

The language comes from the request's Accept-Language, which the clients set
from the household setting; unknown or absent means English.
"""

from __future__ import annotations

SUPPORTED = ("en", "vi", "lt")

# English source -> {locale: translation}. Lithuanian entries are machine-drafted
# pending a native review, same standing as the web catalog's
# state="needs-review-translation".
_CATALOG: dict[str, dict[str, str]] = {
    # --- auth / session -----------------------------------------------------
    "Invalid credentials": {
        "vi": "Thông tin đăng nhập không đúng",
        "lt": "Neteisingi prisijungimo duomenys",
    },
    "Unauthorized": {"vi": "Chưa được phép", "lt": "Neautorizuota"},
    "Role does not permit this action": {
        "vi": "Vai trò của bạn không cho phép thao tác này",
        "lt": "Jūsų rolė neleidžia atlikti šio veiksmo",
    },
    "This device's pairing is no longer valid": {
        "vi": "Thiết bị này không còn được ghép nối",
        "lt": "Šio įrenginio susiejimas nebegalioja",
    },
    # --- not found ----------------------------------------------------------
    "Household not found": {
        "vi": "Không tìm thấy hộ gia đình",
        "lt": "Namų ūkis nerastas",
    },
    "Account not found": {"vi": "Không tìm thấy tài khoản", "lt": "Sąskaita nerasta"},
    "Goal not found": {"vi": "Không tìm thấy mục tiêu", "lt": "Tikslas nerastas"},
    "Bill not found": {"vi": "Không tìm thấy hóa đơn", "lt": "Sąskaita nerasta"},
    "Transaction not found": {
        "vi": "Không tìm thấy giao dịch",
        "lt": "Operacija nerasta",
    },
    "Contribution not found": {
        "vi": "Không tìm thấy khoản đóng góp",
        "lt": "Įnašas nerastas",
    },
    "Budget not found": {"vi": "Không tìm thấy ngân sách", "lt": "Biudžetas nerastas"},
    "Category not found": {"vi": "Không tìm thấy danh mục", "lt": "Kategorija nerasta"},
    "Member not found": {"vi": "Không tìm thấy thành viên", "lt": "Narys nerastas"},
    # --- conflicts / limits -------------------------------------------------
    "Too many requests": {
        "vi": "Quá nhiều yêu cầu",
        "lt": "Per daug užklausų",
    },
    "A backup is already running": {
        "vi": "Một bản sao lưu đang chạy",
        "lt": "Atsarginė kopija jau kuriama",
    },
    "You cannot revoke the device you are using": {
        "vi": "Bạn không thể thu hồi thiết bị đang dùng",
        "lt": "Negalite atšaukti įrenginio, kurį naudojate",
    },
    "Sealed household is locked": {
        "vi": "Hộ gia đình đã niêm phong đang bị khóa",
        "lt": "Užantspauduotas namų ūkis užrakintas",
    },
    "Request validation failed": {
        "vi": "Yêu cầu không hợp lệ",
        "lt": "Užklausa neatitinka reikalavimų",
    },
    "HTTP error": {"vi": "Lỗi HTTP", "lt": "HTTP klaida"},
}


def normalize(language: str | None) -> str:
    """An Accept-Language header (or bare code) reduced to a supported locale."""
    if not language:
        return "en"
    # "vi-VN,vi;q=0.9,en;q=0.8" -> "vi". Only the first tag is considered: the
    # household has ONE language, so quality-value negotiation would invent a
    # preference the app doesn't model.
    first = language.split(",")[0].split(";")[0].strip().lower()
    base = first.split("-")[0]
    return base if base in SUPPORTED else "en"


def translate(message: str, language: str | None) -> str:
    """The message in `language`, or the English source when untranslated.

    Falling back to the source (never to a key or an error) means a missing
    entry degrades to a sentence the reader can still act on.
    """
    locale = normalize(language)
    if locale == "en":
        return message
    return _CATALOG.get(message, {}).get(locale, message)
