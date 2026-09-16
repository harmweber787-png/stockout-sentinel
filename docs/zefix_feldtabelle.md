# Zefix-Feldtabelle: Zefix Public REST API (live erhoben am 2026-09-16)

Erhoben mit `scripts/probe_zefix.py`. Die Felder stammen aus der OpenAPI-
Beschreibung der API, die ohne Login lesbar ist. Die Daten-Endpunkte verlangen
Basic-Auth; ohne Zugangsdaten gab es keine Stichprobe, deshalb ist die
Abdeckung je Feld **nicht gemessen**. Sobald `ZEFIX_USER`/`ZEFIX_PASSWORD`
gesetzt sind, misst derselbe Aufruf die Abdeckung an wenigen Stichproben.

```bash
python scripts/probe_zefix.py --dry-run
python scripts/probe_zefix.py --json-out docs/zefix_feldtabelle.json
```

Nicht benutzt: die interne Web-API des Portals (`/ZefixREST`) antwortet ohne
Login, steht aber unter `Disallow: /` in der robots.txt von
`www.zefix.admin.ch` und ist nicht die dokumentierte Schnittstelle.

* Basis-URL: `https://www.zefix.admin.ch/ZefixPublicREST`
* API-Version laut OpenAPI: `2.7.2.3`
* Authentifizierung: Zefix-Credentials: http/basic
* Zugangsdaten verwendet: nein
* Hinweis: HTTP 401 fuer https://www.zefix.admin.ch/ZefixPublicREST/api/v1/legalForm: Zugangsdaten noetig

## Endpunkte

| Methode | Pfad | Antwort |
|---|---|---|
| POST | `/api/v1/company/search` | `array<CompanyShort>` |
| GET | `/api/v1/sogc/{id}` | `SogcPublicationAndCompanyShort` |
| GET | `/api/v1/sogc/bydate/{date}` | `array<SogcPublicationAndCompanyShort>` |
| GET | `/api/v1/registryOfCommerce` | `array<RegistryOfCommerce>` |
| GET | `/api/v1/registryOfCommerce/byBfsCommunityId/{id}` | `RegistryOfCommerce` |
| GET | `/api/v1/legalForm` | `array<LegalForm>` |
| GET | `/api/v1/company/uid/{id}` | `array<CompanyFull>` |
| GET | `/api/v1/company/ehraid/{id}` | `CompanyFull` |
| GET | `/api/v1/company/chid/{id}` | `array<CompanyFull>` |
| GET | `/api/v1/community` | `array<BfsCommunity>` |

## Felder laut OpenAPI

### CompanyFull

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `name` | `string` | - | nein | - | primary business name of the company |
| `ehraid` | `integer` | int64 | nein | - | Internal company unique ID used by federal registry of commerce |
| `uid` | `string` | - | nein | - | UID number, CHE... |
| `chid` | `string` | - | nein | - | CH-ID (old CH-number with 13 digits, which is no longer used in public) |
| `legalSeatId` | `integer` | int64 | nein | - | Legal seat ID (commune number according to the swiss official commune register) |
| `legalSeat` | `string` | - | nein | - | Legal seat name (name of the political commune) |
| `registryOfCommerceId` | `integer` | int64 | nein | - | Internal office number of the cantonal registry of commerce |
| `legalForm` | `LegalForm` | - | nein | - | legal form |
| `status` | `string` | - | nein | ACTIVE, CANCELLED, BEING_CANCELLED | Current company status (active, in liquidation, deleted) |
| `sogcDate` | `string` | date | nein | - | Date of the last publication in the SOGC |
| `deletionDate` | `string` | date | nein | - | Date of deletion of the legal unit |
| `translation` | `array<string>` | - | nein | - | company name translations |
| `purpose` | `string` | - | nein | - | purpose |
| `sogcPub` | `array<SogcPublication>` | - | nein | - | SOGC published publications regarding the registry of commerce |
| `address` | `Address` | - | nein | - | address |
| `canton` | `string` | - | nein | - | 2 character abbreviation of the canton |
| `capitalNominal` | `string` | - | nein | - | nominal capital (only available for corporations) |
| `capitalCurrency` | `string` | - | nein | - | currency of the nominal capital |
| `headOffices` | `array<CompanyShort>` | - | nein | - | head offices |
| `furtherHeadOffices` | `array<CompanyShort>` | - | nein | - | further head offices |
| `branchOffices` | `array<CompanyShort>` | - | nein | - | branch offices |
| `hasTakenOver` | `array<CompanyShort>` | - | nein | - | has taken over |
| `wasTakenOverBy` | `array<CompanyShort>` | - | nein | - | was taken over by |
| `auditCompanies` | `array<CompanyShort>` | - | nein | - | audit companies |
| `oldNames` | `array<CompanyOldName>` | - | nein | - | Previous names of the company |
| `cantonalExcerptWeb` | `string` | - | nein | - | Link to the excerpt of the cantonal commercial register (URL), based on RegistryOfCommerce url2/url4 |
| `zefixDetailWeb` | `DFIEString` | - | nein | - | Link to the detail view in Zefix (URL) |

### CompanyShort

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `name` | `string` | - | nein | - | primary business name of the company |
| `ehraid` | `integer` | int64 | nein | - | Internal company unique ID used by federal registry of commerce |
| `uid` | `string` | - | nein | - | UID number, CHE... |
| `chid` | `string` | - | nein | - | CH-ID (old CH-number with 13 digits, which is no longer used in public) |
| `legalSeatId` | `integer` | int64 | nein | - | Legal seat ID (commune number according to the swiss official commune register) |
| `legalSeat` | `string` | - | nein | - | Legal seat name (name of the political commune) |
| `registryOfCommerceId` | `integer` | int64 | nein | - | Internal office number of the cantonal registry of commerce |
| `legalForm` | `LegalForm` | - | nein | - | legal form |
| `status` | `string` | - | nein | ACTIVE, CANCELLED, BEING_CANCELLED | Current company status (active, in liquidation, deleted) |
| `sogcDate` | `string` | date | nein | - | Date of the last publication in the SOGC |
| `deletionDate` | `string` | date | nein | - | Date of deletion of the legal unit |

### Address

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `organisation` | `string` | - | nein | - | organisation name |
| `careOf` | `string` | - | nein | - | care of |
| `street` | `string` | - | nein | - | street |
| `houseNumber` | `string` | - | nein | - | house number |
| `addon` | `string` | - | nein | - | address addon |
| `poBox` | `string` | - | nein | - | PO box |
| `city` | `string` | - | nein | - | city |
| `swissZipCode` | `string` | - | nein | - | zip code |

### LegalForm

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `id` | `integer` | int64 | nein | - | Internal legal form ID used by the commercial register |
| `uid` | `string` | - | nein | - | Public legal form code according to the data standard eCH-0097 |
| `name` | `DFIEString` | - | nein | - | full name |
| `shortName` | `DFIEString` | - | nein | - | abbreviation |

### SogcPublication

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `sogcDate` | `string` | date | nein | - | Publication date of the SOGC |
| `sogcId` | `integer` | int64 | nein | - | Publication number of the particular publication in the SOGC(SOGC-ID) |
| `registryOfCommerceId` | `integer` | int64 | nein | - | Internal office number of the publishing cantonal registry of commerce |
| `registryOfCommerceCanton` | `string` | - | nein | - | Canton of the publishing registry of commerce |
| `registryOfCommerceJournalId` | `integer` | int64 | nein | - | Number of the daily register (of the publishing register of commerce) |
| `registryOfCommerceJournalDate` | `string` | date | nein | - | date of the daily register (of the publishing register of commerce) |
| `message` | `string` | - | nein | - | Formatted text of the publication |
| `mutationTypes` | `array<MutationType>` | - | nein | - | mutation types |

### MutationType

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `id` | `integer` | int32 | nein | - |  |
| `key` | `string` | - | nein | - |  |

### CompanyOldName

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `name` | `string` | - | nein | - | primary name |
| `sequenceNr` | `integer` | int64 | nein | - | The sequence number provides a hint about the age of the entry. A bigger number is older |
| `translation` | `array<string>` | - | nein | - | translations |

### CompanySearchQuery

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `name` | `string` | - | ja | - |  |
| `legalFormId` | `integer` | int64 | nein | - | If a legal form ID is provided, only companies matching the given legal form are returned. The LegalForm endpoint provides the allowed values. |
| `legalFormUid` | `string` | - | nein | - | If a legal form UID is provided, only companies matching the given legal form are returned. The LegalForm endpoint provides the allowed values. |
| `registryOfCommerceId` | `integer` | int64 | nein | - | If a registryofcommerceID is given, only companies which have their seat in this registry district are returned. The RegistryOfCommerce endpoint provides all allowed values. Not allowed together with legalSeatId or canton |
| `legalSeatId` | `integer` | int64 | nein | - | If a legal seat ID is given only companies with seat in the given political commune are returned. The Community endpoint provides all allowed values. Not allowed together with registryOfCommerceId or canton |
| `canton` | `string` | - | nein | - | Not allowed together with registryOfCommerceId or legalSeatId |
| `activeOnly` | `boolean` | - | nein | - |  |

### ErrorDetails

| Feld | Typ | Format | Pflicht | Enum | Beschreibung |
|---|---|---|---|---|---|
| `type` | `string` | - | nein | INTERNAL_SERVER_ERROR, INVALID_QUERY_WORDS, INVALID_REQUEST_DATA, RESULTLIST_TO_LARGE, NOT_FOUND | Type of error |
| `message` | `string` | - | nein | - | Details about the error |

## Abdeckung in der Stichprobe

Nicht gemessen: die Daten-Endpunkte verlangen Zugangsdaten. Mit `ZEFIX_USER`/`ZEFIX_PASSWORD` in der Umgebung misst das Skript die Abdeckung an Stichproben.
