# KNBS Leading Economic Indicators Extractor

A Python pipeline that downloads KNBS "Leading Economic Indicators" PDF reports and extracts structured economic data — CBR/interest rates, inflation, exchange rates, and fuel prices — directly from the tables inside them. Rather than hardcoding table numbers (which KNBS reshuffles between reports), it locates each target table semantically via the report's table of contents, then uses fuzzy matching and keyword-weighted scoring to pick the correct table out of several candidates on the page. The result is a reusable, table-agnostic extraction backbone plus a small set of table-specific parsers that turn raw Camelot dataframes into clean JSON-ready records.

## Features

- **URL-based ingestion** — pass a KNBS report URL and the pipeline downloads, parses, and cleans up after itself.
- **TOC-driven table discovery** — finds the "List of Tables" page and fuzzy-matches target table titles (via `rapidfuzz`) instead of relying on fixed table numbers.
- **Candidate scoring** — extracts multiple candidate tables around the matched page with Camelot (`stream` flavor) and scores them with weighted keywords to pick the best match.
- **Standardized responses** — every extractor returns the same response shape (`table_name`, `report_date`, `status`, `source_url`, `data`), regardless of which table it targets.
- **Per-table extractors** — CBR, Inflation, and Exchange Rates are fully implemented; Fuel Prices uses a specialized selection strategy to avoid drifting into neighboring tables; Stock Market is a stubbed placeholder.

## Architecture

```
KNBSExtractor                  # top-level orchestrator
│
├── PDFManager                 # PDF download (with retry logic) + report-date metadata
│
├── PipelineEngine             # reusable, table-agnostic backbone
│     ├── find_toc()
│     ├── parse_toc()
│     ├── find_table()
│     ├── locate_candidate_pages()
│     ├── extract_candidate_tables()
│     ├── score_table() / select_best_table()
│     └── run()
│
├── Validator                  # generic output validation
│
└── Extractors (BaseExtractor subclasses)
      ├── CBRExtractor
      ├── InflationExtractor
      ├── ExchangeExtractor
      ├── FuelExtractor        # custom page-window + scoring logic (see below)
      └── StockMarketExtractor # placeholder, not yet implemented
```

### Why FuelExtractor is different

The generic pipeline's 5-page forward window and generic keyword scoring reliably drifted into neighboring tables (e.g. "Consumption of Petroleum Fuels", "OPEC Reference Basket Prices") instead of landing on "National Average Retail Prices for Selected Fuels in Kenya" (Table 15(e), though it is never targeted by that ID). `FuelExtractor` reuses `PipelineEngine`'s individual steps (`find_toc`, `parse_toc`, `find_table`, `extract_candidate_tables`) but swaps in:

- a widened, TOC-offset-computed page window,
- fuel-specific weighted keyword scoring with penalty terms for neighboring tables, and
- a required-keyword validation gate (must match ≥4 of 5 core fuel categories).

`PipelineEngine` itself is untouched, and no other extractor is affected by this logic.

## Requirements

- Python 3.9+
- [`pdfplumber`](https://github.com/jsvine/pdfplumber)
- [`camelot-py`](https://camelot-py.readthedocs.io/) (stream flavor; requires Ghostscript)
- [`requests`](https://docs.python-requests.org/)
- [`urllib3`](https://urllib3.readthedocs.io/)
- [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz)

Install dependencies:

```bash
pip install pdfplumber camelot-py[cv] requests urllib3 rapidfuzz
```

> Camelot's `stream` flavor depends on Ghostscript being installed on your system separately (`apt install ghostscript` / `brew install ghostscript`).

## Usage

```python
from knbs_scraper import KNBSExtractor

extractor = KNBSExtractor()

pdf_url = "https://www.knbs.or.ke/wp-content/uploads/2026/05/Leading-Economic-Indicators-March-2026.pdf"

cbr_data = extractor.get_cbr(pdf_url)
inflation_data = extractor.get_inflation(pdf_url)
exchange_data = extractor.get_exchange_rates(pdf_url)
fuel_data = extractor.get_fuels(pdf_url)
stock_data = extractor.get_stock_market(pdf_url)  # placeholder, always returns status="failed"

print(inflation_data)
```

Each call returns a dict shaped like:

```json
{
  "table_name": "Consumer Price Indices and Inflation Rates",
  "report_date": "March 2026",
  "status": "success",
  "source_url": "https://www.knbs.or.ke/wp-content/uploads/2026/05/Leading-Economic-Indicators-March-2026.pdf",
  "data": [
    {"month": "January", "year": 2026, "kenya_inflation": 6.9}
  ]
}
```

> **Note:** `tester.py` currently imports from a module named `inside` (`from inside import KNBSExtractor`). Update that import to match your actual module filename (e.g. `knbs_scraper`) before running it.

## Extractor Status

| Extractor              | Status              | Notes                                                                 |
|-------------------------|---------------------|------------------------------------------------------------------------|
| `CBRExtractor`           | ✅ Implemented       | Year-carry logic, skip-row filtering, positional value extraction     |
| `InflationExtractor`     | ✅ Implemented       | Mirrors CBR parsing pattern                                            |
| `ExchangeExtractor`      | ✅ Implemented       | Extracts USD, GBP, EUR columns; ignores JPY/ZAR/USHS/TSHS              |
| `FuelExtractor`          | ✅ Implemented       | Custom page window + fuel-specific scoring/validation gate             |
| `StockMarketExtractor`   | 🚧 Placeholder       | Returns a standardized failure response; parsing not yet implemented   |

## Known Limitations & Tradeoffs

- **SSL verification is disabled** (`verify=False`) when downloading PDFs, to work around KNBS's certificate setup. This is a deliberate tradeoff logged explicitly at runtime — it carries MITM risk and should be revisited if a safer alternative (e.g. a custom CA bundle) becomes viable.
- Table-targeting is done by matching table **titles/semantics** in the TOC, never by hardcoded table numbers, since KNBS renumbers tables between report editions.
- Camelot's `stream` flavor can be sensitive to PDF layout changes; if KNBS significantly restructures a report's tables, scoring keywords/weights may need retuning.
- `StockMarketExtractor` is not yet implemented and will always return `status: "failed"` with empty `data`.

## Development Notes

- Changes are intended to be **surgical and isolated** — modifications to one extractor should not touch `PipelineEngine`, other extractors, or shared utilities unless explicitly in scope.
- Every extractor funnels its return value through `build_extractor_response()` to keep the response shape consistent across the whole project.
- Recommended validation before committing changes: `python3 -m py_compile knbs_scraper.py` (or `ast.parse`) plus functional smoke tests with `pdfplumber`, `camelot`, and `rapidfuzz` stubbed out, covering success, no-table-found, and placeholder paths.

## License

MIT license ig :).