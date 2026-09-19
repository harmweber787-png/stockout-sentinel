# Zefix-Felder als Signal fuer kleine Betriebe mit manueller Administration

Stand 2026-09-16, auf Basis der OpenAPI-Beschreibung der Zefix Public REST
API (`docs/zefix_feldtabelle.md`). Die Abdeckung der Felder ist ohne
Zugangsdaten nicht gemessen; jede Aussage unten ist deshalb eine Hypothese,
die an einer Stichprobe zu pruefen ist, bevor sie ins Scoring geht.

## Was die API kann und was nicht

Zefix kennt **keinen** NOGA-Code, keine Groessenklasse, keine Website, keine
E-Mail und **kein Gruendungsdatum** als eigenes Feld. Groesse und Alter sind
nur indirekt ablesbar. Was Zefix gegenueber LINDAS zusaetzlich hat: Status,
SHAB-Historie mit Mutationsarten, Kapital, Revisionsstelle, Verknuepfungen,
fruehere Namen.

## Felder nach Nutzen

| Feld | Was es sagt | Nutzen fuer die Suche | Vorsicht |
|---|---|---|---|
| `status` | `ACTIVE`, `BEING_CANCELLED`, `CANCELLED` | Harter Filter: nur `ACTIVE`. Liquidation und Loeschung raus, bevor irgendetwas anderes zaehlt. | LINDAS hat dieses Feld nicht; dort steht der Zusatz nur im Namen. |
| `legalForm.uid` (eCH-0097) | Rechtsform | `0101` Einzelunternehmen und `0107` GmbH sind die Kernzielgruppe; `0103` Kollektivgesellschaft ebenso. AG (`0106`) nur mit weiteren Kleinheitssignalen. Vereine, Stiftungen, oeffentlich-rechtliche raus. | Rechtsform allein sagt nichts ueber Administrationsschmerz; sie grenzt nur die Grundgesamtheit ein. |
| `auditCompanies` | eingetragene Revisionsstelle | **Staerkstes Groessensignal der API.** Eine AG/GmbH ohne Revisionsstelle hat in der Regel auf die eingeschraenkte Revision verzichtet (Opting-out); das ist nur bis zehn Vollzeitstellen im Jahresmittel zulaessig. Leer bei AG/GmbH heisst also: sehr wahrscheinlich Mikrobetrieb. | Einzelunternehmen haben nie eine Revisionsstelle; das Signal gilt nur fuer Kapitalgesellschaften. Ob das Feld befuellt geliefert wird, ist nicht gemessen. |
| `capitalNominal`, `capitalCurrency` | Nominalkapital | GmbH mit 20 000 CHF und AG mit 100 000 CHF sind die gesetzlichen Minima; wer genau dort steht, hat nie Kapital nachgeschossen: Hinweis auf kleinen, eigentuemergefuehrten Betrieb. | Nur bei Kapitalgesellschaften; hohes Kapital schliesst Kleinheit nicht aus (Immobilien-AG). |
| `purpose` | Zweckartikel | Speist die bestehenden Gates (Haftung, ERP) als `REGISTER_PURPOSE`-Dokument. Formulierungen wie "Handel mit Waren aller Art", "Dienstleistungen im Bereich" und Aufzaehlungen mehrerer Taetigkeiten deuten auf Generalisten mit gemischtem Tagesgeschaeft, also viel manueller Koordination. | Zweckartikel sind Juristenprosa mit Standardbausteinen; ohne Website-Text bleibt das ein schwaches Signal. |
| `sogcPub[].sogcDate`, `sogcDate` | SHAB-Publikationen, letzte Mutation | **Alter:** die aelteste Publikation ist eine Untergrenze fuer das Bestehen (kein Gruendungsdatum!). Betriebe, die seit ueber zehn Jahren bestehen und nur wenige Mutationen haben, sind stabil und haben ihre Ablaeufe meist nie professionalisiert: guter Kandidat. **Aktualitaet:** eine Mutation in den letzten zwoelf Monaten ist ein Anlass zur Kontaktaufnahme (Wechsel der Fuehrung, Sitzverlegung, neuer Zweck). | Ob `sogcPub` die ganze Historie liefert oder nur die juengsten Eintraege, ist nicht belegt; vor 2005 gibt es ohnehin keine elektronische SHAB-Historie. Ein einzelnes altes Datum kann auch ein Nachtrag sein. |
| `sogcPub[].mutationTypes[].key` | Art der Mutation | Erlaubt, Mutationen zu klassieren: Adresswechsel und Zeichnungsberechtigung sind Alltag; Zweckaenderung, Kapitalerhoehung oder Fusion deuten auf Wachstum oder Umbau, also eher Beratungs- als Software-Bedarf. | Die Schluesselwerte sind in der OpenAPI nicht aufgezaehlt; die Liste muss aus einer Stichprobe gelesen werden. |
| `sogcPub[].message` | Volltext der Publikation | Enthaelt Personen und Funktionen (Inhaber, Geschaeftsfuehrer): einzige Stelle in Zefix, an der Entscheider stehen. Nur zweckgebunden fuer die Kontaktaufnahme verwenden (revDSG). | Freitext, kantonal unterschiedlich formuliert; LLM-Extraktion noetig, nie Regex. |
| `oldNames[]` | fruehere Firmen | Viele Umfirmierungen sprechen fuer Umbau oder Uebernahme; eine einzige alte Firma mit Namensteil des Inhabers ("Muster Hans" -> "Muster GmbH") ist der typische Weg vom Einzelunternehmen zur GmbH: klein, eigentuemergefuehrt. | `sequenceNr` ordnet nur relativ; kein Datum. |
| `branchOffices`, `headOffices`, `furtherHeadOffices` | Niederlassungsstruktur | Keine Zweigniederlassungen und kein Hauptsitz anderswo = Einstandortbetrieb, die Zielgruppe. Ein Eintrag mit `headOffices` ist selbst eine Filiale und entscheidet nichts. | Zweigniederlassungen sind selten eingetragen; leer heisst nicht sicher einstandortig. |
| `hasTakenOver`, `wasTakenOverBy` | Uebernahmen | `wasTakenOverBy` gesetzt: Betrieb gehoert zu einem groesseren, Entscheidung liegt woanders, raus. `hasTakenOver`: eher Wachstum. | Nur handelsregisterliche Uebernahmen (Fusion, Vermoegensuebertragung), keine Beteiligungen. |
| `address.careOf`, `address.poBox` | c/o-Adresse, Postfach | c/o beim Treuhaender oder Privatadresse deutet auf Kleinstbetrieb ohne eigene Raeume. Verwaltung laeuft dann oft ueber den Treuhaender: Kaufentscheid liegt nicht allein beim Inhaber. | c/o ist auch bei Briefkastenfirmen ueblich; als Kleinheitssignal ja, als Kaufbereitschaftssignal nein. |
| `canton`, `legalSeatId`, `address.swissZipCode` | Sitz | Regionale Eingrenzung fuer die Suche und fuer Besuche vor Ort. | `legalSeatId` ist die BFS-Gemeindenummer, gleich wie bei LINDAS. |
| `translation` | Sprachfassungen der Firma | Mehrere Fassungen deuten auf Export oder mehrsprachige Kundschaft, eher groesser. | Schwach. |

## Was daraus fuer das Scoring folgt

1. **Filter vor Score:** `status == ACTIVE`, `wasTakenOverBy` leer, Rechtsform
   in {Einzelunternehmen, GmbH, Kollektivgesellschaft, AG}.
2. **Kleinheit** (alles Hypothesen, an Stichprobe pruefen): keine
   Revisionsstelle bei AG/GmbH, Kapital am Minimum, keine Niederlassungen,
   hoechstens eine fruehere Firma.
3. **Stabilitaet und Anlass:** alt nach aeltester SHAB-Publikation, wenige
   Mutationen insgesamt, aber eine Mutation in den letzten zwoelf Monaten
   als Kontaktanlass.
4. **Administrationsschmerz** liefert Zefix **nicht** direkt. Das Signal
   kommt aus Website und Stelleninseraten (ERP-Gate, Excel, Fax, Papier).
   Zefix grenzt die Grundgesamtheit ein und liefert Belege und Entscheider,
   mehr nicht.

## Offene Verifikation

Alles oben setzt voraus, dass die Felder befuellt geliefert werden. Erst mit
Zugangsdaten misst `scripts/probe_zefix.py` die Abdeckung: entscheidend sind
`auditCompanies`, `capitalNominal`, die Vollstaendigkeit von `sogcPub` und
die tatsaechlichen Werte von `mutationTypes[].key`.
