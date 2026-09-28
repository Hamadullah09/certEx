"""What the three kinds of certificate say about themselves, in English and Urdu.

Weights say how much a phrase proves, not how often it appears:

* **3 - the title.** "Certificate of Birth" only appears on a birth certificate.
* **2 - a field only this kind has.** "Cause of Death", "Bride's Name", "Name of Child".
* **1 - a field this kind usually has.** "Date of Birth" is on all three, so on its own
  it decides nothing; it only breaks a tie between two otherwise equal readings.

The Urdu terms are the ones printed on Punjab and Sindh forms. They are matched after
normalisation, so the Arabic look-alike letters a PDF hands back have already been
folded to the letters a person types.
"""

from __future__ import annotations

from typing import Final

from certex.enums import CertificateType

__all__ = ["KEYWORDS", "TITLE_WEIGHT"]

TITLE_WEIGHT: Final = 3.0
"""The weight of a certificate's own title: enough on its own to classify a page."""

KEYWORDS: Final[dict[CertificateType, tuple[tuple[str, float], ...]]] = {
    CertificateType.BIRTH: (
        ("certificate of birth", 3.0),
        ("birth certificate", 3.0),
        ("registration of birth", 3.0),
        ("پیدائش کا سرٹیفکیٹ", 3.0),
        ("برتھ سرٹیفکیٹ", 3.0),
        ("name of child", 2.0),
        ("child's name", 2.0),
        ("place of birth", 2.0),
        ("time of birth", 2.0),
        ("بچے کا نام", 2.0),
        ("جائے پیدائش", 2.0),
        ("تاریخ پیدائش", 1.0),
        ("date of birth", 1.0),
        ("father's name", 1.0),
        ("mother's name", 1.0),
        ("والد کا نام", 1.0),
        ("والدہ کا نام", 1.0),
    ),
    CertificateType.DEATH: (
        ("certificate of death", 3.0),
        ("death certificate", 3.0),
        ("registration of death", 3.0),
        ("وفات کا سرٹیفکیٹ", 3.0),
        ("موت کا سرٹیفکیٹ", 3.0),
        ("cause of death", 2.0),
        ("date of death", 2.0),
        ("place of death", 2.0),
        ("name of deceased", 2.0),
        ("deceased", 2.0),
        ("age at death", 2.0),
        ("تاریخ وفات", 2.0),
        ("جائے وفات", 2.0),
        ("متوفی", 2.0),
        ("وجہ وفات", 2.0),
        ("spouse's name", 1.0),
        ("informant", 1.0),
    ),
    CertificateType.MARRIAGE: (
        ("certificate of marriage", 3.0),
        ("marriage certificate", 3.0),
        ("nikah nama", 3.0),
        ("nikahnama", 3.0),
        ("registration of marriage", 3.0),
        ("نکاح نامہ", 3.0),
        ("نکاحنامہ", 3.0),
        ("شادی کا سرٹیفکیٹ", 3.0),
        ("groom", 2.0),
        ("bride", 2.0),
        ("date of marriage", 2.0),
        ("place of marriage", 2.0),
        ("dower", 2.0),
        ("mehr", 2.0),
        ("witness", 1.0),
        ("officiant", 1.0),
        ("دولہا", 2.0),
        ("دلہن", 2.0),
        ("حق مہر", 2.0),
        ("تاریخ نکاح", 2.0),
        ("گواہ", 1.0),
    ),
}
