"""Bank branch data and account rules for NTU payee records.

NTU stores a 7-digit branch code and its short branch name (e.g.
8080624 / 玉山台大). The data in bank_branches.json comes from NTU's own
bank-code search; the account rules mirror manufedit.asp's form_check.
"""

import json
import os
import re

_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bank_branches.json")

with open(_DATA_FILE, encoding="utf-8") as _f:
    BANK_DATA = json.load(_f)

BANKS = {bank["code"]: bank for bank in BANK_DATA["banks"]}
BRANCHES = {
    code: (bank, name)
    for bank in BANK_DATA["banks"]
    for code, name in bank["branches"]
}


def branch_name(branch_code: str) -> str:
    """NTU's short branch name for a 7-digit branch code, or '' if unknown."""
    entry = BRANCHES.get(str(branch_code).strip())
    return entry[1] if entry else ""


def payee_bank_error(branch_code: str, account_number: str) -> str:
    """Return a user-facing error, or '' when NTU's form will accept them."""
    entry = BRANCHES.get(str(branch_code).strip())
    if not entry:
        return "請選擇銀行分行。"
    bank, _ = entry
    account = str(account_number).strip()
    lengths = bank["account_lengths"]
    if not re.fullmatch(r"\d+", account) or len(account) not in lengths:
        allowed = "或".join(str(n) for n in lengths)
        return f"{bank['label']}帳號必須為 {allowed} 碼數字。"
    return ""
