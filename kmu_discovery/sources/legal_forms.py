"""Rechtsformcodes nach eCH-0097 - gemeinsam fuer alle Register-Quellen.

Die Codes und Bezeichnungen wurden am 2026-09-16 live aus dem LINDAS-Graphen
``https://lindas.admin.ch/lindas-ech`` gelesen (Termset
``https://ld.admin.ch/ech/97/legalforms``); die Zefix-REST-API fuehrt denselben
Code als ``LegalForm.uid``. Die Tabelle gehoert zu keiner Quelle, deshalb
liegt sie hier und nicht im LINDAS- oder Zefix-Client.
"""

from __future__ import annotations

from collections.abc import Mapping

from kmu_discovery.models import Rechtsform

__all__ = ["LEGAL_FORM_CODES", "legal_form_codes_for", "rechtsform_from_code"]

#: eCH-0097-Rechtsformcodes -> Modell-Rechtsform. Alle 15 im Zefix-Graphen
#: vorkommenden Codes sind abgedeckt; Codes ausserhalb des Handelsregisters
#: (02xx-05xx) kommen dort nicht vor und bleiben UNBEKANNT.
LEGAL_FORM_CODES: Mapping[str, Rechtsform] = {
    "0101": Rechtsform.EINZELUNTERNEHMEN,
    "0103": Rechtsform.KOLLEKTIVGESELLSCHAFT,
    "0104": Rechtsform.KOMMANDITGESELLSCHAFT,
    "0105": Rechtsform.AG,  # Kommanditaktiengesellschaft - AG-Sonderform
    "0106": Rechtsform.AG,
    "0107": Rechtsform.GMBH,
    "0108": Rechtsform.GENOSSENSCHAFT,
    "0109": Rechtsform.VEREIN,
    "0110": Rechtsform.STIFTUNG,
    "0111": Rechtsform.ZWEIGNIEDERLASSUNG,  # auslaendische Niederlassung
    "0113": Rechtsform.UNBEKANNT,  # besondere Rechtsform
    "0117": Rechtsform.OEFFENTLICH_RECHTLICH,  # Institut des oeffentlichen Rechts
    "0118": Rechtsform.UNBEKANNT,  # nichtkaufmaennische Prokuren
    "0119": Rechtsform.UNBEKANNT,  # Haupt von Gemeinderschaften
    "0151": Rechtsform.ZWEIGNIEDERLASSUNG,  # schweizerische Zweigniederlassung
}


def rechtsform_from_code(code: str | None) -> Rechtsform:
    """Bildet einen eCH-0097-Code auf das Modell ab; unbekannt bleibt UNBEKANNT.

    >>> rechtsform_from_code("0106")
    <Rechtsform.AG: 'ag'>
    >>> rechtsform_from_code(None)
    <Rechtsform.UNBEKANNT: 'unbekannt'>
    """
    if code is None:
        return Rechtsform.UNBEKANNT
    return LEGAL_FORM_CODES.get(code.strip(), Rechtsform.UNBEKANNT)


def legal_form_codes_for(rechtsform: Rechtsform) -> tuple[str, ...]:
    """Alle eCH-0097-Codes, die auf diese Rechtsform abgebildet werden.

    ``UNBEKANNT`` liefert bewusst nichts: es steht fuer alles, was die Tabelle
    nicht kennt, und laesst sich deshalb nicht als Filter ausdruecken.

    >>> legal_form_codes_for(Rechtsform.ZWEIGNIEDERLASSUNG)
    ('0111', '0151')
    >>> legal_form_codes_for(Rechtsform.UNBEKANNT)
    ()
    """
    if rechtsform is Rechtsform.UNBEKANNT:
        return ()
    return tuple(code for code, form in LEGAL_FORM_CODES.items() if form is rechtsform)
