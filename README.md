# Genealogy Evidence Forge

A local-first family-history workbench. Paste your research notes in plain text and get back:

- a **0-100 evidence-readiness score** across five axes (sourcing, consistency, coverage, disambiguation, researchability)
- **45+ findings**, each with the evidence that triggered it, why it matters, and a concrete next step
- an **identity analysis** that tells you when two records with the same name are probably two different people
- a **prioritised research plan** that names the record collections, the years and the fields to search
- **Markdown / CSV / JSON exports** and a local SQLite research log you can rate and annotate

Everything runs on your machine. No API keys, no network calls, no LLM.

## Why it exists

Most family-history software records what you believe. It rarely tells you whether what you believe is *evidenced*, whether two entries with the same name are the same person, or which record collection actually covers the decade you are stuck in. This tool does the second part: it reads your notes the way a careful genealogist would read them, and it tells you where to go next.

## Quick start

~~~
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
~~~

Then open http://127.0.0.1:5001

Press Cmd/Ctrl+Enter inside the notes box to analyse. Load one of the six built-in samples to see the engine work.

## How to write your notes

One person per block. Name on its own line, then labelled lines. A blank line (or a line of dashes) starts the next person.

~~~
John Albert Smith
Born: 12 March 1872, Warren County, Ohio
Died: 4 November 1943, Warren County, Ohio
Buried: 7 November 1943, Union Cemetery, Warren County, Ohio
Father: Samuel Smith (born 1841)
Mother: Mary Jane Doe (born 1848)
Spouse: Anna Louisa Miller (married 1896)
Residence: 1880 - Tarlton Township, Warren County, Ohio
Occupation: farm labourer, then teamster
Source: 1880 US Census, Tarlton Township, Warren County, Ohio
Source: Death certificate, Ohio Department of Health, 1943
Source: Will and probate, Warren County Probate Court, 1944
~~~

**Event fields** - Born, Died, Buried, Christened, Married, Residence, Emigrated, Occupation. Follow with a date, a place, or both separated by a comma.

**Relation fields** - Father, Mother, Spouse, Children, Also known as. Follow with names.

**Source fields** - Source, Sources, Evidence, Record, Citation. Follow with the citation; separate multiple with a semicolon.

**Conflict** - anything the sources disagree about. The forge treats this as a first-class claim.

**Note** - background that is not itself a claim.

**Dates** - 12 March 1872 / March 12, 1872 / 1872 / c. 1872 / before 1875 / between 1860 and 1870 / 1940s / aged 21. Numeric dates are flagged when they could be read both ways.

**Places** - read as Town, County, State or Parish, County, England. A country alone is accepted but flagged as too broad to search.

## What it checks

**Sourcing** - primary versus secondary versus derivative versus undocumented; a census cited for an exact birth date; an online tree used as evidence; a memorial as the only source; citations with no year; a single record type repeated (which is not corroboration).

**Consistency** - birth after death; marriage before birth or after death; events outside the lifespan; burial before death or long after it; two conflicting dates or places for the same event; parents who are younger than their child or implausibly old at the birth; a source created before the event it is supposed to prove.

**Coverage** - missing birth date, birth place, death date, death place, parents, spouse, residence; a life with no evidence in the middle years; born in one country and died in another with no migration record.

**Disambiguation** - names too common to identify a person; no middle name; birth dates known only to a year or a decade; pairs of records that look like one person; pairs that look like two people with the same name.

**Researchability** - the 1890 US census and the 1941 British census do not exist; no collection covers a birth in that place and year; a migration with no arrival port; nothing in the claim that can be checked against an independent record.

Structural caps stop a claim scoring well by skipping the hard part: a person with no citation caps the overall score at 45, an unresolved conflict caps consistency at 45, four or more high findings cap the overall score at 60.

## The research plan

Each step names a real record collection - US federal census, US state censuses, county vital records, Social Security applications, draft registration cards, passenger lists and naturalisation files, probate and land records, newspapers, cemetery records, church registers, England and Wales civil registration and parish registers, the UK censuses, Scottish OPRs, Irish civil registration and census fragments, Canadian and Australian censuses, family papers, oral history and DNA matches - along with its coverage window, what it proves, its caveats and how to search it.

For a person with a birth year it also lists the census years worth pulling and the age the person would have been, and it skips the 1890 and 1941 holes for you.

## Tech stack

- Python 3.9+ standard library for the parsing and analysis engine
- Flask for the API and static hosting
- SQLite for the research log
- Vanilla HTML, CSS and JavaScript for the interface (no build step, no npm)

## API

~~~
GET  /api/health
GET  /api/modes
GET  /api/samples
POST /api/analyze          {"text": "...", "mode": "standard", "save": true}
GET  /api/sessions
POST /api/sessions         {"text": "...", "mode": "standard", "result": {...}}
GET  /api/sessions/<id>
PATCH /api/sessions/<id>   {"rating": 4, "notes": "..."}
GET  /api/export/<id>?fmt=md|csv|json
~~~

## Tests

~~~
python3 -m unittest discover -s tests -v
~~~

## Layout

~~~
gh_sources.py    source-type profiles, record collections, place data
gh_parser.py     date / place / name / record parsing
gh_analysis.py   findings, scoring, identity analysis, research plan, exports
db.py            SQLite research log
app.py           Flask API
static/          dark vanilla-JS interface
samples/         six built-in samples
tests/           unittest smoke tests
~~~

## Licence

MIT
