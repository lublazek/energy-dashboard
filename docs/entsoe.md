# ENTSO-E Transparency Platform — REST API reference

A local copy of the ENTSO-E API structure so you don't have to browse the Postman docs every
time. Covers **all 77 documented endpoints** across the 8 data domains, plus the code tables you
need to build a request by hand.

This documents the **raw REST API** — plain HTTP + XML. That is deliberate: it's the ground
truth, and it's the fallback whenever `entsoe-py` misbehaves. What this project currently calls
through `entsoe-py` is in [§7](#7-what-this-project-uses).

> **Source**: the official Postman collection *"Transparency Platform Restful API"*
> (<https://documenter.getpostman.com/view/7009892/2s93JtP3F6>), captured **2026-08-21**.
> Every endpoint, parameter and code below was extracted from the collection JSON, not
> hand-copied. Re-fetch it with:
> ```bash
> curl -sSL "https://documenter.gw.postman.com/api/collections/7009892/2s93JtP3F6" -o coll.json
> ```
> The Postman web page is a JavaScript app — plain fetching gets you an empty shell. Use that
> gateway URL instead.

---

## 1. Request fundamentals

**Base URL**

```
https://web-api.tp.entsoe.eu/api
```

Everything is a `GET` against this single endpoint. There are no paths — you select the dataset
entirely through query parameters. (A `POST` form is also supported for large parameter sets;
the collection includes one example, `6.1.A Actual Total Load (Post)`.)

**Authentication** — your API key, two interchangeable ways:

| Where | Name | Example |
|---|---|---|
| Query parameter | `securityToken` | `...&securityToken=<key>` |
| HTTP header | `SECURITY_TOKEN` | `SECURITY_TOKEN: <key>` |

Prefer the **header**. The query-parameter form ends up in logs, proxies, and shell history —
which is exactly how this project leaks its key at `LOG_LEVEL=DEBUG` (see [§6](#6-operational-gotchas)).

Keys are free but not self-service: register on the platform, then email `transparency@entsoe.eu`
asking for API access.

**Time period** — every request needs both:

| Parameter | Format | Note |
|---|---|---|
| `periodStart` | `yyyyMMddHHmm` | e.g. `202601010000` |
| `periodEnd` | `yyyyMMddHHmm` | e.g. `202601020000` |

**Always UTC**, with no timezone suffix — the format has nowhere to put one. Times are typically
given on the hour. Note that the examples in the collection use values like `202303032200`, i.e.
22:00 UTC, because that is midnight in CET/CEST — a reminder that a "day" in this data is a local
market day, not a UTC day.

**Response** is XML (some bulk endpoints return a ZIP of XML documents).

**The empty-response trap.** ENTSO-E returns **HTTP 200 with an XML body containing
`No matching data found`** when your parameters are valid but there's simply nothing there. It is
not a 404. Any direct client must check the body text, not just the status code:

```python
if "No matching data found" in response.text:
    ...   # valid query, no data — not an error
```

Genuine parameter errors come back as HTTP 400 with an explanatory `<text>` element. Recognisable
messages include `check you request against dependency tables` (bad parameter combination),
`is not valid for this area` (wrong `psrType` for that country), and
`amount of requested data exceeds allowed limit` (response too large — paginate).

**Request period limits.** Each endpoint caps how long a window one request may cover — mostly
**1 year**, but some are much tighter. Exceeding the cap is an error, so long histories must be
fetched in chunks. The per-endpoint limit is the last column of every table in [§3](#3-endpoint-catalogue).
The tight ones are worth memorising:

| Endpoint | Limit |
|---|---|
| 16.1.A Actual Generation per Generation Unit | **1 day** |
| IF aFRR 3.16 Cross Border Marginal Prices for aFRR CS | **1 day** |
| IFs 3.10/3.16/3.17 Netted and Exchanged Volumes (+ per Border) | **1 day** for aFRR, 1 year otherwise |
| 12.3.A Current balancing state | **100 days** |

**Response size limits and pagination.** Many endpoints return at most **100 TimeSeries elements**
(or 100 documents inside a ZIP) per response. Where supported, the `offset` parameter pages
through in blocks of 100: `offset=n` returns items `n+1 … n+100`. If a query is too big and the
endpoint has no `offset`, narrow the period instead.

**Rate limit.** The platform guide states a limit of roughly **400 requests per minute per user**.
I could not load the guide page directly to confirm the exact figure (it returned 400/403 to
automated fetches), so treat this as approximate — but it's the right order of magnitude, and far
above anything this project does.

---

## 2. How a query is composed

Every request is the same shape:

```
GET https://web-api.tp.entsoe.eu/api
    ?documentType=<what kind of document>
    [&processType=<which time horizon / reserve process>]
    [&businessType=<which flavour of the document>]
    [&<some>_Domain=<EIC code>]
    &periodStart=...&periodEnd=...
    &securityToken=...
```

- **`documentType`** is the primary selector — "system total load", "imbalance volume", etc.
- **`processType`** narrows the time horizon (day-ahead / week-ahead / realised) or the balancing
  reserve type (FCR / aFRR / mFRR / RR).
- **`businessType`** distinguishes variants within one document type.
- **A domain parameter** carries the EIC code of the area. *Which* parameter name it is varies by
  dataset — see [§5](#5-domain-parameters). Getting this wrong is the single most common mistake.

**Codes are namespaced per parameter.** The same string means different things in different
parameters — `A44` is *"Price Document"* as a `documentType` but *"Intraday"* as a `processType`;
`B33` is *"Published offered capacity"* as a `documentType` but *"Area Control Error"* as a
`businessType`. Always read a code against the table for the parameter it sits in.

**Parameter names are case-insensitive.** The collection mixes `documentType`/`DocumentType` and
`out_Domain`/`Out_Domain` freely across examples, and both work.

**Worked example — actual load for Czechia:**

```
https://web-api.tp.entsoe.eu/api
  ?documentType=A65               # System total load
  &processType=A16                # Realised
  &outBiddingZone_Domain=10YCZ-CEPS-----N
  &periodStart=202603030000
  &periodEnd=202603060000
```

---

## 3. Endpoint catalogue

All 77 requests, grouped as the collection groups them. The number in each heading is the count of
endpoints in that domain. "Other key params" lists the mandatory discriminators beyond
`documentType`; every endpoint also takes `periodStart`, `periodEnd` and the security token, and
almost all accept an optional `curveType`.

Names like `12.1.D` or `16.1.B&C` are references to the articles of EU Regulation 543/2013, which
is what the Transparency Platform is legally required to publish. The same numbering labels the
pages on the platform website, so it's the reliable way to match a web page to an API call.

### Market  (13)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| 12.1.D Energy Prices | `A44` | dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.E Implicit and Flow-based Allocations - Congestion Income | `A25` | `businessType=B10` `contract_MA.Type=A01` dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.E Implicit Auction — Net Positions | `A25` | `businessType=B09` `contract_MA.Type=A07` dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.A Explicit Allocations - Use of the Transfer Capacity | `A25` | `businessType=B05` `contract_MA.Type=A07` dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.A Explicit Allocations - Auction Revenue | `A25` | `businessType=B07` `contract_MA.Type=A01` dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.B Total Nominated Capacity | `A26` | `businessType=B08` dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.C Total Capacity Already Allocated | `A26` | `businessType=A29` `contract_MA.Type=A01` dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.H Transfer Capacities Allocated with Third Countries [12.1.H] (explicit) | `A94` | `auction.Type=A02` `contract_MA.Type=A07` dom: `in_Domain`, `out_Domain` | 1 year |
| 11.1 Implicit Allocations - Offered Transfer Capacity | `A31` | `auction.Type=A01` `contract_MA.Type=A01` dom: `in_Domain`, `out_Domain` | 1 year |
| 11.1.A Explicit Allocations - Offered Transfer Capacity | `A31` | `auction.Type=A02` `contract_MA.Type=A01` dom: `in_Domain`, `out_Domain` | 1 year |
| 11.1 Continuous Allocations - Offered Transfer Capacity | `A31` | `auction.Type=A08` `contract_MA.Type=A07` dom: `In_Domain`, `Out_Domain` | 1 year |
| 11.1.B Flow Based Allocations | `B09` | `processType=A44` dom: `in_Domain`, `out_Domain` | 1 year |
| 11.1.B Flow Based Allocations Archives | `B09` | `processType=A32` dom: `in_Domain`, `out_Domain` + `StorageType=archive` | 1 year |

**Day-ahead prices** are `documentType=A44` with `in_Domain` **equal to** `out_Domain` (both the
bidding zone). Add `contract_MarketAgreement.type=A07` for intraday prices instead.

### Load  (8)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| 6.1.A Actual Total Load | `A65` | `processType=A16` dom: `outBiddingZone_Domain` | 1 year |
| 6.1.A Actual Total Load — SECURITY_TOKEN in header | `A65` | `processType=A16` dom: `outBiddingZone_Domain` | 1 year |
| 6.1.A Actual Total Load (POST) | `A65` | same, sent as a POST body | 1 year |
| 6.1.B Day-ahead Total Load Forecast | `A65` | `processType=A01` dom: `outBiddingZone_Domain` | 1 year |
| 6.1.C Week-ahead Total Load Forecast | `A65` | `processType=A31` dom: `outBiddingZone_Domain` | 1 year |
| 6.1.D Month-ahead Total Load Forecast | `A65` | `processType=A32` dom: `outBiddingZone_Domain` | 1 year |
| 6.1.E Year-ahead Total Load Forecast | `A65` | `processType=A33` dom: `outBiddingZone_Domain` | 1 year |
| 8.1 Year-ahead Forecast Margin | `A70` | `processType=A33` dom: `outBiddingZone_Domain` | 1 year |

The whole Load family is one `documentType` (`A65`) with `processType` selecting the horizon —
the cleanest illustration of how the API is organised.

### Generation  (7)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| 16.1.B&C Actual Generation per Production Type | `A75` | `processType=A16` dom: `in_Domain` | 1 year |
| 16.1.A Actual Generation per Generation Unit | `A73` | `processType=A16` dom: `in_Domain` | **1 day** |
| 16.1.D Water Reservoirs and Hydro Storage Plants | `A72` | `processType=A16` dom: `in_Domain` | 1 year |
| 14.1.A Installed Capacity per Production Type | `A68` | `processType=A33` dom: `in_Domain` | 1 year |
| 14.1.B Installed Capacity Per Production Unit | `A71` | `processType=A33` dom: `in_Domain` | — |
| 14.1.C Generation Forecast - Day ahead | `A71` | `processType=A01` dom: `in_Domain` | 1 year |
| 14.1.D Generation Forecasts for Wind and Solar | `A69` | `processType=A01` dom: `in_Domain` | 1 year |

Two notes carried verbatim from the collection:

- `A74` (wind & solar only) and `A75` (all production types) **return the same response**. The
  filtering is done by `psrType`, not by the document type.
- In the forecast documents, a TimeSeries with an `inBiddingZone_Domain` attribute is
  **generation**, while `outBiddingZone_Domain` is **consumption**. Mixing them up inflates your
  totals — the raw-XML equivalent of the pumped-storage trap in [§7](#7-what-this-project-uses).

### Transmission  (10)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| 12.1.G Cross-Border Physical Flows | `A11` | dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.F Commercial Schedules | `A09` | dom: `in_Domain`, `out_Domain` | 1 year |
| 12.1.F Commercial Schedules - Net Positions | `A09` | `businessType=B09` dom: `in_Domain`, `out_Domain` | 1 year |
| 11.1.A Forecasted Transfer Capacities | `A61` | `contract_MA.Type=A01` dom: `in_Domain`, `out_Domain` | 1 year |
| 11.3 Cross Border Capacity of DC Links - Intraday Transfer Limits | `A93` | dom: `in_Domain`, `out_Domain` | 1 year |
| 13.1.A Redispatching Internal | `A63` | `businessType=A85` dom: `in_Domain`, `out_Domain` | 1 year |
| 13.1.A Redispatching Cross Border | `A63` | `businessType=A46` dom: `in_Domain`, `out_Domain` | 1 year |
| 13.1.B Countertrading | `A91` | dom: `in_Domain`, `out_Domain` | 1 year |
| 13.1.C Costs of Congestion Management | `A92` | dom: `in_Domain`, `out_Domain` | 1 year |
| 9.1 Expansion and Dismantling Project | `A90` | dom: `in_Domain`, `out_Domain` | 1 year |

Cross-border physical flows: **the API returns values per direction, not netted** — unlike the web
GUI, which nets them. To get a net flow you must fetch both directions and subtract.

### Outages  (8)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| 15.1.A&B Unavailability of Generation Units | `A80` | dom: `BiddingZone_Domain` | 1 year |
| 15.1.C-D Unavailability of Production Units | `A77` | dom: `BiddingZone_Domain` | 1 year |
| 7.1.A-B Aggregated Unavailability of Consumption Units | `A76` | dom: `BiddingZone_Domain` | 1 year |
| 10.1.A&B Unavailability of Transmission Infrastructure | `A78` | dom: `In_Domain`, `Out_Domain` | 1 year |
| 10.1.A&B Unavailability of Transmission Infrastructure - Available Capacity | `A78` | dom: `ControlArea_Domain` | 1 year |
| 10.1.A&B Unavailability of Transmission Infrastructure - Net Position Impact | `A78` | dom: `pTDF_Domain.mRID` | 1 year |
| 10.1.C Unavailability of Offshore Grid Infrastructure | `A79` | dom: `BiddingZone_Domain` | 1 year |
| Fall-backs [IFs IN 7.2, mFRR 3.11, aFRR 3.10] | `A53` | `processType=A51` `businessType=C47` dom: `BiddingZone_Domain` | 1 year |

Outage endpoints additionally accept `periodStartUpdate` / `periodEndUpdate` to filter by when the
outage record was *published or revised* rather than when the outage occurs. When those are
present, the 1-year cap applies to them instead of to `periodStart`/`periodEnd`. Use `docStatus`
to include or exclude cancelled and withdrawn notices.

### Balancing  (29)

The largest and messiest domain. `documentType=A26` (Capacity document) is heavily overloaded here
— `processType` + `businessType` together do the real selecting.

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| 17.1.G Imbalance prices | `A85` | dom: `controlArea_Domain` | 1 year |
| 17.1.H Total Imbalance Volumes | `A86` | dom: `controlArea_Domain` | 1 year |
| 17.1.F Prices of Activated Balancing Energy | `A84` | `processType=A16` `businessType=A96` dom: `controlArea_Domain` | 1 year |
| 17.1.I Financial Expenses and Income for Balancing | `A87` | dom: `controlArea_Domain` | 1 year |
| 17.1.B&C Volumes and Prices of Contracted Reserves | `A81` | `processType=A52` `businessType=B95` `type_MA.Type=A01` dom: `controlArea_Domain` | 1 year |
| 12.3.A Current balancing state [GL EB] | `A86` | `businessType=B33` dom: `area_Domain` | **100 days** |
| 12.3.B&C Balancing energy bids | `A37` | `processType=A47` `businessType=B74` dom: `connecting_Domain` | 1 year |
| 12.3.B&C Balancing energy bids archives | `A37` | `processType=A47` `businessType=B74` dom: `connecting_Domain` + `storageType=archive` | 1 year |
| 12.3.E Aggregated Balancing Energy Bids (GL EB) | `A24` | `processType=A51` dom: `area_Domain` | 1 year |
| 12.3.F Procured balancing capacity (GL EB) | `A15` | `processType=A51` `type_MA.Type=A01` dom: `area_Domain` | 1 year |
| 12.3.H&I Allocation and use of cross-zonal balancing capacity | `A38` | `processType=A51` dom: `Acquiring_Domain`, `Connecting_Domain` | 1 year |
| IF aFRR 3.16 Cross Border Marginal Prices (CBMPs) for aFRR Central Selection (CS) | `A84` | `processType=A67` `businessType=A96` `std_MarketProduct=A01` dom: `controlArea_Domain` | **1 day** |
| IFs 3.10, 3.16 & 3.17 Netted and Exchanged Volumes | `B17` | `processType=A63` dom: `Acquiring_Domain`, `Connecting_Domain` | 1 day (aFRR) |
| IFs 3.10, 3.16 & 3.17 Netted and Exchanged Volumes per Border | `A30` | `processType=A60` dom: `Acquiring_Domain`, `Connecting_Domain` | 1 day (aFRR) |
| IFs aFRR 3.4 & mFRR 3.4 Elastic Demands | `A37` | `processType=A47` `businessType=B75` dom: `Acquiring_Domain` | 1 year |
| IFs mFRR 9.9, aFRR 9.6&9.8 Changes to Bid Availability | `B45` | `processType=A47` dom: `Domain` | 1 year |
| IFs mFRR 9.9, aFRR 9.6&9.8 Changes to Bid Availability Archives | `B45` | `processType=A47` dom: `Domain` + `storageType=archive` | 1 year |
| IFs 4.3 & 4.4 Balancing Border Capacity Limitations | `A31` | `processType=A47` `businessType=A26` dom: `In_Domain`, `Out_Domain` | 1 year |
| IFs 4.5 Permanent Allocation Limitations to Cross-border Capacity on HVDC Lines | `A99` | `processType=A63` `businessType=B06` dom: `In_Domain`, `Out_Domain` | 1 year |
| 187.2 FCR Total capacity (SO GL) | `A26` | `businessType=A25` dom: `area_Domain` | 1 year |
| 187.2 Shares of FCR capacity (SO GL) | `A26` | `businessType=C23` dom: `area_Domain` | 1 year |
| 188.3 & 189.2 FRR & RR Capacity Outlook (SO GL) | `A26` | `processType=A56` `businessType=C76` dom: `area_Domain` | 1 year |
| 188.4 & 189.3 FRR and RR Actual Capacity (SO GL) | `A26` | `processType=A56` `businessType=C77` dom: `area_Domain` | 1 year |
| 189.2 Outlook of Reserve Capacities on RR (SO GL) | `A26` | `processType=A46` `businessType=C76` dom: `area_Domain` | — |
| 189.3 RR Actual Capacity (SO GL) | `A26` | `processType=A46` `businessType=C77` dom: `area_Domain` | — |
| 190.1 Sharing of RR and FRR (SO GL) | `A26` | `processType=A51` `businessType=C22` dom: `Area_Domain` | 1 year |
| 190.2 Sharing of FCR between SAs (SO GL) | `A26` | `processType=A52` `businessType=C22` dom: `area_Domain` | 1 year |
| 190.3 Exchanged Reserve Capacity (SO GL) | `A26` | `processType=A46` `businessType=C21` dom: `Acquiring_Domain`, `Connecting_Domain` | 1 year |
| 185.4 Results of the Criteria Application Process - Measurements (SO GL) | `A45` | `processType=A65` dom: `area_domain` | 1 year |

**Imbalance volumes** (`A86`) default to `businessType=A19` (Balance Energy Deviation) when
`businessType` is omitted — which is what this project relies on.

### Master Data  (1)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| Production and Generation Units | `A95` | `businessType=B11` dom: `BiddingZone_Domain` | — |

Takes `Implementation_DateAndOrTime` (format `YYYY-MM-DD`) instead of a period — production and
generation units change over their lifecycle (capacity revisions, production-type changes,
decommissioning), so you query the register *as of* a date.

### OMI — Other Market Information  (1)

| Endpoint | documentType | Other key params | Max period |
|---|---|---|---|
| Other Market Information | `B47` | dom: `ControlArea_Domain` | 1 year |

---

## 4. Code tables

Extracted from the parameter descriptions in the collection. These are the codes *used by the
documented endpoints* — the underlying ENTSO-E code lists are longer, but anything outside these
is unlikely to be accepted.

### documentType

| Code | Meaning |
|---|---|
| `A09` | Finalised schedule |
| `A11` | Aggregated energy data report |
| `A15` | Acquiring system operator reserve schedule |
| `A24` | Bid document |
| `A25` | Allocation result document |
| `A26` | Capacity document |
| `A30` | Cross border schedule |
| `A31` | Agreed capacity |
| `A37` | Reserve bid document |
| `A38` | Reserve allocation result document |
| `A44` | Price document |
| `A45` | Measurement value document |
| `A53` | Outage publication document |
| `A61` | Estimated net transfer capacity |
| `A63` | Redispatch notice |
| `A65` | System total load |
| `A68` | Installed generation per type |
| `A69` | Wind and solar forecast |
| `A70` | Load forecast margin |
| `A71` | Generation forecast |
| `A72` | Reservoir filling information |
| `A73` | Actual generation |
| `A74` | Wind and solar generation only |
| `A75` | Actual generation per type |
| `A76` | Load unavailability |
| `A77` | Production unit unavailability |
| `A78` | Transmission unavailability |
| `A79` | Offshore grid infrastructure unavailability |
| `A80` | Generation unavailability |
| `A81` | Contracted reserves |
| `A84` | Activated balancing prices |
| `A85` | Imbalance prices |
| `A86` | Imbalance volume |
| `A87` | Financial situation |
| `A90` | Interconnector network expansion |
| `A91` | Counter trade notice |
| `A92` | Congestion costs |
| `A93` | DC link capacity |
| `A94` | Non-EU allocations |
| `A95` | Configuration document |
| `A99` | HVDC link constraints |
| `B09` | Flow-based domain publication |
| `B17` | Aggregated netted external TSO schedule document |
| `B33` | Published offered capacity |
| `B45` | Bid availability document |
| `B47` | Other market information |

### processType

| Code | Meaning |
|---|---|
| `A01` | Day ahead |
| `A16` | Realised |
| `A18` | Current |
| `A31` | Week ahead |
| `A32` | Month ahead |
| `A33` | Year ahead |
| `A40` | Intraday |
| `A43` | Day ahead *(flow-based context)* |
| `A44` | Intraday *(flow-based context)* |
| `A46` | Replacement reserve (RR) |
| `A47` | Manual frequency restoration reserve (mFRR) |
| `A51` | Automatic frequency restoration reserve (aFRR) |
| `A52` | Frequency containment reserve (FCR) |
| `A56` | Frequency restoration reserve (FRR) |
| `A60` | Scheduled activation mFRR |
| `A61` | Direct activation mFRR |
| `A63` | Imbalance netting |
| `A64` | Criteria application for instantaneous frequency (for SNA) |
| `A65` | Criteria application for frequency restoration (for LFC block) |
| `A67` | Central selection aFRR |
| `A68` | Local selection aFRR |

### businessType

| Code | Meaning |
|---|---|
| `A19` | Balance energy deviation |
| `A25` | General capacity information |
| `A26` | Available transfer capacity |
| `A29` | Already allocated capacity |
| `A43` | Requested capacity |
| `A46` | System operator re-dispatching |
| `A53` | Planned maintenance |
| `A54` | Forced unavailability |
| `A83` | Auction cancellation |
| `A85` | Internal requirements |
| `A95` | Frequency containment reserve |
| `A96` | Automatic frequency restoration reserve |
| `A97` | Manual frequency restoration reserve |
| `A98` | Replacement reserve |
| `B01` | Interconnector network evolution |
| `B02` | Interconnector network dismantling |
| `B05` | Capacity allocated |
| `B06` | DC link constraint |
| `B07` | Auction revenue |
| `B08` | Total nominated capacity |
| `B09` | Net position |
| `B10` | Congestion income |
| `B11` | Production unit |
| `B33` | Area control error |
| `B74` | Offer |
| `B75` | Need |
| `B95` | Procured capacity |
| `C21` | Exchanged balancing reserve capacity |
| `C22` | Shared balancing reserve capacity |
| `C23` | Share of reserve capacity |
| `C40` | Conditional bid |
| `C41` | Thermal limit |
| `C42` | Frequency limit |
| `C43` | Voltage limit |
| `C44` | Current limit |
| `C45` | Short-circuit current limit |
| `C46` | Dynamic stability limit |
| `C47` | Disconnection |
| `C76` | Forecasted capacity |
| `C77` | Min |
| `C78` | Avg |
| `C79` | Max |

### psrType — production types

Used to filter generation and capacity queries. Optional: omit it to get every type.

| Code | Meaning | This project's category |
|---|---|---|
| `B01` | Biomass | `biomass` |
| `B02` | Fossil Brown coal/Lignite | `lignite` |
| `B03` | Fossil Coal-derived gas | `gas` |
| `B04` | Fossil Gas | `gas` |
| `B05` | Fossil Hard coal | `hard_coal` |
| `B06` | Fossil Oil | `other` |
| `B07` | Fossil Oil shale | `other` |
| `B08` | Fossil Peat | `other` |
| `B09` | Geothermal | `other` |
| `B10` | Hydro Pumped Storage | `hydro` |
| `B11` | Hydro Run-of-river and poundage | `hydro` |
| `B12` | Hydro Water Reservoir | `hydro` |
| `B13` | Marine | `other` |
| `B14` | Nuclear | `nuclear` |
| `B15` | Other renewable | `other` |
| `B16` | Solar | `solar` |
| `B17` | Waste | `other` |
| `B18` | Wind Offshore | `wind` |
| `B19` | Wind Onshore | `wind` |
| `B20` | Other | `other` |
| `B25` | **Energy storage** | *(unmapped — falls through to `other`)* |

`A03` (Mixed), `A04` (Generation) and `A05` (Load) also appear as `psrType` values, but only in
the balancing endpoints, where the parameter means something different — generation vs. load side
rather than a fuel.

> **B25 is not in this project's mapping.** `GENERATION_TYPE_MAP` in
> [psr_types.py](../backend/providers/entsoe/psr_types.py) covers B01–B20 but not B25, so any
> energy-storage output silently lands in `other` via the fallback. Harmless today — CZ doesn't
> report it — but worth knowing before adding a country that does. See [§7](#7-what-this-project-uses).

### curveType

| Code | Meaning |
|---|---|
| `A01` | Sequential fixed block |
| `A03` | Variable sized blocks **(default)** |

### contract_MarketAgreement.Type

| Code | Meaning |
|---|---|
| `A01` | Daily / day-ahead |
| `A02` | Weekly |
| `A03` | Monthly |
| `A04` | Yearly |
| `A05` | Total |
| `A06` | Long term |
| `A07` | Intraday |
| `A08` | Quarterly |

### type_MarketAgreement.Type

| Code | Meaning |
|---|---|
| `A01` | Daily |
| `A02` | Weekly |
| `A03` | Monthly |
| `A04` | Yearly |
| `A05` | Total |
| `A06` | Long term |
| `A07` | Intraday |
| `A13` | Hourly |

### auction.Type / auction.Category

| Code | auction.Type | | Code | auction.Category |
|---|---|---|---|---|
| `A01` | Implicit | | `A01` | Base |
| `A02` | Explicit | | `A02` | Peak |
| `A08` | Continuous | | `A03` | Off peak |
| | | | `A04` | Hourly |

### Market products

| Code | Standard_MarketProduct | | Code | Original_MarketProduct |
|---|---|---|---|---|
| `A01` | Standard | | `A02` | Specific |
| `A05` | Standard mFRR scheduled activation | | `A03` | Integrated process |
| `A07` | Standard mFRR direct activation | | `A04` | Local |

### docStatus — for outage queries

| Code | Meaning |
|---|---|
| `A01` | Intermediate |
| `A02` | Final |
| `A05` | Active |
| `A09` | Cancelled |
| `A13` | Withdrawn |

---

## 5. Domain parameters

The area parameter name varies by dataset. Using the wrong one is the most common cause of an
empty or rejected response.

| Parameter | Used by | Holds |
|---|---|---|
| `outBiddingZone_Domain` | Load (all), forecast margin | Control area, bidding zone or country |
| `in_Domain` | Generation (all) | Control area, bidding zone or country |
| `in_Domain` + `out_Domain` | Market, Transmission | Two areas — a border. **For day-ahead prices both must be the same bidding zone.** |
| `controlArea_Domain` | Balancing (17.1.x), OMI | Scheduling area / market balance area / LFA / IPA / SCA |
| `area_Domain` | Balancing (12.3.x, SO GL) | Scheduling area |
| `BiddingZone_Domain` | Outages, Master Data | Bidding zone |
| `Acquiring_Domain` + `Connecting_Domain` | Cross-border balancing | Two areas |
| `connecting_Domain` | Balancing energy bids | One area |
| `ControlArea_Domain` | Transmission unavailability (available capacity) | Control area |
| `pTDF_Domain.mRID` | Transmission unavailability (net position impact) | Bidding zone |

Values are **EIC codes** — 16-character identifiers, dash-padded. Some seen in the collection:

| Area | EIC |
|---|---|
| Czechia (ČEPS) | `10YCZ-CEPS-----N` |
| Austria (APG) | `10YAT-APG------L` |
| Germany (50Hertz / VE) | `10YDE-VE-------2` |
| Germany (Amprion / RWE) | `10YDE-RWENET---I` |
| Germany (TransnetBW / EnBW) | `10YDE-ENBW-----N` |
| France (RTE) | `10YFR-RTE------C` |
| Belgium | `10YBE----------2` |
| Netherlands | `10YNL----------L` |
| Great Britain | `10YGB----------A` |
| Spain (REE) | `10YES-REE------0` |
| Hungary (MAVIR) | `10YHU-MAVIR----U` |
| Slovakia (SEPS) | `10YSK-SEPS-----K` |
| Croatia (HEP) | `10YHR-HEP------M` |
| Denmark DK1 / DK2 | `10YDK-1--------W` / `10YDK-2--------M` |
| Finland | `10YFI-1--------U` |
| Bulgaria | `10YCA-BULGARIA-R` |
| Continental Europe synchronous area | `10YEU-CONT-SYNC0` |
| CORE / flow-based region | `10YDOM-REGION-1V` |

The full EIC register is published separately by ENTSO-E; this project keeps the ones it needs in
[config/countries.yaml](../config/countries.yaml).

---

## 6. Operational gotchas

- **`No matching data found` arrives as HTTP 200.** Covered in [§1](#1-request-fundamentals), but
  it's the one that costs the most debugging time — repeated here on purpose.
- **Imbalance is genuinely sparse for CZ** — roughly 6–8 points per day. Authentic data, not a
  fetch bug. Don't "fix" a chart that looks empty between points.
- **503s happen.** The platform goes down for stretches. Fetch errors surface in `/api/health`
  while the scheduler keeps running and retries on its next interval.
- **Day-ahead prices publish once daily**, around midday for the following day. Polling more often
  returns identical data.
- **Never put the token in a logged URL.** `LOG_LEVEL=DEBUG` makes this project log full request
  URLs including `securityToken`. Use the `SECURITY_TOKEN` header instead, and redact before
  sharing logs.
- **Long histories must be chunked** to respect the per-endpoint period cap — see the limits in
  [§3](#3-endpoint-catalogue).

---

## 7. What this project uses

Five series, fetched through the **raw REST API** (`requests` + stdlib XML) —
`backend/providers/entsoe/raw_client.py` does the HTTP, `xml_parsers.py` the parsing, and the
normalizers absorb every remaining quirk. entsoe-py was removed on 2026-08-25.

| Series | Raw request | Value element | Response root |
|---|---|---|---|
| `day_ahead_prices` | `documentType=A44`, `in_Domain` = `out_Domain` | `price.amount` | `Publication_MarketDocument` |
| `load` | `documentType=A65&processType=A16`, `outBiddingZone_Domain` | `quantity` | `GL_MarketDocument` |
| `generation` | `documentType=A75&processType=A16`, `in_Domain` | `quantity` | `GL_MarketDocument` |
| `imbalance` | `documentType=A86`, `controlArea_Domain` | `quantity` | `Balancing_MarketDocument` |
| `imbalance_prices` | `documentType=A85`, `controlArea_Domain` | `imbalance_Price.amount` | `Balancing_MarketDocument` |

Every enabled country carries one `eic` in `config/countries.yaml` that serves all five domain
parameters. Germany uses the DE-LU bidding-zone EIC (`10Y1001A1001A82H`) — ENTSO-E aggregates the
four German TSO control areas into it, imbalance included.

Quirks the code absorbs, learned by running it:

- **Multi-document responses arrive zipped.** When the window spans several documents — routine
  for A85/A86 — the body is a zip archive (`PK…` magic) whose members are one XML document each.
  `raw_client._get` detects and unpacks this; the `parse_*_documents` wrappers merge the members,
  later documents winning on overlapping timestamps.
- **Imbalance volumes are published unsigned**, with the sign in the TimeSeries'
  `flowDirection.direction` (`A01` = surplus → positive, `A02` = deficit → negated).
- **Imbalance prices are settled in the national currency** — CZK for ČEPS, PLN for PSE — carried
  in `currency_Unit.name`. The unit is read from the response, never declared.
- **Omitted positions repeat the previous value** (see below); the parser fills them, so NaN in
  parsed output means "this TimeSeries genuinely ends here", which is what the ragged-tail trim
  keys on.

### The raw request

Every series is reachable with `requests` + an XML parse — no library needed:

```python
import requests

r = requests.get(
    "https://web-api.tp.entsoe.eu/api",
    params={
        "documentType": "A86",                    # imbalance volume
        "controlArea_Domain": "10YCZ-CEPS-----N",
        "periodStart": "202608200000",
        "periodEnd": "202608210000",
    },
    headers={"SECURITY_TOKEN": api_key},          # not in the URL
    timeout=30,
)
r.raise_for_status()
if "No matching data found" in r.text:
    ...   # valid query, no data
```

The response is a `GL_MarketDocument` (or `Publication_MarketDocument` for prices,
`Balancing_MarketDocument` for imbalance) containing `TimeSeries` → `Period` → `Point` elements,
each with a `position` and a value. `position` is a 1-based index into the period, not a
timestamp — you reconstruct the timestamp from the period's `start` plus `resolution` (e.g.
`PT60M`, `PT15M`) times `position - 1`. **Positions with no value are omitted**, meaning a gap
repeats the previous value rather than being missing — `xml_parsers._walk_period` implements
exactly this fill, extending a trailing omission to the period's end.

### The MultiIndex quirk was an entsoe-py artifact, not the API (historical)

Worth being precise about, since it shapes `normalize_generation`. The raw API returns
`TimeSeries` elements each carrying a `psrType` B-code. **`entsoe-py` reshapes that** into a
DataFrame with MultiIndex columns, translating B-codes into human-readable names via its own
`PSRTYPE_MAPPINGS` table:

```
columns = MultiIndex([('Nuclear',              'Actual Aggregated'),
                      ('Fossil Gas',           'Actual Aggregated'),
                      ('Hydro Pumped Storage', 'Actual Aggregated'),
                      ('Hydro Pumped Storage', 'Actual Consumption'),   # ← not generation
                      ...])
```

- Level 0 is the **production type name** (`'Fossil Brown coal/Lignite'`), not the B-code.
  `psr_types.GENERATION_TYPE_MAP` is keyed by these names for that reason — and its keys match
  entsoe-py's `PSRTYPE_MAPPINGS` values exactly (verified against entsoe-py 0.8.0), except for
  the missing `B25 = Energy storage` noted in [§4](#psrtype--production-types).
- Level 1 is the aggregation. `'Actual Aggregated'` is generation; `'Actual Consumption'` is
  pumped storage *drawing* power — that is load, not generation. Include it and hydro inflates.
- The column set varies by country and window. Types with no output may be absent entirely rather
  than present-and-zero, so never index columns positionally.

**This happened on 2026-08-25**: the project dropped `entsoe-py`, reads `psrType` B-codes
straight off the XML, and `psr_types.PSR_CODE_MAP` is keyed by B-code — the more stable
identifier. The consumption exclusion survives in a different form: a generation `TimeSeries`
carrying `outBiddingZone_Domain.mRID` is pumped storage drawing power and
`xml_parsers.parse_generation_xml` skips it. The section above is kept as a record of why the
old code looked the way it did.

---

## 8. Sources

- Postman collection (primary source for everything above) —
  <https://documenter.getpostman.com/view/7009892/2s93JtP3F6>
- Machine-readable form —
  `https://documenter.gw.postman.com/api/collections/7009892/2s93JtP3F6`
- Transparency Platform (web GUI) — <https://transparency.entsoe.eu/>
- Knowledge base — <https://transparencyplatform.zendesk.com/>
- API user guide — linked from the platform's static content; blocks automated fetching, so open
  it in a browser
