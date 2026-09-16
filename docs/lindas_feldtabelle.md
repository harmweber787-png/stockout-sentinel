# LINDAS-Feldtabelle: Zefix-Graph (live erhoben am 2026-09-16)

Erhoben mit `scripts/probe_lindas.py` gegen `https://lindas.admin.ch/query`. Diese
Tabelle ist die einzige Grundlage der Praedikatnamen in
`kmu_discovery/sources/lindas.py`; nichts darin stammt aus dem Gedaechtnis.

Aufruf (der automatische Graph-Scan in Schritt 1 zaehlt alle Tripel des
Endpunkts und laeuft in das Timeout; der Zefix-Graph wurde deshalb per `ASK`
bestaetigt und explizit uebergeben; die automatische Klassenwahl traefe die
haeufigste Klasse `schema:PropertyValue`, deshalb auch die Klasse explizit):

```bash
python scripts/probe_lindas.py --graph https://lindas.admin.ch/foj/zefix \
    --class https://schema.ld.admin.ch/ZefixOrganisation --timeout 120
python scripts/probe_lindas.py --graph https://lindas.admin.ch/foj/zefix \
    --class http://schema.org/PostalAddress --timeout 120
```

* Endpunkt: `https://lindas.admin.ch/query`
* Graph: `https://lindas.admin.ch/foj/zefix`
* Zielklasse: `https://schema.ld.admin.ch/ZefixOrganisation`
* Instanzen der Zielklasse: 793459

## Klassen im Zielgraphen

| Klasse | Instanzen |
|---|---:|
| `http://schema.org/PropertyValue` | 2380377 |
| `http://schema.org/DefinedTerm` | 793459 |
| `http://schema.org/Organization` | 793459 |
| `https://schema.ld.admin.ch/ZefixOrganisation` | 793459 |
| `http://schema.org/PostalAddress` | 793459 |
| `http://www.w3.org/ns/locn#Address` | 793459 |
| `http://rdfs.org/ns/void#Dataset` | 1 |
| `http://www.w3.org/ns/dcat#Dataset` | 1 |
| `http://schema.org/Dataset` | 1 |
| `http://schema.org/DefinedTermSet` | 1 |
| `https://cube.link/meta/SharedDimension` | 1 |
| `http://schema.org/Person` | 1 |

Jeder Betrieb traegt drei Typen (`schema:DefinedTerm`, `schema:Organization`,
`ZefixOrganisation`) und haengt an genau einem Adressknoten
(`schema:PostalAddress` = `locn:Address`) sowie drei Identifikator-Knoten
(`schema:PropertyValue`). Die Klasse `schema:Person` hat eine einzige Instanz
(Metadaten des Datensatzes) - **zeichnungsberechtigte Personen fuehrt der
Graph nicht**.

## Felder der Zielklasse `ZefixOrganisation`

| Praedikat | Abdeckung | Vorkommen | max. je Subjekt | Typ | Beispielwert |
|---|---:|---:|---:|---|---|
| `http://www.w3.org/1999/02/22-rdf-syntax-ns#type` | 100.0% | 2380377 | 3 | uri | http://schema.org/Organization |
| `http://schema.org/identifier` | 100.0% | 2380377 | 3 | uri | https://register.ld.admin.ch/zefix/company/1767648/UID/CHE409751126 |
| `http://schema.org/name` | 100.0% | 881619 | 8 | literal | Impasse Gourmet Sàrl |
| `http://schema.org/inDefinedTermSet` | 100.0% | 793459 | 1 | uri | https://ld.admin.ch/dimension/zefix |
| `http://schema.org/address` | 100.0% | 793459 | 1 | uri | https://register.ld.admin.ch/zefix/company/1767648/address |
| `http://schema.org/additionalType` | 100.0% | 793459 | 1 | uri | https://ld.admin.ch/ech/97/legalforms/0107 |
| `http://schema.org/legalName` | 100.0% | 793459 | 1 | literal | Atelier Hora Sàrl |
| `http://www.w3.org/ns/locn#address` | 100.0% | 793459 | 1 | uri | https://register.ld.admin.ch/zefix/company/1767648/address |
| `https://schema.ld.admin.ch/municipality` | 100.0% | 793459 | 1 | uri | https://ld.admin.ch/municipality/6436 |
| `http://schema.org/description` | 98.4% | 780548 | 1 | literal | La société a pour but la fabrication et la vente de montres, de fournitures, de  |

Beobachtungen:

* `schema:legalName` ist die Firma (1:1, 100 %). `schema:name` traegt daneben
  Sprachfassungen: 718 436 ohne Sprachtag (in der Regel gleich `legalName`),
  67 818 `@en`, 53 589 `@fr`, 27 272 `@it`, 14 457 `@de`, 39 `@rm`, 7 `@es`.
* `schema:description` ist der Zweckartikel (98.4 %).
* `schema:additionalType` verweist auf eCH-0097-Rechtsformen (100 %).
* Ein **Statusfeld gibt es nicht**; ein Liquidationszusatz steht nur in der
  Firma (`... in Liquidation`).
* `locn:address` dupliziert `schema:address`; `schema:inDefinedTermSet` ist
  konstant `https://ld.admin.ch/dimension/zefix`.
* **Nicht vorhanden:** NOGA-Code, Groessenklasse, Website, E-Mail, Personen.

## Felder des Adressknotens `schema:PostalAddress`

Instanzen: 793459

| Praedikat | Abdeckung | Vorkommen | max. je Subjekt | Typ | Beispielwert |
|---|---:|---:|---:|---|---|
| `http://www.w3.org/1999/02/22-rdf-syntax-ns#type` | 100.0% | 1586918 | 2 | uri | http://www.w3.org/ns/locn#Address |
| `http://schema.org/streetAddress` | 100.0% | 793459 | 1 | literal | Les Foyards 46 |
| `http://schema.org/addressLocality` | 100.0% | 793459 | 1 | literal | La Chaux-de-Fonds |
| `http://schema.org/addressRegion` | 100.0% | 793459 | 1 | literal | NE |
| `http://www.w3.org/ns/locn#postName` | 100.0% | 793459 | 1 | literal | Les Brenets |
| `http://www.w3.org/ns/locn#adminUnitL2` | 100.0% | 793459 | 1 | literal | NE |
| `http://schema.org/postalCode` | 99.5% | 789790 | 1 | literal | 2416 |
| `http://www.w3.org/ns/locn#postCode` | 99.5% | 789790 | 1 | literal | 2416 |
| `http://www.w3.org/ns/locn#thoroughfare` | 99.3% | 787731 | 1 | literal | Rue du Lac |
| `http://www.w3.org/ns/locn#locatorDesignator` | 98.0% | 777553 | 1 | literal | 26 |
| `http://www.w3.org/ns/locn#locatorName` | 16.5% | 130787 | 1 | literal | c/o NRG Solutions AG |
| `http://schema.org/areaServed` | 3.0% | 24048 | 1 | literal | Welle 7 |
| `http://www.w3.org/ns/locn#addressArea` | 3.0% | 24048 | 1 | literal | Welle 7 |
| `http://schema.org/postOfficeBoxNumber` | 2.3% | 18190 | 1 | literal | Boîte postale 242 |
| `http://www.w3.org/ns/locn#poBox` | 2.3% | 18190 | 1 | literal | Boîte postale 242 |

`schema:addressRegion` ist das Kantonskuerzel (100 %). Die `locn:*`-Felder
duplizieren die `schema:*`-Felder und werden nicht verwendet.

## Identifikator-Knoten `schema:PropertyValue`

Instanzen: 2380377 (drei je Betrieb)

| Praedikat | Abdeckung | Vorkommen | max. je Subjekt | Typ | Beispielwert |
|---|---:|---:|---:|---|---|
| `http://www.w3.org/1999/02/22-rdf-syntax-ns#type` | 100.0% | 2380377 | 1 | uri | http://schema.org/PropertyValue |
| `http://schema.org/name` | 100.0% | 2380377 | 1 | literal | CompanyCHID |
| `http://schema.org/value` | 100.0% | 2380377 | 1 | literal | CH64541328326 |

`schema:name` nimmt genau drei Werte an, je 793459 mal:
`CompanyUID` (Wert kompakt, z. B. `CHE242294601`), `CompanyCHID`
(z. B. `CH03640617915`), `CompanyEHRAID`.

## Rechtsformen (`schema:additionalType`)

Codes und Bezeichnungen aus dem Graphen `https://lindas.admin.ch/lindas-ech`
(Termset `https://ld.admin.ch/ech/97/legalforms`), Haeufigkeit im Zefix-Graphen:

| Code | Bezeichnung (de) | Betriebe | Modell-Rechtsform |
|---|---|---:|---|
| 0107 | Gesellschaft mit beschraenkter Haftung | 291671 | `gmbh` |
| 0106 | Aktiengesellschaft | 250966 | `ag` |
| 0101 | Einzelunternehmen | 180198 | `einzelunternehmen` |
| 0110 | Stiftung | 17784 | `stiftung` |
| 0151 | Schweizerische Zweigniederlassung im Handelsregister eingetragen | 15356 | `zweigniederlassung` |
| 0109 | Verein | 13544 | `verein` |
| 0103 | Kollektivgesellschaft | 11215 | `kollektivgesellschaft` |
| 0108 | Genossenschaft | 7957 | `genossenschaft` |
| 0111 | Auslaendische Niederlassung im Handelsregister eingetragen | 3162 | `zweigniederlassung` |
| 0104 | Kommanditgesellschaft | 1030 | `kommanditgesellschaft` |
| 0117 | Institut des oeffentlichen Rechts | 468 | `oeffentlich_rechtlich` |
| 0113 | Besondere Rechtsform | 54 | `unbekannt` |
| 0119 | Haupt von Gemeinderschaften | 31 | `unbekannt` |
| 0118 | Nichtkaufmaennische Prokuren | 14 | `unbekannt` |
| 0105 | Kommanditaktiengesellschaft | 9 | `ag` |

## Gemeinde (`schema.ld.admin.ch/municipality`)

Verweist auf `https://ld.admin.ch/municipality/<BFS-Nummer>` im Graphen
`https://lindas.admin.ch/fso/register` (dort `schema:name`,
`schema:containedInPlace` auf Kanton und Bezirk). Der Client uebernimmt nur
die BFS-Nummer; der Kanton kommt direkt aus `schema:addressRegion`.

## Beispielsubjekt

| Praedikat | Wert |
|---|---|
| `http://www.w3.org/1999/02/22-rdf-syntax-ns#type` | http://schema.org/DefinedTerm |
| `http://www.w3.org/1999/02/22-rdf-syntax-ns#type` | http://schema.org/Organization |
| `http://www.w3.org/1999/02/22-rdf-syntax-ns#type` | https://schema.ld.admin.ch/ZefixOrganisation |
| `http://schema.org/description` | Die Gesellschaft bezweckt das Herstellen von Produkten, den Handel, die Vermittlung und den Verleih  |
| `http://schema.org/identifier` | https://register.ld.admin.ch/zefix/company/1198554/UID/CHE242294601 |
| `http://schema.org/identifier` | https://register.ld.admin.ch/zefix/company/1198554/CHID/CH03640617915 |
| `http://schema.org/identifier` | https://register.ld.admin.ch/zefix/company/1198554/EHRAID |
| `http://schema.org/inDefinedTermSet` | https://ld.admin.ch/dimension/zefix |
| `http://schema.org/address` | https://register.ld.admin.ch/zefix/company/1198554/address |
| `http://schema.org/additionalType` | https://ld.admin.ch/ech/97/legalforms/0107 |
| `http://schema.org/legalName` | Zazuko GmbH |
| `http://www.w3.org/ns/locn#address` | https://register.ld.admin.ch/zefix/company/1198554/address |
| `https://schema.ld.admin.ch/municipality` | https://ld.admin.ch/municipality/371 |
