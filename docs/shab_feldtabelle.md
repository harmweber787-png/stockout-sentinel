# SHAB-Feldtabelle: Amtsblattportal-API, Rubrik HR (live erhoben am 2026-09-17)

Erhoben mit `scripts/probe_shab.py` gegen die dokumentierte, offene REST-API
des Amtsblattportals. Die Tabelle hat drei Ebenen: Metadaten der Trefferliste
(JSON), Inhaltsfelder einer Einzelpublikation (XML, Abdeckung an je drei
Stichproben gemessen) und die vollstaendige Inhaltsstruktur aus dem Schema
(XSD, gilt auch ohne Stichprobe).

```bash
python scripts/probe_shab.py --dry-run
python scripts/probe_shab.py --json-out docs/shab_feldtabelle.json
```

## Was die Erhebung ergeben hat

* **Kein Personenelement.** Zeichnungsberechtigte stehen ausschliesslich im
  Freitext `content/publicationText`. Weder Liste noch XML noch XSD kennen ein
  strukturiertes Personenfeld.
* **Die Aenderungsart ist strukturiert.** `transaction/update/changements`
  fuehrt je ein Flag fuer `addressChanged`, `seatChanged`, `nameChanged`,
  `purposeChanged`, `capitalChanged`, `statusChanged` und `others`. Damit ist
  eine Adressaenderung ohne Textauswertung erkennbar.
* **`others` ist ein Sammeltopf.** Laut Schema: "This option implies any
  changement other than the subsequent listed changement options. This might
  include typos, changements in prenames or changements in signature
  authorisations." Ein Personenwechsel liegt also unter `others`, aber nicht
  jedes `others` ist ein Personenwechsel.
* **Neuer und bisheriger Stand.** `commonsNew` ist der neue Stand,
  `commonsActual` der bisherige. HR01 fuehrt nur `commonsNew`, HR03 nur
  `commonsActual`, HR02 in der Regel beide - daraus laesst sich die alte
  Adresse der neuen gegenueberstellen.
* **Die Liste enthaelt keinen Inhalt.** Laut API-Doku (Kapitel 2.2) liefert
  die Trefferliste nur Metadaten; fuer den Inhalt ist je Treffer ein
  Einzelabruf noetig. Das bestimmt die Aufrufzahl des Clients.
* **Rubrik und Unterrubrik werden mit ODER verknuepft.** `rubrics=HR` neben
  `subRubrics=HR01` liefert alle HR-Meldungen. Der Client setzt deshalb nur
  `subRubrics`.
* **Rechtsform als eCH-0097-Code.** `company/legalForm` traegt denselben Code
  wie LINDAS und Zefix (`0101`, `0106`, `0107`), also dieselbe Tabelle in
  `sources/legal_forms.py`.

## Rechtlicher Rahmen

Die API ist laut Doku (Kapitel 1.1) frei nutzbar: "The API is freely
accessible for anyone to use"; publizierte Meldungen brauchen keine
Zugangsdaten. Die robots.txt von `amtsblattportal.ch` steht auf
`Disallow: /` und gilt der HTML-Oberflaeche; benutzt wird ausschliesslich die
API als vorgesehener Maschinenzugang. Publikationstexte enthalten
Personendaten aus einem oeffentlichen Register: zweckgebunden fuer die
Kontaktaufnahme, keine Bulk-Auswertung, keine Personenprofile (revDSG).

* Basis-URL: `https://amtsblattportal.ch/api/v1`
* Kantone: ZH, AG, ZG
* Zeitraum: 2026-06-19 bis 2026-09-17
* Schemaversion: `1.26`

## Anzahl Publikationen im Zeitraum

| Unterrubrik | ZH | AG | ZG | alle drei |
|---|---|---|---|---|
| HR01 | 2438 | 875 | 864 | 4177 |
| HR02 | 10428 | 2909 | 4764 | 18101 |
| HR03 | 1801 | 591 | 509 | 2901 |

## Meta-Felder der Liste (JSON)

### HR01

| Pfad | Abdeckung | Beispielwert |
|---|---|---|
| `meta.id` | 100% | 0c65a54b-7361-491f-890d-82ab8272d5a5 |
| `meta.creationDate` | 100% | 2026-09-15T13:10:11.443Z |
| `meta.updateDate` | 100% | 2026-09-16T22:39:18.307Z |
| `meta.rubric` | 100% | HR |
| `meta.subRubric` | 100% | HR01 |
| `meta.language` | 100% | de |
| `meta.registrationOffice.id` | 100% | e15a629a-a08d-11e8-aa11-0050569d3c43 |
| `meta.registrationOffice.displayName` | 100% | Bundesamt für Justiz (BJ), Eidgenössisches Amt für das Handelsregister |
| `meta.registrationOffice.street` | 100% | Bundesrain |
| `meta.registrationOffice.streetNumber` | 100% | 20 |
| `meta.registrationOffice.swissZipCode` | 100% | 3003 |
| `meta.registrationOffice.town` | 100% | Bern |
| `meta.registrationOffice.containsPostOfficeBox` | 100% | False |
| `meta.registrationOffice.postOfficeBox` | 0% |  |
| `meta.registrationOffice.municipalityId` | 0% |  |
| `meta.registrationOffice.uid` | 0% |  |
| `meta.publicationOriginator` | 0% |  |
| `meta.publicationNumber` | 100% | HR01-1006759110 |
| `meta.publicationState` | 100% | PUBLISHED |
| `meta.publicationDate` | 100% | 2026-09-17T00:00:00.000Z |
| `meta.expirationDate` | 0% |  |
| `meta.primaryTenantCode` | 100% | shab |
| `meta.onBehalfOf` | 0% |  |
| `meta.invoiceAddressId` | 0% |  |
| `meta.legalRemedy` | 100% | Die aufgeführte Rechtseinheit wurde ins Handelsregister aufgenommen und ist rech |
| `meta.cantons[]` | 100% | AG |
| `meta.secondaryTenants` | 0% |  |
| `meta.repeatedPublications` | 0% |  |
| `meta.customsStampImages` | 0% |  |
| `meta.title.de` | 100% | Neueintragung Nothelfer Star Campos García, Wettingen |
| `meta.title.en` | 100% | New entries Nothelfer Star Campos García, Wettingen |
| `meta.title.it` | 100% | Nuove registrazioni Nothelfer Star Campos García, Wettingen |
| `meta.title.fr` | 100% | Nouvelles entrées Nothelfer Star Campos García, Wettingen |
| `meta.dossierReference` | 0% |  |
| `meta.copyDeadline` | 0% |  |
| `links` | 0% |  |
| `attachments` | 0% |  |
| `content` | 0% |  |
| `commented` | 100% | False |
| `meta.secondaryTenants[].tenantCode` | 20% | kabzg |
| `meta.secondaryTenants[].publicationDate` | 20% | 2026-09-17T00:00:00.000Z |

### HR02

| Pfad | Abdeckung | Beispielwert |
|---|---|---|
| `meta.id` | 100% | b714a093-1676-45c9-bc33-28300f249a89 |
| `meta.creationDate` | 100% | 2026-09-15T13:29:35.289Z |
| `meta.updateDate` | 100% | 2026-09-16T22:39:11.396Z |
| `meta.rubric` | 100% | HR |
| `meta.subRubric` | 100% | HR02 |
| `meta.language` | 100% | de |
| `meta.registrationOffice.id` | 100% | e15a629a-a08d-11e8-aa11-0050569d3c43 |
| `meta.registrationOffice.displayName` | 100% | Bundesamt für Justiz (BJ), Eidgenössisches Amt für das Handelsregister |
| `meta.registrationOffice.street` | 100% | Bundesrain |
| `meta.registrationOffice.streetNumber` | 100% | 20 |
| `meta.registrationOffice.swissZipCode` | 100% | 3003 |
| `meta.registrationOffice.town` | 100% | Bern |
| `meta.registrationOffice.containsPostOfficeBox` | 100% | False |
| `meta.registrationOffice.postOfficeBox` | 0% |  |
| `meta.registrationOffice.municipalityId` | 0% |  |
| `meta.registrationOffice.uid` | 0% |  |
| `meta.publicationOriginator` | 0% |  |
| `meta.publicationNumber` | 100% | HR02-1006758471 |
| `meta.publicationState` | 100% | PUBLISHED |
| `meta.publicationDate` | 100% | 2026-09-17T00:00:00.000Z |
| `meta.expirationDate` | 0% |  |
| `meta.primaryTenantCode` | 100% | shab |
| `meta.onBehalfOf` | 0% |  |
| `meta.invoiceAddressId` | 0% |  |
| `meta.legalRemedy` | 100% | Die Mutation der aufgeführten Rechtseinheit wurde im Handelsregister vorgenommen |
| `meta.cantons[]` | 100% | ZH |
| `meta.secondaryTenants` | 0% |  |
| `meta.repeatedPublications` | 0% |  |
| `meta.customsStampImages` | 0% |  |
| `meta.title.de` | 100% | Mutation Collabree AG, Zürich |
| `meta.title.en` | 100% | Change Collabree AG, Zürich |
| `meta.title.it` | 100% | Cambiamenti Collabree AG, Zürich |
| `meta.title.fr` | 100% | Mutation Collabree AG, Zürich |
| `meta.dossierReference` | 0% |  |
| `meta.copyDeadline` | 0% |  |
| `links` | 0% |  |
| `attachments` | 0% |  |
| `content` | 0% |  |
| `commented` | 100% | False |
| `meta.secondaryTenants[].tenantCode` | 20% | kabzg |
| `meta.secondaryTenants[].publicationDate` | 20% | 2026-09-17T00:00:00.000Z |

### HR03

| Pfad | Abdeckung | Beispielwert |
|---|---|---|
| `meta.id` | 100% | 3f6085cf-3c8a-42f1-a7ad-a42b352701da |
| `meta.creationDate` | 100% | 2026-09-15T13:31:47.218Z |
| `meta.updateDate` | 100% | 2026-09-16T22:38:39.883Z |
| `meta.rubric` | 100% | HR |
| `meta.subRubric` | 100% | HR03 |
| `meta.language` | 100% | de |
| `meta.registrationOffice.id` | 100% | e15a629a-a08d-11e8-aa11-0050569d3c43 |
| `meta.registrationOffice.displayName` | 100% | Bundesamt für Justiz (BJ), Eidgenössisches Amt für das Handelsregister |
| `meta.registrationOffice.street` | 100% | Bundesrain |
| `meta.registrationOffice.streetNumber` | 100% | 20 |
| `meta.registrationOffice.swissZipCode` | 100% | 3003 |
| `meta.registrationOffice.town` | 100% | Bern |
| `meta.registrationOffice.containsPostOfficeBox` | 100% | False |
| `meta.registrationOffice.postOfficeBox` | 0% |  |
| `meta.registrationOffice.municipalityId` | 0% |  |
| `meta.registrationOffice.uid` | 0% |  |
| `meta.publicationOriginator` | 0% |  |
| `meta.publicationNumber` | 100% | HR03-1006758532 |
| `meta.publicationState` | 100% | PUBLISHED |
| `meta.publicationDate` | 100% | 2026-09-17T00:00:00.000Z |
| `meta.expirationDate` | 0% |  |
| `meta.primaryTenantCode` | 100% | shab |
| `meta.onBehalfOf` | 0% |  |
| `meta.invoiceAddressId` | 0% |  |
| `meta.legalRemedy` | 0% |  |
| `meta.cantons[]` | 100% | ZH |
| `meta.secondaryTenants` | 0% |  |
| `meta.repeatedPublications` | 0% |  |
| `meta.customsStampImages` | 0% |  |
| `meta.title.de` | 100% | Löschung Malerbetrieb Steiner, Winterthur |
| `meta.title.en` | 100% | Deletion Malerbetrieb Steiner, Winterthur |
| `meta.title.it` | 100% | Cancellazione Malerbetrieb Steiner, Winterthur |
| `meta.title.fr` | 100% | Annulation Malerbetrieb Steiner, Winterthur |
| `meta.dossierReference` | 0% |  |
| `meta.copyDeadline` | 0% |  |
| `links` | 0% |  |
| `attachments` | 0% |  |
| `content` | 0% |  |
| `commented` | 100% | False |

## Inhaltsfelder der Einzelpublikation (XML)

### HR01 (3 Stichproben)

| Pfad | Abdeckung | Beispielwert |
|---|---|---|
| `meta/id` | 100% | 0c65a54b-7361-491f-890d-82ab8272d5a5 |
| `meta/rubric` | 100% | HR |
| `meta/subRubric` | 100% | HR01 |
| `meta/language` | 100% | de |
| `meta/registrationOffice/id` | 100% | e15a629a-a08d-11e8-aa11-0050569d3c43 |
| `meta/registrationOffice/displayName` | 100% | Bundesamt für Justiz (BJ), Eidgenössisches Amt für das Handelsregister |
| `meta/registrationOffice/street` | 100% | Bundesrain |
| `meta/registrationOffice/streetNumber` | 100% | 20 |
| `meta/registrationOffice/swissZipCode` | 100% | 3003 |
| `meta/registrationOffice/town` | 100% | Bern |
| `meta/registrationOffice/containsPostOfficeBox` | 100% | false |
| `meta/publicationNumber` | 100% | HR01-1006759110 |
| `meta/publicationState` | 100% | PUBLISHED |
| `meta/publicationDate` | 100% | 2026-09-17 |
| `meta/primaryTenantCode` | 100% | shab |
| `meta/legalRemedy` | 100% | Die aufgeführte Rechtseinheit wurde ins Handelsregister aufgenommen und ist rech |
| `meta/cantons` | 100% | AG |
| `meta/title/de` | 100% | Neueintragung Nothelfer Star Campos García, Wettingen |
| `meta/title/en` | 100% | New entries Nothelfer Star Campos García, Wettingen |
| `meta/title/it` | 100% | Nuove registrazioni Nothelfer Star Campos García, Wettingen |
| `meta/title/fr` | 100% | Nouvelles entrées Nothelfer Star Campos García, Wettingen |
| `content/testImport` | 100% | false |
| `content/journalNumber` | 100% | 13128 |
| `content/journalDate` | 100% | 2026-09-14 |
| `content/publicationText` | 100% | Nothelfer Star Campos García, in Wettingen, CHE-306.462.688, Güterstrasse 12, 54 |
| `content/commonsNew/company/name` | 100% | Nothelfer Star Campos García |
| `content/commonsNew/company/uid` | 100% | CHE-306.462.688 |
| `content/commonsNew/company/uidOrganisationId` | 100% | 306462688 |
| `content/commonsNew/company/uidOrganisationIdCategorie` | 100% | CHE |
| `content/commonsNew/company/code13` | 100% | CH40016147828 |
| `content/commonsNew/company/seat` | 100% | Wettingen |
| `content/commonsNew/company/legalForm` | 100% | 0101 |
| `content/commonsNew/company/noAddress` | 100% | false |
| `content/commonsNew/company/address/street` | 100% | Güterstrasse |
| `content/commonsNew/company/address/houseNumber` | 100% | 12 |
| `content/commonsNew/company/address/swissZipCode` | 100% | 5430 |
| `content/commonsNew/company/address/town` | 100% | Wettingen |
| `content/commonsNew/purpose` | 100% | Unterrichtung von Erwachsenen und Kindern sowie Erbringung von Unterrichtsdienst |
| `content/commonsNew/revision/optingOut` | 100% | false |
| `content/transaction/registration` | 100% | true |
| `content/senderOffice/officeName` | 100% | Handelsregisteramt des Kantons Aargau |
| `meta/secondaryTenants/tenantCode` | 33% | kabzg |
| `meta/secondaryTenants/publicationDate` | 33% | 2026-09-17 |
| `content/commonsNew/capital/nominal` | 33% | 100000.00 |
| `content/commonsNew/capital/paid` | 33% | 100000.00 |

### HR02 (3 Stichproben)

| Pfad | Abdeckung | Beispielwert |
|---|---|---|
| `meta/id` | 100% | b714a093-1676-45c9-bc33-28300f249a89 |
| `meta/rubric` | 100% | HR |
| `meta/subRubric` | 100% | HR02 |
| `meta/language` | 100% | de |
| `meta/registrationOffice/id` | 100% | e15a629a-a08d-11e8-aa11-0050569d3c43 |
| `meta/registrationOffice/displayName` | 100% | Bundesamt für Justiz (BJ), Eidgenössisches Amt für das Handelsregister |
| `meta/registrationOffice/street` | 100% | Bundesrain |
| `meta/registrationOffice/streetNumber` | 100% | 20 |
| `meta/registrationOffice/swissZipCode` | 100% | 3003 |
| `meta/registrationOffice/town` | 100% | Bern |
| `meta/registrationOffice/containsPostOfficeBox` | 100% | false |
| `meta/publicationNumber` | 100% | HR02-1006758471 |
| `meta/publicationState` | 100% | PUBLISHED |
| `meta/publicationDate` | 100% | 2026-09-17 |
| `meta/primaryTenantCode` | 100% | shab |
| `meta/legalRemedy` | 100% | Die Mutation der aufgeführten Rechtseinheit wurde im Handelsregister vorgenommen |
| `meta/cantons` | 100% | ZH |
| `meta/title/de` | 100% | Mutation Collabree AG, Zürich |
| `meta/title/en` | 100% | Change Collabree AG, Zürich |
| `meta/title/it` | 100% | Cambiamenti Collabree AG, Zürich |
| `meta/title/fr` | 100% | Mutation Collabree AG, Zürich |
| `content/testImport` | 100% | false |
| `content/journalNumber` | 100% | 43195 |
| `content/journalDate` | 100% | 2026-09-14 |
| `content/publicationText` | 100% | Collabree AG, in Zürich, CHE-287.738.801, Aktiengesellschaft (SHAB Nr. 78 vom 24 |
| `content/commonsNew/company/name` | 100% | Collabree AG |
| `content/commonsNew/company/translations` | 67% | (Collabree SA) (Collabree Ltd) |
| `content/commonsNew/company/uid` | 100% | CHE-287.738.801 |
| `content/commonsNew/company/uidOrganisationId` | 100% | 287738801 |
| `content/commonsNew/company/uidOrganisationIdCategorie` | 100% | CHE |
| `content/commonsNew/company/code13` | 100% | CH02030488662 |
| `content/commonsNew/company/seat` | 100% | Zürich |
| `content/commonsNew/company/legalForm` | 100% | 0106 |
| `content/commonsNew/company/noAddress` | 100% | false |
| `content/commonsNew/company/address/addressLine1` | 67% | c/o Evoleen AG |
| `content/commonsNew/company/address/street` | 100% | Dreikönigstrasse |
| `content/commonsNew/company/address/houseNumber` | 100% | 34 |
| `content/commonsNew/company/address/swissZipCode` | 100% | 8002 |
| `content/commonsNew/company/address/town` | 100% | Zürich |
| `content/commonsNew/purpose` | 100% | Die Gesellschaft bezweckt die Entwicklung und Vermarktung von digitalen Gesundhe |
| `content/commonsNew/capital/nominal` | 100% | 151333.00 |
| `content/commonsNew/capital/paid` | 100% | 151333.00 |
| `content/commonsNew/revision/optingOut` | 100% | false |
| `content/commonsNew/revision/revisionCompany/name` | 67% | Lienhard Audit AG |
| `content/commonsNew/revision/revisionCompany/country` | 67% | CH |
| `content/commonsNew/revision/revisionCompany/uid` | 67% | CHE139583547 |
| `content/commonsNew/revision/revisionCompany/uidOrganisationId` | 67% | 139583547 |
| `content/commonsNew/revision/revisionCompany/uidOrganisationIdCategorie` | 67% | CHE |
| `content/commonsActual/company/name` | 100% | Collabree AG |
| `content/commonsActual/company/translations` | 67% | (Collabree SA) (Collabree Ltd) |
| `content/commonsActual/company/uid` | 100% | CHE-287.738.801 |
| `content/commonsActual/company/uidOrganisationId` | 100% | 287738801 |
| `content/commonsActual/company/uidOrganisationIdCategorie` | 100% | CHE |
| `content/commonsActual/company/code13` | 100% | CH02030488662 |
| `content/commonsActual/company/seat` | 100% | Zürich |
| `content/commonsActual/company/legalForm` | 100% | 0106 |
| `content/commonsActual/company/noAddress` | 100% | false |
| `content/commonsActual/company/address/addressLine1` | 67% | c/o Evoleen AG |
| `content/commonsActual/company/address/street` | 100% | Dreikönigstrasse |
| `content/commonsActual/company/address/houseNumber` | 100% | 34 |
| `content/commonsActual/company/address/swissZipCode` | 100% | 8002 |
| `content/commonsActual/company/address/town` | 100% | Zürich |
| `content/commonsActual/purpose` | 100% | Die Gesellschaft bezweckt die Entwicklung und Vermarktung von digitalen Gesundhe |
| `content/commonsActual/capital/nominal` | 100% | 151333.00 |
| `content/commonsActual/capital/paid` | 100% | 151333.00 |
| `content/commonsActual/revision/optingOut` | 100% | false |
| `content/lastFosc/lastFoscDate` | 100% | 2026-04-24 |
| `content/lastFosc/lastFoscNumber` | 100% | 78 |
| `content/lastFosc/lastFoscSequence` | 100% | 1006633600 |
| `content/transaction/update/changements/others` | 100% | true |
| `content/transaction/update/changements/nameChanged` | 100% | false |
| `content/transaction/update/changements/uidChanged` | 100% | false |
| `content/transaction/update/changements/legalStatusChanged` | 100% | false |
| `content/transaction/update/changements/seatChanged` | 100% | false |
| `content/transaction/update/changements/addressChanged` | 100% | false |
| `content/transaction/update/changements/purposeChanged` | 100% | false |
| `content/transaction/update/changements/capitalChanged/nominal` | 100% | false |
| `content/transaction/update/changements/capitalChanged/paid` | 100% | false |
| `content/transaction/update/changements/capitalChanged/other` | 100% | false |
| `content/transaction/update/changements/statusChanged/bankruptcy/dissolution` | 100% | false |
| `content/transaction/update/changements/statusChanged/bankruptcy/dismissal` | 100% | false |
| `content/transaction/update/changements/statusChanged/bankruptcy/revocation` | 100% | false |
| `content/transaction/update/changements/statusChanged/bankruptcy/cancellation` | 100% | false |
| `content/transaction/update/changements/statusChanged/bankruptcy/summary` | 100% | false |
| `content/transaction/update/changements/statusChanged/liquidation/dissolution/nonExceptional` | 100% | false |
| `content/transaction/update/changements/statusChanged/liquidation/dissolution/or731b` | 100% | false |
| `content/transaction/update/changements/statusChanged/liquidation/dissolution/hregv153b` | 100% | false |
| `content/transaction/update/changements/statusChanged/liquidation/revocation` | 100% | false |
| `content/transaction/update/changements/statusChanged/suspension` | 100% | false |
| `content/transaction/update/changements/statusChanged/reentry` | 100% | false |
| `content/transaction/update/changements/statusChanged/reapplication` | 100% | false |
| `content/senderOffice/officeName` | 100% | Handelsregisteramt des Kantons Zürich |

### HR03 (3 Stichproben)

| Pfad | Abdeckung | Beispielwert |
|---|---|---|
| `meta/id` | 100% | 3f6085cf-3c8a-42f1-a7ad-a42b352701da |
| `meta/rubric` | 100% | HR |
| `meta/subRubric` | 100% | HR03 |
| `meta/language` | 100% | de |
| `meta/registrationOffice/id` | 100% | e15a629a-a08d-11e8-aa11-0050569d3c43 |
| `meta/registrationOffice/displayName` | 100% | Bundesamt für Justiz (BJ), Eidgenössisches Amt für das Handelsregister |
| `meta/registrationOffice/street` | 100% | Bundesrain |
| `meta/registrationOffice/streetNumber` | 100% | 20 |
| `meta/registrationOffice/swissZipCode` | 100% | 3003 |
| `meta/registrationOffice/town` | 100% | Bern |
| `meta/registrationOffice/containsPostOfficeBox` | 100% | false |
| `meta/publicationNumber` | 100% | HR03-1006758532 |
| `meta/publicationState` | 100% | PUBLISHED |
| `meta/publicationDate` | 100% | 2026-09-17 |
| `meta/primaryTenantCode` | 100% | shab |
| `meta/cantons` | 100% | ZH |
| `meta/title/de` | 100% | Löschung Malerbetrieb Steiner, Winterthur |
| `meta/title/en` | 100% | Deletion Malerbetrieb Steiner, Winterthur |
| `meta/title/it` | 100% | Cancellazione Malerbetrieb Steiner, Winterthur |
| `meta/title/fr` | 100% | Annulation Malerbetrieb Steiner, Winterthur |
| `content/testImport` | 100% | false |
| `content/journalNumber` | 100% | 43257 |
| `content/journalDate` | 100% | 2026-09-14 |
| `content/publicationText` | 100% | Malerbetrieb Steiner, in Winterthur, CHE-167.447.145, Einzelunternehmen (SHAB Nr |
| `content/commonsActual/company/name` | 100% | Malerbetrieb Steiner |
| `content/commonsActual/company/uid` | 100% | CHE-167.447.145 |
| `content/commonsActual/company/uidOrganisationId` | 100% | 167447145 |
| `content/commonsActual/company/uidOrganisationIdCategorie` | 100% | CHE |
| `content/commonsActual/company/code13` | 100% | CH02010784881 |
| `content/commonsActual/company/seat` | 100% | Winterthur |
| `content/commonsActual/company/legalForm` | 100% | 0101 |
| `content/commonsActual/company/noAddress` | 100% | false |
| `content/commonsActual/company/address/street` | 100% | Bürglistrasse |
| `content/commonsActual/company/address/houseNumber` | 100% | 57 |
| `content/commonsActual/company/address/swissZipCode` | 100% | 8400 |
| `content/commonsActual/company/address/town` | 100% | Winterthur |
| `content/commonsActual/purpose` | 100% | Malerbetrieb Steiner ist ein Unternehmen, dass im Bereich jeglicher Malarbeiten, |
| `content/commonsActual/checkOptingOut` | 100% | false |
| `content/lastFosc/lastFoscDate` | 100% | 2026-06-30 |
| `content/lastFosc/lastFoscNumber` | 100% | 123 |
| `content/lastFosc/lastFoscSequence` | 100% | 1006691918 |
| `content/transaction/delete/deletionDate` | 100% | 2026-09-14 |
| `content/senderOffice/officeName` | 100% | Handelsregisteramt des Kantons Zürich |
| `content/commonsActual/company/address/addressLine1` | 33% | c/o Laura López Miñano |
| `content/commonsActual/capital/nominal` | 67% | 20000.00 |

## Schema (XSD): Inhaltsstruktur je Unterrubrik

### HR01

| Pfad | Typ | min | max | Dokumentation |
|---|---|---|---|---|
| `testImport` | xs:boolean | 0 | 1 | Indicates a Test-Publication |
| `journalNumber` | xs:string | 1 | 1 | Please provide text without XML tags. |
| `journalDate` | xs:date | 1 | 1 | Please provide text without XML tags. |
| `publicationText` | xs:string | 1 | 1 | Please provide text without XML tags. |
| `commonsNew` | complex | 0 | unbounded |  |
| `commonsNew/company` | complex | 1 | 1 |  |
| `commonsNew/company/name` | xs:string | 1 | 1 |  |
| `commonsNew/company/translations` | xs:string | 0 | 1 | Name of foreign firm. Please enter before the language given in square brackets. [DE], [FR] etc. |
| `commonsNew/company/uid` | xs:string | 0 | 1 |  |
| `commonsNew/company/uidOrganisationId` | xs:string | 1 | 1 |  |
| `commonsNew/company/uidOrganisationIdCategorie` | complex | 1 | 1 |  |
| `commonsNew/company/code13` | xs:string | 0 | 1 |  |
| `commonsNew/company/seat` | xs:string | 1 | 1 | This shows the official number (as per the SFSO register) of the commune where the undertaking is legally domiciled. |
| `commonsNew/company/additionalSeat` | xs:string | 0 | 1 | Only companies with two headquarters. National bank, UBS, Nestlé |
| `commonsNew/company/legalForm` | complex | 1 | 1 | Enter the legal form and corresponding number. |
| `commonsNew/company/noAddress` | xs:boolean | 0 | 1 |  |
| `commonsNew/company/address` | complex | 0 | unbounded |  |
| `commonsNew/company/address/addressLine1` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/addressLine2` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/street` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/houseNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/town` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress` | complex | 0 | unbounded |  |
| `commonsNew/company/liqAddress/addressLine1` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/addressLine2` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/street` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/houseNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/town` | xs:string | 0 | 1 |  |
| `commonsNew/purpose` | xs:string | 0 | 1 | In future, carriage returns and line feeds can be inserted here |
| `commonsNew/capital` | complex | 0 | unbounded |  |
| `commonsNew/capital/nominal` | complex | 0 | 1 |  |
| `commonsNew/capital/paid` | complex | 0 | 1 |  |
| `commonsNew/revision` | complex | 0 | unbounded |  |
| `commonsNew/revision/optingOut` | xs:boolean | 0 | 1 | Audit opt-out |
| `commonsNew/revision/revisionCompany` | complex | 0 | unbounded |  |
| `commonsNew/revision/revisionCompany/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsNew/revision/revisionCompany/country` | xs:anyType | 0 | 1 |  |
| `commonsNew/revision/revisionCompany/uid` | xs:string | 0 | 1 |  |
| `commonsNew/revision/revisionCompany/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsNew/revision/revisionCompany/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `commonsNew/headOffice` | complex | 0 | unbounded |  |
| `commonsNew/headOffice/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsNew/headOffice/country` | xs:anyType | 0 | 1 |  |
| `commonsNew/headOffice/uid` | xs:string | 0 | 1 |  |
| `commonsNew/headOffice/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsNew/headOffice/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `transaction` | complex | 0 | unbounded |  |
| `transaction/registration` | xs:boolean | 0 | 1 | Option indicating a new entry (redundant with publication type: HR01 is ALWAYS a new entry). Previous code: 01 |
| `senderOffice` | complex | 0 | unbounded |  |
| `senderOffice/officeName` | xs:string | 0 | 1 | Can appear either in the meta data (onBehalfOf) or as it is. |

### HR02

| Pfad | Typ | min | max | Dokumentation |
|---|---|---|---|---|
| `testImport` | xs:boolean | 0 | 1 |  |
| `journalNumber` | xs:string | 1 | 1 | Please provide text without XML tags. |
| `journalDate` | xs:date | 1 | 1 | Please provide text without XML tags. |
| `publicationText` | xs:string | 1 | 1 | Please provide text without XML tags. |
| `commonsNew` | complex | 0 | unbounded |  |
| `commonsNew/company` | complex | 0 | unbounded |  |
| `commonsNew/company/name` | xs:string | 1 | 1 |  |
| `commonsNew/company/translations` | xs:string | 0 | 1 | Name of foreign firm. Please enter before the language given in square brackets. [DE], [FR] etc. |
| `commonsNew/company/uid` | xs:string | 0 | 1 |  |
| `commonsNew/company/uidOrganisationId` | xs:string | 1 | 1 |  |
| `commonsNew/company/uidOrganisationIdCategorie` | complex | 1 | 1 |  |
| `commonsNew/company/code13` | xs:string | 0 | 1 |  |
| `commonsNew/company/seat` | xs:string | 1 | 1 | This shows the official number (as per the SFSO register) of the commune where the undertaking is legally domiciled. |
| `commonsNew/company/additionalSeat` | xs:string | 0 | 1 | Only companies with two headquarters. National bank, UBS, Nestlé |
| `commonsNew/company/legalForm` | complex | 1 | 1 | Enter the legal form and corresponding number. |
| `commonsNew/company/noAddress` | xs:boolean | 0 | 1 |  |
| `commonsNew/company/address` | complex | 0 | unbounded |  |
| `commonsNew/company/address/addressLine1` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/addressLine2` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/street` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/houseNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsNew/company/address/town` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress` | complex | 0 | unbounded |  |
| `commonsNew/company/liqAddress/addressLine1` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/addressLine2` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/street` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/houseNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsNew/company/liqAddress/town` | xs:string | 0 | 1 |  |
| `commonsNew/purpose` | xs:string | 0 | 1 | In future, carriage returns and line feeds can be inserted here |
| `commonsNew/capital` | complex | 0 | unbounded |  |
| `commonsNew/capital/nominal` | complex | 0 | 1 |  |
| `commonsNew/capital/paid` | complex | 0 | 1 |  |
| `commonsNew/revision` | complex | 0 | unbounded |  |
| `commonsNew/revision/optingOut` | xs:boolean | 0 | 1 | Audit opt-out |
| `commonsNew/revision/revisionCompany` | complex | 0 | unbounded |  |
| `commonsNew/revision/revisionCompany/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsNew/revision/revisionCompany/country` | xs:anyType | 0 | 1 |  |
| `commonsNew/revision/revisionCompany/uid` | xs:string | 0 | 1 |  |
| `commonsNew/revision/revisionCompany/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsNew/revision/revisionCompany/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `commonsNew/headOffice` | complex | 0 | unbounded |  |
| `commonsNew/headOffice/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsNew/headOffice/country` | xs:anyType | 0 | 1 |  |
| `commonsNew/headOffice/uid` | xs:string | 0 | 1 |  |
| `commonsNew/headOffice/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsNew/headOffice/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `commonsActual` | complex | 0 | unbounded |  |
| `commonsActual/company` | complex | 0 | unbounded |  |
| `commonsActual/company/name` | xs:string | 1 | 1 |  |
| `commonsActual/company/translations` | xs:string | 0 | 1 | Name of foreign firm. Please enter before the language given in square brackets. [DE], [FR] etc. |
| `commonsActual/company/uid` | xs:string | 0 | 1 |  |
| `commonsActual/company/uidOrganisationId` | xs:string | 1 | 1 |  |
| `commonsActual/company/uidOrganisationIdCategorie` | complex | 1 | 1 |  |
| `commonsActual/company/code13` | xs:string | 0 | 1 |  |
| `commonsActual/company/seat` | xs:string | 1 | 1 | This shows the official number (as per the SFSO register) of the commune where the undertaking is legally domiciled. |
| `commonsActual/company/additionalSeat` | xs:string | 0 | 1 | Only companies with two headquarters. National bank, UBS, Nestlé |
| `commonsActual/company/legalForm` | complex | 1 | 1 | Enter the legal form and corresponding number. |
| `commonsActual/company/noAddress` | xs:boolean | 0 | 1 |  |
| `commonsActual/company/address` | complex | 0 | unbounded |  |
| `commonsActual/company/address/addressLine1` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/addressLine2` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/street` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/houseNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/town` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress` | complex | 0 | unbounded |  |
| `commonsActual/company/liqAddress/addressLine1` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/addressLine2` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/street` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/houseNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/town` | xs:string | 0 | 1 |  |
| `commonsActual/purpose` | xs:string | 0 | 1 | Purpose to date |
| `commonsActual/capital` | complex | 0 | unbounded |  |
| `commonsActual/capital/nominal` | complex | 0 | 1 |  |
| `commonsActual/capital/paid` | complex | 0 | 1 |  |
| `commonsActual/revision` | complex | 0 | unbounded |  |
| `commonsActual/revision/optingOut` | xs:boolean | 0 | 1 | Audit opt-out |
| `commonsActual/revision/revisionCompany` | complex | 0 | unbounded |  |
| `commonsActual/revision/revisionCompany/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsActual/revision/revisionCompany/country` | xs:anyType | 0 | 1 |  |
| `commonsActual/revision/revisionCompany/uid` | xs:string | 0 | 1 |  |
| `commonsActual/revision/revisionCompany/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsActual/revision/revisionCompany/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `commonsActual/headOffice` | complex | 0 | unbounded |  |
| `commonsActual/headOffice/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsActual/headOffice/country` | xs:anyType | 0 | 1 |  |
| `commonsActual/headOffice/uid` | xs:string | 0 | 1 |  |
| `commonsActual/headOffice/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsActual/headOffice/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `lastFosc` | complex | 0 | unbounded |  |
| `lastFosc/lastFoscDate` | xs:date | 0 | 1 |  |
| `lastFosc/lastFoscNumber` | complex | 0 | 1 | SOGC numbers were in use until 28 May 2018. Publications which appear in the SOGC after this date no longer feature and SOGC number. |
| `lastFosc/lastFoscSequence` | xs:string | 0 | 1 |  |
| `transaction` | complex | 0 | unbounded |  |
| `transaction/update` | complex | 0 | unbounded |  |
| `transaction/update/changements` | complex | 0 | unbounded |  |
| `transaction/update/changements/others` | xs:boolean | 0 | 1 | This option implies any changement other than the subsequent listed changement options. This might include typos, changements in prenames or changements in signature authorisations. Previous code: Not available |
| `transaction/update/changements/nameChanged` | xs:boolean | 0 | 1 | Changement of company name or changement of translation of company name. Previos code: Not available |
| `transaction/update/changements/uidChanged` | xs:boolean | 0 | 1 | Changement in UID number. Previous code: Not available |
| `transaction/update/changements/legalStatusChanged` | xs:boolean | 0 | 1 | Changement of legal form. Previous code: Not available |
| `transaction/update/changements/seatChanged` | xs:boolean | 0 | 1 | New seat (e.g. seat changed to other political municipality). Previous code: Not available |
| `transaction/update/changements/addressChanged` | xs:boolean | 0 | 1 | Changement in Address of residence. Might be in the same political municipality. Previous code: Not available |
| `transaction/update/changements/purposeChanged` | xs:boolean | 0 | 1 | Changement in purpose. Previous code: Not available |
| `transaction/update/changements/capitalChanged` | complex | 0 | unbounded |  |
| `transaction/update/changements/capitalChanged/nominal` | xs:boolean | 0 | 1 | Capital changed nominal. Previous code: 1. ATTENTION: The option indicating the combination of capital changements "nominal" and "paid" (previous codes: 3) is not available anymore. If this combination should be evaluate |
| `transaction/update/changements/capitalChanged/paid` | xs:boolean | 0 | 1 | Capital changed paid. Previous code: 2. ATTENTION: The option indicating the combination of capital changements "nominal" and "paid" (previous codes: 3) is not available anymore. If this combination should be evaluated/d |
| `transaction/update/changements/capitalChanged/other` | xs:boolean | 0 | 1 | Capital changed for other reasons (generally "division into shares"). Previous code: 4. ATTENTION: The option indicating combinations of capital changements (previous codes: 5, 6,7) are not available anymore. REASON: The |
| `transaction/update/changements/statusChanged` | complex | 0 | unbounded |  |
| `transaction/update/changements/statusChanged/bankruptcy` | complex | 0 | unbounded |  |
| `transaction/update/changements/statusChanged/bankruptcy/dissolution` | xs:boolean | 0 | 1 | Dissolution due to bankruptcy. Previous code: 13 |
| `transaction/update/changements/statusChanged/bankruptcy/dismissal` | xs:boolean | 0 | 1 | Dismissal of bankruptcy. Previous code: Not available |
| `transaction/update/changements/statusChanged/bankruptcy/revocation` | xs:boolean | 0 | 1 | Revocation of bankruptcy. Previous code: 30 |
| `transaction/update/changements/statusChanged/bankruptcy/cancellation` | xs:boolean | 0 | 1 | Cancellation of bankruptcy. Previous code: Not available |
| `transaction/update/changements/statusChanged/bankruptcy/summary` | xs:boolean | 0 | 1 | Summary procedure. Previous code: Not available |
| `transaction/update/changements/statusChanged/liquidation` | complex | 0 | unbounded |  |
| `transaction/update/changements/statusChanged/liquidation/dissolution` | complex | 0 | unbounded |  |
| `transaction/update/changements/statusChanged/liquidation/dissolution/nonExceptional` | xs:boolean | 0 | 1 | Dissolution due to Liquidation with no exceptional reason. Previous code: Not available. ATTENTION: The previous code 12 (Dissolution due to Liquidation) is not provided anymore, the present option indicates a "Dissoluti |
| `transaction/update/changements/statusChanged/liquidation/dissolution/or731b` | xs:boolean | 0 | 1 | Dissolution due to Liquidation according to OR 731b. Previous code: 15 |
| `transaction/update/changements/statusChanged/liquidation/dissolution/hregv153b` | xs:boolean | 0 | 1 | Dissolution due to Liquidation according to HRegV 153b. Previous code: Not available. |
| `transaction/update/changements/statusChanged/liquidation/revocation` | xs:boolean | 0 | 1 | Revocation of Liquidation. Previous code: 30 |
| `transaction/update/changements/statusChanged/suspension` | xs:boolean | 0 | 1 | Suspension of bankruptcy OR Liquidation. Previous code: Not available |
| `transaction/update/changements/statusChanged/reentry` | xs:boolean | 0 | 1 | Reentry of company. Previous code: 40 |
| `transaction/update/changements/statusChanged/reapplication` | xs:boolean | 0 | 1 | Reapplication of company. Previous company: Not available |
| `senderOffice` | complex | 0 | unbounded |  |
| `senderOffice/officeName` | xs:string | 0 | 1 | Can appear either in the meta data (onBehalfOf) or as it is. |

### HR03

| Pfad | Typ | min | max | Dokumentation |
|---|---|---|---|---|
| `testImport` | xs:boolean | 0 | 1 |  |
| `journalNumber` | xs:string | 1 | 1 | Please provide text without XML tags. |
| `journalDate` | xs:date | 1 | 1 | Please provide text without XML tags. |
| `publicationText` | xs:string | 1 | 1 | Please provide text without XML tags. |
| `commonsActual` | complex | 0 | unbounded |  |
| `commonsActual/company` | complex | 0 | unbounded |  |
| `commonsActual/company/name` | xs:string | 1 | 1 |  |
| `commonsActual/company/translations` | xs:string | 0 | 1 | Name of foreign firm. Please enter before the language given in square brackets. [DE], [FR] etc. |
| `commonsActual/company/uid` | xs:string | 0 | 1 |  |
| `commonsActual/company/uidOrganisationId` | xs:string | 1 | 1 |  |
| `commonsActual/company/uidOrganisationIdCategorie` | complex | 1 | 1 |  |
| `commonsActual/company/code13` | xs:string | 0 | 1 |  |
| `commonsActual/company/seat` | xs:string | 1 | 1 | This shows the official number (as per the SFSO register) of the commune where the undertaking is legally domiciled. |
| `commonsActual/company/additionalSeat` | xs:string | 0 | 1 | Only companies with two headquarters. National bank, UBS, Nestlé |
| `commonsActual/company/legalForm` | complex | 1 | 1 | Enter the legal form and corresponding number. |
| `commonsActual/company/noAddress` | xs:boolean | 0 | 1 |  |
| `commonsActual/company/address` | complex | 0 | unbounded |  |
| `commonsActual/company/address/addressLine1` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/addressLine2` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/street` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/houseNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsActual/company/address/town` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress` | complex | 0 | unbounded |  |
| `commonsActual/company/liqAddress/addressLine1` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/addressLine2` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/street` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/houseNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/postOfficeBoxNumber` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/postOfficeBoxText` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/swissZipCode` | xs:string | 0 | 1 |  |
| `commonsActual/company/liqAddress/town` | xs:string | 0 | 1 |  |
| `commonsActual/purpose` | xs:string | 0 | 1 | Purpose to date |
| `commonsActual/capital` | complex | 0 | unbounded |  |
| `commonsActual/capital/nominal` | complex | 0 | 1 |  |
| `commonsActual/capital/paid` | complex | 0 | 1 |  |
| `commonsActual/checkOptingOut` | xs:boolean | 0 | 1 |  |
| `commonsActual/revision` | complex | 0 | unbounded |  |
| `commonsActual/revision/revisionCompany` | complex | 0 | unbounded |  |
| `commonsActual/revision/revisionCompany/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsActual/revision/revisionCompany/country` | xs:anyType | 0 | 1 |  |
| `commonsActual/revision/revisionCompany/uid` | xs:string | 0 | 1 |  |
| `commonsActual/revision/revisionCompany/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsActual/revision/revisionCompany/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `commonsActual/headOffice` | complex | 0 | unbounded |  |
| `commonsActual/headOffice/name` | xs:string | 1 | 1 | If the head office of the organisation is not in Switzerland, you can skip the UID fields. |
| `commonsActual/headOffice/country` | xs:anyType | 0 | 1 |  |
| `commonsActual/headOffice/uid` | xs:string | 0 | 1 |  |
| `commonsActual/headOffice/uidOrganisationId` | xs:string | 0 | 1 |  |
| `commonsActual/headOffice/uidOrganisationIdCategorie` | complex | 0 | 1 |  |
| `lastFosc` | complex | 0 | unbounded |  |
| `lastFosc/lastFoscDate` | xs:date | 0 | 1 |  |
| `lastFosc/lastFoscNumber` | complex | 0 | 1 | SOGC numbers were in use until 28 May 2018. Publications which appear in the SOGC after this date no longer feature and SOGC number. |
| `lastFosc/lastFoscSequence` | xs:string | 0 | 1 |  |
| `transaction` | complex | 0 | unbounded |  |
| `transaction/delete` | complex | 0 | unbounded |  |
| `transaction/delete/deletionDate` | xs:date | 1 | 1 | Date of dissolution. Since a dissolutions is involved (always HRO3), the date of dissolution is mandatory. Former code: 20. NOTE: The "Dissolution following merger" option (previous code: 11) is no longer available. |
| `senderOffice` | complex | 0 | unbounded |  |
| `senderOffice/officeName` | xs:string | 0 | 1 | Can appear either in the meta data (onBehalfOf) or as it is. |

