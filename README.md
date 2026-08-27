# Greenlight Studio

**Decide with the record, not the room.**

A greenlight committee in software. Paste a screenplay, a treatment or a series
bible; a team of Gemini agents investigates a real catalogue of what actually
happened to comparable films and television, writes its own SQL when the
standard questions are not the right ones, and returns a memo you can argue
with — every figure sourced, every assumption stated.

Built for **Agentic Cinema: The Blockbuster Hackathon** — ClickHouse track.

---

## What it does

A development executive asks three questions about any project: *what is this
like, what happened to things like it, and what would have to change.* This
answers all three from evidence rather than from the room.

- **Film** — projected gross against the break-even it has to clear, and how
  often films of this genre *at this budget* actually cleared it.
- **Series** — whether it comes back, judged against the market it is made for.
  A ten-episode single season is a cancellation in Los Angeles and a completed
  commission in Tokyo, and the score knows the difference.
- **What if** — move the budget or the episode order and the verdict re-derives
  from a fresh ClickHouse read, with no model call at all.

---

## Architecture

```
  screenplay / treatment / series bible
                 │
    ┌────────────▼─────────────┐
    │ 1. Script Analyst        │  ADK LlmAgent, structured output, no tools
    │    tone, genre, language │
    └────────────┬─────────────┘
                 │  Gemini embedding (768d)
    ┌────────────▼─────────────────────────────────────┐
    │ 2. Research Analyst      │  ADK LlmAgent, 5 tools │
    │                                                   │
    │   find_comparable_titles  vector search           │
    │   benchmark_segment       budget-band outcomes    │
    │   rank_talent             realised director ROI   │
    │   query_catalogue         ★ SQL the model writes  │
    │   describe_catalogue      what it may claim       │
    └────────────┬──────────────────────────────────────┘
                 │
    ┌────────────▼─────────────┐
    │    deterministic Python  │  projection, score, What-if
    │    (no model call here)  │
    └────────────┬─────────────┘
    ┌────────────▼─────────────┐
    │ 3. Greenlight Writer     │  ADK LlmAgent, English or Japanese
    └────────────┬─────────────┘
                 ▼
         memo + evidence + trace
```

Everything above runs against **ClickHouse**: a single self-hosted node on
Compute Engine, reachable only from inside the VPC, holding 25,325 rows with a
768-dimension Gemini embedding on every title.

### Why the arithmetic is not in the model

The model decides *what to look up* and *how to explain it*. It never computes
a number. Every figure in the memo is calculated in Python from rows ClickHouse
returned, which means the prose and the chart cannot disagree, the same input
always yields the same verdict, and the What-if sliders re-run in milliseconds
without another model call.

---

## Runtime use of the partner's product

The ClickHouse track requires runtime use *via the official MCP server*. Both
paths are live:

| Path | Where | What it does |
|---|---|---|
| `mcp-clickhouse` (official) | `app/agents/clickhouse_mcp.py` | launched as a child process over stdio, exposed to ADK as tools, read-only |
| `clickhouse-connect` | `app/store/clickhouse.py` | vector search and the measured per-query latency the UI displays |

`mcp-clickhouse` pins `fastmcp` 2.x while this project runs 3.x, so it lives in
its own virtualenv and is spoken to as a separate process. That is the correct
MCP architecture and it also keeps the dependency fight from reaching the app.

**Check it yourself:** `GET /api/health` reports the live ClickHouse server
version, the row count of every table, the Gemini backend and project, and
whether the official MCP server is installed. It reports what is wired up, not
what this README claims.

---

## The catalogue

Built from **Wikidata (CC0)** and **English Wikipedia (CC BY-SA 4.0)**.

| Table | Rows | Coverage |
|---|---|---|
| `movies_historical` | 2,719 | 1921–2026, 34 languages. Every row records a budget **and** a box-office gross in USD; titles missing either were dropped. |
| `series_historical` | 20,836 | 1937–2027, 123 languages. 48% returned for a second season, 44% did not. |
| `talent_analytics` | 1,770 | Directors and actors with 2+ credits in one genre. |

Those numbers are written by the ingest into `data/manifest.json` and shown in
the page footer, so the interface can only claim coverage the data has.

**Why not a commercial film database.** The obvious source requires certifying
that use is strictly personal and that the data will not be used in any
business environment. This is submitted under a company name and runs at a
public URL, so that certification could not be made honestly. Wikidata is CC0
and restricts neither feeding the text to a model nor running the result inside
a business.

The catalogue is **not committed to this repository**; it is rebuilt from
source (see below), which is also how the licences are respected.

---

## Running it

### Prerequisites

- Python 3.13
- A ClickHouse instance (Cloud or self-hosted) — `infra/clickhouse-startup.sh`
  brings up the single-node configuration this project uses
- Google Cloud project with Vertex AI enabled, and `gcloud auth application-default login`

### Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# The official ClickHouse MCP server, deliberately isolated
python -m venv .venv-mcp
.venv-mcp/bin/pip install mcp-clickhouse==0.4.1

cp .env.example .env      # then fill in ClickHouse host/password and the GCP project
```

### Build the catalogue

Roughly 40 minutes end to end, most of it Wikipedia and embedding calls. Every
stage caches to `data/_cache`, so an interruption costs only the stage it was in.

```bash
python -m ingest.wikidata films     # Wikidata + Wikipedia -> data/*.jsonl
python -m ingest.wikidata series
python -m ingest.wikidata talent
python -m ingest.wikidata embed     # Gemini embeddings, about $1.80
python -m ingest.wikidata load      # into ClickHouse
```

### Run

```bash
python -m uvicorn app.main:app --port 8080
```

Then open http://localhost:8080 and click one of the sample projects.

### Tests

```bash
python -m tests.test_guardrails
```

---

## Cost and runaway protection

An agent with tools can loop: call a tool, dislike the answer, call it again
with almost the same arguments, forever. Six independent ceilings sit in front
of every model call and every tool call, through ADK's `before_model` and
`before_tool` hooks:

| Ceiling | Default |
|---|---|
| Spend per request | $0.25, enforced by a meter that prices every call |
| Wall clock | 90 s, with `asyncio.wait_for` as the outer backstop |
| Model calls | 26 per request |
| Tool calls | 16 per request, 5 per individual tool |
| Identical repeated calls | 2 — the signature of a loop |
| SQL result rows | 200, added to any query that omits a `LIMIT` |

Tripping a ceiling does not raise. The model is told to answer with the
evidence it already has, and the trip is recorded in the trace shown on the
page — visible rather than silent. ClickHouse enforces its own limits too
(15 s per query, 5,000 rows, 1.5 GB), so a query that gets past the application
guard still cannot take the database down.

A typical full analysis: **20–40 seconds, about $0.02.**

---

## What this does not do

- **It does not know what a film will earn.** It reports what comparable films
  earned and how often they cleared break-even. Those are different claims.
- **Budgets are not randomly assigned.** A high hit rate in the $150M band
  partly measures the belief that got those films funded, not the money. The
  report says so on every projection.
- **Figures are nominal USD**, not inflation adjusted.
- **Japanese film coverage is thin** — 20 titles, because Wikidata rarely
  records budgets for Japanese cinema. Japanese *television* is well covered at
  346 series, which is why the Japanese interface is genuinely useful for
  series work and honest about its limits for film.
- **No viewership data exists publicly**, so renewal outcomes stand in for
  audience performance on the series side.

---

## Licence and attribution

MIT — see [LICENSE](LICENSE).

Catalogue data from [Wikidata](https://www.wikidata.org) (CC0 1.0) and
[Wikipedia](https://en.wikipedia.org) (CC BY-SA 4.0). This project is not
endorsed by or affiliated with either.

Built by [Studio S.O](https://sutekioojisan-so.com).
