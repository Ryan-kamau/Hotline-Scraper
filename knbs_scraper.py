"""
knbs_extractor.py

Reusable extraction engine for KNBS "Leading Economic Indicators" PDFs.

This module provides a generic, table-agnostic pipeline for locating and
extracting tables from KNBS PDF reports. It is designed so that new
table-specific extractors (CBR, inflation, exchange rates, fuels, stock
market data, etc.) can be added later WITHOUT modifying the reusable
pipeline itself.

Pipeline overview:

    PDF (local file or URL)
     -> load_pdf
     -> find_toc
     -> parse_toc
     -> find_table
     -> locate_candidate_pages
     -> extract_candidate_tables
     -> select_best_table
     -> (table-specific parser -- NOT implemented yet)
     -> validate_output
     -> return results

NOTE:
- Table-specific parsing logic is intentionally NOT implemented yet.
  All extract_* functions are placeholders that call run_pipeline() to
  fetch the best-guess raw dataframe, but do not parse it into structured
  records.
- The pipeline supports being fed either a local PDF path OR a PDF URL.
  When a URL is supplied, it is downloaded once at the start of the flow
  via download_pdf(); every other function still operates on a local
  pdf_path exactly as before.
"""

import logging
import os
import re
import tempfile
from urllib.parse import urlparse, unquote

import pdfplumber
import camelot
import requests
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("knbs_extractor")


# ---------------------------------------------------------------------------
# STEP 0: PDF acquisition (URL download support)
# ---------------------------------------------------------------------------

def download_pdf(pdf_url, download_dir="temp"):
    """
    Download a PDF from a URL and save it to a local temporary directory.

    Args:
        pdf_url (str): Fully qualified URL pointing to a PDF file.
        download_dir (str): Directory to save the downloaded file into.
            Defaults to "temp" (created relative to the current working
            directory if it does not already exist).

    Responsibilities:
        * Validate the URL
        * Download the PDF using requests
        * Save it into a temporary directory, preserving the original
          filename where possible
        * Log progress and errors

    Returns:
        str: Local filesystem path to the downloaded PDF.

    Raises:
        ValueError: If the URL is invalid or does not appear to point to
            a PDF file.
        RuntimeError: If the download fails for any reason.
    """
    logger.info("Downloading PDF...")

    # --- Validate URL -----------------------------------------------------
    parsed_url = urlparse(pdf_url)
    if not parsed_url.scheme or not parsed_url.netloc:
        logger.error("Could not download PDF")
        raise ValueError(f"Invalid PDF URL: {pdf_url}")

    # Derive a filename from the URL path, preserving the original name.
    filename = os.path.basename(unquote(parsed_url.path))
    if not filename:
        filename = "downloaded_report.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    # --- Prepare temporary directory --------------------------------------
    os.makedirs(download_dir, exist_ok=True)
    local_pdf_path = os.path.join(download_dir, filename)

    # --- Download -----------------------------------------------------------
    try:
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Could not download PDF")
        raise RuntimeError(f"Failed to download PDF from {pdf_url}: {exc}") from exc

    with open(local_pdf_path, "wb") as f:
        f.write(response.content)

    logger.info("Saved PDF to %s", local_pdf_path)
    return local_pdf_path


def extract_report_date_from_url(pdf_url):
    """
    Extract the report month/year from a KNBS PDF URL or filename.

    Example:
        Input:
            "https://www.knbs.or.ke/wp-content/uploads/2026/05/
             Leading-Economic-Indicators-March-2026.pdf"
        Output:
            {
                "month": "March",
                "year": 2026,
                "report_date": "March 2026"
            }

    Args:
        pdf_url (str): URL (or plain filename) referencing the KNBS report.

    Returns:
        dict | None: Dictionary with "month", "year", and "report_date"
            keys if extraction succeeds, otherwise None.
    """
    parsed_url = urlparse(pdf_url)
    filename = os.path.basename(unquote(parsed_url.path)) or pdf_url

    # Strip extension, e.g. ".pdf"
    filename_no_ext = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)

    month_pattern = (
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
    )
    year_pattern = r"(20\d{2})"

    match = re.search(
        rf"{month_pattern}[-_ ]?{year_pattern}",
        filename_no_ext,
        flags=re.IGNORECASE,
    )

    if not match:
        logger.warning(
            "Could not extract report date from URL: %s", pdf_url
        )
        return None

    month = match.group(1).capitalize()
    year = int(match.group(2))
    report_date = f"{month} {year}"

    logger.info("Report date extracted: %s", report_date)

    return {
        "month": month,
        "year": year,
        "report_date": report_date,
    }


# ---------------------------------------------------------------------------
# STEP 1: PDF handling
# ---------------------------------------------------------------------------

def load_pdf(path):
    """
    Validate and open a local PDF file using pdfplumber.

    Args:
        path (str): Local filesystem path to the PDF file.

    Responsibilities:
        * Validate that the file exists
        * Open the PDF using pdfplumber
        * Log the page count
        * Return the opened PDF object

    Returns:
        pdfplumber.PDF: The opened PDF object.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
    """
    if not os.path.exists(path):
        logger.error("PDF file not found: %s", path)
        raise FileNotFoundError(f"PDF file not found: {path}")

    pdf = pdfplumber.open(path)
    logger.info("Loaded PDF '%s' with %d pages", path, len(pdf.pages))
    return pdf


# ---------------------------------------------------------------------------
# STEP 2: TOC detection
# ---------------------------------------------------------------------------

def find_toc(pdf, max_pages_to_search=10):
    """
    Locate the "List of Tables" (Table of Contents) page within a PDF.

    Args:
        pdf (pdfplumber.PDF): An opened PDF object (from load_pdf).
        max_pages_to_search (int): Number of leading pages to search
            through before giving up. Defaults to 10.

    Responsibilities:
        * Search only the first few pages of the document
        * Find the page containing "List of Tables"
        * Return the page index

    Returns:
        int | None: Zero-based page index of the TOC page, or None if
            no TOC page was found.
    """
    search_limit = min(max_pages_to_search, len(pdf.pages))

    for page_index in range(search_limit):
        page = pdf.pages[page_index]
        text = page.extract_text() or ""

        if re.search(r"list of tables", text, flags=re.IGNORECASE):
            logger.info("Found TOC ('List of Tables') on page index %d", page_index)
            return page_index

    logger.warning("Could not locate 'List of Tables' in first %d pages", search_limit)
    return None


# ---------------------------------------------------------------------------
# STEP 3: TOC parsing
# ---------------------------------------------------------------------------

def parse_toc(pdf, toc_page):
    """
    Parse table-of-contents entries from the TOC page(s).

    Handles common KNBS TOC formatting variations:
        * "Table 1" / "Table 1(a)"
        * Variable spacing between title and page number
        * Dot leaders (e.g. "Table 1: CBR Rates ..... 6")

    Args:
        pdf (pdfplumber.PDF): An opened PDF object.
        toc_page (int): Zero-based page index where the TOC begins
            (typically the result of find_toc()).

    Responsibilities:
        * Extract TOC entries in the form:
            [{"title": "...", "document_page": 6}, ...]

    Returns:
        list[dict]: Parsed TOC entries. Empty list if none found or if
            toc_page is None.
    """
    if toc_page is None:
        logger.warning("No TOC page provided; cannot parse TOC entries")
        return []

    page = pdf.pages[toc_page]
    text = page.extract_text() or ""
    lines = text.splitlines()

    # Matches lines like:
    #   "Table 1: CBR and Interest Rates ........... 6"
    #   "Table 1(a) Exchange Rates   12"
    #   "Table 12  Fuel Prices...... 20"
    entry_pattern = re.compile(
        r"^\s*Table\s+\d+[a-zA-Z]?(?:\([a-zA-Z]\))?\s*[:.\-]?\s*"
        r"(?P<title>.+?)\s*[\.\s]{2,}\s*(?P<page>\d+)\s*$",
        flags=re.IGNORECASE,
    )

    entries = []
    for line in lines:
        line = line.strip()
        if not line.lower().startswith("table"):
            continue

        match = entry_pattern.match(line)
        if match:
            title = match.group("title").strip(" .")
            document_page = int(match.group("page"))
            entries.append({"title": title, "document_page": document_page})
        else:
            logger.warning("Could not parse TOC line: '%s'", line)

    logger.info("Parsed %d TOC entries from page index %d", len(entries), toc_page)
    return entries


# ---------------------------------------------------------------------------
# STEP 4: Target table search
# ---------------------------------------------------------------------------

def find_table(entries, target, score_cutoff=50):
    """
    Find the TOC entry that best matches a target table name using
    fuzzy string matching.

    Args:
        entries (list[dict]): Parsed TOC entries from parse_toc().
        target (str): The target table name/description to search for,
            e.g. "Interest rate".
        score_cutoff (int): Minimum fuzzy match score (0-100) required
            to consider a match valid. Defaults to 50.

    Responsibilities:
        * Use fuzzy matching to compare `target` against each entry title
        * Select the best matching entry

    Returns:
        dict | None: {"title": "...", "document_page": x} for the best
            match, or None if no entry meets the score cutoff.
    """
    if not entries:
        logger.warning("No TOC entries available to search for target '%s'", target)
        return None

    best_entry = None
    best_score = -1

    for entry in entries:
        score = fuzz.partial_ratio(target.lower(), entry["title"].lower())
        logger.info("TOC candidate '%s' scored %d against target '%s'",
                    entry["title"], score, target)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry is None or best_score < score_cutoff:
        logger.warning(
            "No suitable match found for target '%s' (best score=%s)",
            target, best_score,
        )
        return None

    logger.info(
        "Selected TOC entry '%s' (page %d) for target '%s' with score %d",
        best_entry["title"], best_entry["document_page"], target, best_score,
    )
    return best_entry


# ---------------------------------------------------------------------------
# STEP 5: Candidate page locator
# ---------------------------------------------------------------------------

def locate_candidate_pages(document_page, window_size=5):
    """
    Generate a forward-only search window of candidate pages, starting
    from the page referenced in the TOC.

    Note: This does NOT attempt to calculate any offset between the
    TOC's printed page numbers and actual PDF/document page indices.
    It simply returns a forward window starting at document_page.

    Example:
        locate_candidate_pages(9) -> [9, 10, 11, 12, 13]

    Args:
        document_page (int): Starting page number, as referenced in the TOC.
        window_size (int): Number of pages to include in the forward
            search window. Defaults to 5.

    Returns:
        list[int]: List of candidate page numbers.
    """
    candidate_pages = list(range(document_page, document_page + window_size))
    logger.info(
        "Generated candidate page window starting at %d: %s",
        document_page, candidate_pages,
    )
    return candidate_pages


# ---------------------------------------------------------------------------
# STEP 6: Camelot extraction
# ---------------------------------------------------------------------------

def extract_candidate_tables(pdf_path, candidate_pages):
    """
    Run Camelot table extraction (stream flavor) across a set of
    candidate pages.

    Args:
        pdf_path (str): Local filesystem path to the PDF file.
        candidate_pages (list[int]): Page numbers to attempt extraction on.

    Responsibilities:
        * For each candidate page, run Camelot with flavor="stream"
        * Log page number and number of tables found
        * Collect results

    Returns:
        list[dict]: [{"page": page_number, "table": dataframe}, ...]
            One entry per table found (a single page may yield multiple
            tables, or none).
    """
    candidate_tables = []

    for page_number in candidate_pages:
        try:
            tables = camelot.read_pdf(
                pdf_path,
                pages=str(page_number),
                flavor="stream",
            )
        except Exception as exc:  # noqa: BLE001 - log and continue on any Camelot failure
            logger.warning(
                "Camelot extraction failed on page %d: %s", page_number, exc
            )
            continue

        logger.info("Page %d: found %d table(s)", page_number, len(tables))

        for table in tables:
            candidate_tables.append({
                "page": page_number,
                "table": table.df,
            })

    logger.info(
        "Extracted %d total candidate table(s) across %d page(s)",
        len(candidate_tables), len(candidate_pages),
    )
    return candidate_tables


# ---------------------------------------------------------------------------
# STEP 7: Table scoring
# ---------------------------------------------------------------------------

def score_table(dataframe, keywords):
    """
    Score a dataframe based on how many times each keyword appears in
    its flattened text content, weighted by the keyword's configured
    importance.

    Args:
        dataframe (pandas.DataFrame): Candidate table to score.
        keywords (dict): Mapping of keyword -> weight, e.g.
            {"interest": 2, "cbr": 3, "deposit": 1}

    Responsibilities:
        * Flatten all dataframe cell text into a single lowercase string
        * Count keyword occurrences weighted by their configured score

    Returns:
        int: Total numeric score for the table.
    """
    if dataframe is None or dataframe.empty:
        return 0

    flattened_text = " ".join(
        str(cell) for cell in dataframe.values.flatten()
    ).lower()

    total_score = 0
    for keyword, weight in keywords.items():
        occurrences = flattened_text.count(keyword.lower())
        total_score += occurrences * weight

    return total_score


# ---------------------------------------------------------------------------
# STEP 8: Best table selector
# ---------------------------------------------------------------------------

def select_best_table(candidate_tables, keywords):
    """
    Score every candidate table and select the highest scoring one.

    Args:
        candidate_tables (list[dict]): Output of extract_candidate_tables().
        keywords (dict): Keyword weight mapping used for scoring.

    Responsibilities:
        * Score all candidate tables
        * Select the highest scoring dataframe
        * Log each table's score and the final selection

    Returns:
        pandas.DataFrame | None: The best matching dataframe, or None if
            no candidate tables were provided.
    """
    if not candidate_tables:
        logger.warning("No candidate tables available to select from")
        return None

    best_table = None
    best_score = -1
    best_index = -1

    for index, candidate in enumerate(candidate_tables):
        score = score_table(candidate["table"], keywords)
        logger.info("table %d (page %d) score=%d", index, candidate["page"], score)

        if score > best_score:
            best_score = score
            best_table = candidate["table"]
            best_index = index

    logger.info("selected table=%d (score=%d)", best_index, best_score)
    return best_table


# ---------------------------------------------------------------------------
# STEP 9: Validation
# ---------------------------------------------------------------------------

def validate_output(data):
    """
    Perform generic, table-agnostic validation checks on extracted data.

    Checks performed:
        * Output is not empty
        * Records are well-formed (dict-like, if data is a list of records)
        * No obviously missing/blank values in top-level fields

    NOTE: Table-specific validation (e.g. checking that a CBR value is a
    valid percentage) is NOT implemented here. This is intentionally
    generic and will be extended by table-specific extractors later.

    Args:
        data (Any): The data to validate. Typically a list of records or
            a pandas.DataFrame.

    Returns:
        bool: True if the data passes generic validation, False otherwise.
    """
    if data is None:
        logger.error("Validation failed: output is None")
        return False

    # Handle pandas DataFrame
    if hasattr(data, "empty"):
        if data.empty:
            logger.error("Validation failed: dataframe is empty")
            return False
        logger.info("Validation passed: dataframe has %d row(s)", len(data))
        return True

    # Handle list-like output
    if isinstance(data, list):
        if len(data) == 0:
            logger.warning("Validation warning: output list is empty")
            return False

        for i, record in enumerate(data):
            if not isinstance(record, dict):
                logger.error("Malformed record at index %d: not a dict", i)
                return False
            if any(value in (None, "", []) for value in record.values()):
                logger.warning("Record at index %d has missing value(s)", i)

        logger.info("Validation passed: %d record(s) checked", len(data))
        return True

    logger.warning("Validation skipped: unrecognized data type %s", type(data))
    return False


# ---------------------------------------------------------------------------
# STEP 10: Main reusable extraction engine
# ---------------------------------------------------------------------------

def run_pipeline(pdf_path, target, keywords):
    """
    Execute the full generic extraction pipeline for a given target table.

    This function does NOT parse table contents into structured records.
    It only locates and returns the best-matching raw dataframe for the
    requested target table. Table-specific parsing is the responsibility
    of the individual extract_* functions.

    Pipeline:
        load_pdf
        -> find_toc
        -> parse_toc
        -> find_table
        -> locate_candidate_pages
        -> extract_candidate_tables
        -> select_best_table

    Args:
        pdf_path (str): Local filesystem path to the PDF file.
        target (str): Target table name/description to search for.
        keywords (dict): Keyword weight mapping used for scoring
            candidate tables.

    Returns:
        pandas.DataFrame | None: The selected raw dataframe, or None if
            the pipeline could not locate a suitable table.
    """
    logger.info("Running pipeline for target='%s'", target)

    pdf = load_pdf(pdf_path)

    toc_page = find_toc(pdf)
    entries = parse_toc(pdf, toc_page)
    matched_entry = find_table(entries, target)

    if matched_entry is None:
        logger.error("Pipeline aborted: no matching TOC entry for target '%s'", target)
        pdf.close()
        return None

    candidate_pages = locate_candidate_pages(matched_entry["document_page"])
    candidate_tables = extract_candidate_tables(pdf_path, candidate_pages)
    selected_dataframe = select_best_table(candidate_tables, keywords)

    pdf.close()

    return selected_dataframe


# ---------------------------------------------------------------------------
# Configuration storage
# ---------------------------------------------------------------------------

TABLE_CONFIG = {
    "cbr": {
        "target": "Interest rate",
        "keywords": {
            "interest": 2,
            "cbr": 3,
            "deposit": 1,
        },
    },
    "inflation": {
        "target": "Inflation rate",
        "keywords": {
            "inflation": 3,
            "cpi": 2,
            "index": 1,
        },
    },
    "exchange_rates": {
        "target": "Exchange rate",
        "keywords": {
            "exchange": 3,
            "usd": 2,
            "rate": 1,
        },
    },
    "fuels": {
        "target": "Fuel price",
        "keywords": {
            "fuel": 3,
            "petrol": 2,
            "diesel": 2,
            "kerosene": 1,
        },
    },
    "stock_market": {
        "target": "Stock market",
        "keywords": {
            "stock": 3,
            "nse": 2,
            "share": 1,
            "index": 1,
        },
    },
}


# ---------------------------------------------------------------------------
# Placeholder table-specific extractors
# ---------------------------------------------------------------------------
#
# These functions accept a PDF URL (not a local path). Internally they:
#   1. Download the PDF once via download_pdf()
#   2. Extract report metadata from the URL via extract_report_date_from_url()
#   3. Run the generic pipeline on the resulting local PDF path
#
# They do NOT yet parse the resulting dataframe into structured records.
# That logic will be added table-by-table in the future without touching
# the reusable pipeline above.
# ---------------------------------------------------------------------------

def extract_cbr(pdf_url):
    """
    Placeholder extractor for the CBR (Central Bank Rate) / interest
    rates table.

    Args:
        pdf_url (str): URL to the KNBS PDF report.

    Returns:
        list: Empty list (parsing not yet implemented).
    """
    local_pdf_path = download_pdf(pdf_url)
    report_metadata = extract_report_date_from_url(pdf_url)

    config = TABLE_CONFIG["cbr"]
    table_df = run_pipeline(
        pdf_path=local_pdf_path,
        target=config["target"],
        keywords=config["keywords"],
    )

    metadata = {
        "source_url": pdf_url,
        "report_date": report_metadata["report_date"] if report_metadata else None,
        "month": report_metadata["month"] if report_metadata else None,
        "year": report_metadata["year"] if report_metadata else None,
    }
    logger.info("extract_cbr metadata: %s", metadata)

    # TODO:
    # Add CBR parsing logic
    # - Parse table_df into structured records (date, CBR value, etc.)
    # - Attach `metadata` to each returned record

    return []


def extract_inflation(pdf_url):
    """
    Placeholder extractor for the inflation rate table.

    Args:
        pdf_url (str): URL to the KNBS PDF report.

    Returns:
        list: Empty list (parsing not yet implemented).
    """
    local_pdf_path = download_pdf(pdf_url)
    report_metadata = extract_report_date_from_url(pdf_url)

    config = TABLE_CONFIG["inflation"]
    table_df = run_pipeline(
        pdf_path=local_pdf_path,
        target=config["target"],
        keywords=config["keywords"],
    )

    metadata = {
        "source_url": pdf_url,
        "report_date": report_metadata["report_date"] if report_metadata else None,
        "month": report_metadata["month"] if report_metadata else None,
        "year": report_metadata["year"] if report_metadata else None,
    }
    logger.info("extract_inflation metadata: %s", metadata)

    # TODO:
    # Add inflation parsing logic
    # - Parse table_df into structured records
    # - Attach `metadata` to each returned record

    return []


def extract_exchange_rates(pdf_url):
    """
    Placeholder extractor for the exchange rates table.

    Args:
        pdf_url (str): URL to the KNBS PDF report.

    Returns:
        list: Empty list (parsing not yet implemented).
    """
    local_pdf_path = download_pdf(pdf_url)
    report_metadata = extract_report_date_from_url(pdf_url)

    config = TABLE_CONFIG["exchange_rates"]
    table_df = run_pipeline(
        pdf_path=local_pdf_path,
        target=config["target"],
        keywords=config["keywords"],
    )

    metadata = {
        "source_url": pdf_url,
        "report_date": report_metadata["report_date"] if report_metadata else None,
        "month": report_metadata["month"] if report_metadata else None,
        "year": report_metadata["year"] if report_metadata else None,
    }
    logger.info("extract_exchange_rates metadata: %s", metadata)

    # TODO:
    # Add exchange rate parsing logic
    # - Parse table_df into structured records
    # - Attach `metadata` to each returned record

    return []


def extract_fuels(pdf_url):
    """
    Placeholder extractor for the fuel prices table.

    Args:
        pdf_url (str): URL to the KNBS PDF report.

    Returns:
        list: Empty list (parsing not yet implemented).
    """
    local_pdf_path = download_pdf(pdf_url)
    report_metadata = extract_report_date_from_url(pdf_url)

    config = TABLE_CONFIG["fuels"]
    table_df = run_pipeline(
        pdf_path=local_pdf_path,
        target=config["target"],
        keywords=config["keywords"],
    )

    metadata = {
        "source_url": pdf_url,
        "report_date": report_metadata["report_date"] if report_metadata else None,
        "month": report_metadata["month"] if report_metadata else None,
        "year": report_metadata["year"] if report_metadata else None,
    }
    logger.info("extract_fuels metadata: %s", metadata)

    # TODO:
    # Add fuel price parsing logic
    # - Parse table_df into structured records
    # - Attach `metadata` to each returned record

    return []


def extract_stock_market(pdf_url):
    """
    Placeholder extractor for the stock market data table.

    Args:
        pdf_url (str): URL to the KNBS PDF report.

    Returns:
        list: Empty list (parsing not yet implemented).
    """
    local_pdf_path = download_pdf(pdf_url)
    report_metadata = extract_report_date_from_url(pdf_url)

    config = TABLE_CONFIG["stock_market"]
    table_df = run_pipeline(
        pdf_path=local_pdf_path,
        target=config["target"],
        keywords=config["keywords"],
    )

    metadata = {
        "source_url": pdf_url,
        "report_date": report_metadata["report_date"] if report_metadata else None,
        "month": report_metadata["month"] if report_metadata else None,
        "year": report_metadata["year"] if report_metadata else None,
    }
    logger.info("extract_stock_market metadata: %s", metadata)

    # TODO:
    # Add stock market parsing logic
    # - Parse table_df into structured records
    # - Attach `metadata` to each returned record

    return []


# ---------------------------------------------------------------------------
# Manual test entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Example usage (manual smoke test only):
    #
    # sample_url = (
    #     "https://www.knbs.or.ke/wp-content/uploads/2026/05/"
    #     "Leading-Economic-Indicators-March-2026.pdf"
    # )
    # results = extract_cbr(sample_url)
    # print(results)
    pass