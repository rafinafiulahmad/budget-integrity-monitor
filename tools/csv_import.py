"""
CSV Bulk Import Tool
Supports importing multiple projects from a single CSV file.

Expected CSV columns:
    project_name, country, total_budget_usd, donor, sector,
    procurement_method, beneficiary_count, national_law_reference,
    context_notes,
    item_name, item_amount_usd, item_category, item_vendor_origin, item_notes

Multi-line format: one row per line item.  Rows sharing the same
project_name (case-sensitive) are grouped into one BudgetProject.
"""

import csv
import io
from pathlib import Path
from typing import Union

from core.models import BudgetProject, BudgetLineItem

# ── Column aliases ────────────────────────────────────────────────────────────
PROJECT_COLS = {
    "project_name":           ["project_name", "project", "name"],
    "country":                ["country"],
    "total_budget_usd":       ["total_budget_usd", "total_budget", "budget_usd", "budget"],
    "donor":                  ["donor"],
    "sector":                 ["sector"],
    "procurement_method":     ["procurement_method", "procurement"],
    "beneficiary_count":      ["beneficiary_count", "beneficiaries"],
    "national_law_reference": ["national_law_reference", "national_law", "law_ref"],
    "context_notes":          ["context_notes", "context", "notes"],
}

ITEM_COLS = {
    "name":          ["item_name", "line_item", "item"],
    "amount_usd":    ["item_amount_usd", "item_amount", "amount_usd", "amount"],
    "category":      ["item_category", "category"],
    "vendor_origin": ["item_vendor_origin", "vendor_origin", "vendor"],
    "notes":         ["item_notes"],
}


def _resolve_col(header: list[str], aliases: list[str]) -> str | None:
    """Return the first header that matches any alias (case-insensitive)."""
    lower = {h.lower(): h for h in header}
    for alias in aliases:
        if alias.lower() in lower:
            return lower[alias.lower()]
    return None


def _build_col_map(header: list[str], spec: dict) -> dict[str, str | None]:
    return {field: _resolve_col(header, aliases) for field, aliases in spec.items()}


def parse_csv(source: Union[str, Path, io.StringIO]) -> list[BudgetProject]:
    """
    Parse a CSV and return a list of BudgetProject objects.
    source can be a file path (str / Path) or a StringIO object.
    """
    if isinstance(source, (str, Path)):
        text = Path(source).read_text(encoding="utf-8-sig")  # strip BOM
    else:
        text = source.read()

    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []

    pcols = _build_col_map(header, PROJECT_COLS)
    icols = _build_col_map(header, ITEM_COLS)

    projects: dict[str, dict] = {}   # keyed by project_name

    def _get(row, col_key, default=""):
        col = pcols.get(col_key) or icols.get(col_key)
        if col and col in row:
            return row[col].strip()
        return default

    for i, row in enumerate(reader, start=2):   # row 2 = first data row
        pname_col = pcols["project_name"]
        if not pname_col or not row.get(pname_col, "").strip():
            continue   # skip blank rows

        pname = row[pname_col].strip()

        if pname not in projects:
            try:
                total_budget = float(_get(row, "total_budget_usd", "0").replace(",", ""))
            except ValueError:
                total_budget = 0.0

            try:
                bcount_raw = _get(row, "beneficiary_count", "").replace(",", "")
                bcount = int(bcount_raw) if bcount_raw else None
            except ValueError:
                bcount = None

            projects[pname] = {
                "name":                  pname,
                "country":               _get(row, "country"),
                "total_budget_usd":      total_budget,
                "donor":                 _get(row, "donor"),
                "sector":                _get(row, "sector"),
                "procurement_method":    _get(row, "procurement_method"),
                "beneficiary_count":     bcount,
                "national_law_reference": _get(row, "national_law_reference"),
                "context_notes":         _get(row, "context_notes"),
                "line_items":            [],
            }

        # Parse line item columns
        item_name_col = icols.get("name")
        if item_name_col and row.get(item_name_col, "").strip():
            try:
                amt_raw = _get(row, "amount_usd", "0").replace(",", "")
                amt = float(amt_raw) if amt_raw else 0.0
            except ValueError:
                amt = 0.0

            item = BudgetLineItem(
                name=_get(row, "name"),
                amount_usd=amt,
                category=_get(row, "category", "other"),
                vendor_origin=_get(row, "vendor_origin", "local"),
                notes=_get(row, "notes"),
            )
            projects[pname]["line_items"].append(item)

    result = []
    for pdata in projects.values():
        # Build via from_dict so we reuse validation logic
        p = BudgetProject(
            name=pdata["name"],
            country=pdata["country"],
            total_budget_usd=pdata["total_budget_usd"],
            donor=pdata["donor"],
            sector=pdata["sector"],
            line_items=pdata["line_items"],
            procurement_method=pdata["procurement_method"],
            beneficiary_count=pdata["beneficiary_count"],
            national_law_reference=pdata["national_law_reference"],
            context_notes=pdata["context_notes"],
        )
        result.append(p)

    return result


def generate_template_csv() -> str:
    """Return a template CSV string showing the expected format."""
    rows = [
        [
            "project_name", "country", "total_budget_usd", "donor", "sector",
            "procurement_method", "beneficiary_count", "national_law_reference",
            "context_notes",
            "item_name", "item_amount_usd", "item_category", "item_vendor_origin", "item_notes",
        ],
        [
            "Rural Road Project", "Bangladesh", "8500000", "ADB", "Infrastructure",
            "ICB restricted", "120000", "PPR 2008",
            "ADB requires approved vendor list",
            "International Consultancy", "1800000", "consultancy", "foreign", "",
        ],
        [
            "Rural Road Project", "Bangladesh", "8500000", "ADB", "Infrastructure",
            "", "", "", "",
            "Civil Works", "3200000", "civil_works", "local", "",
        ],
        [
            "Health Program", "Nepal", "4200000", "USAID", "Health",
            "Restricted to USAID-approved", "200000", "Nepal Procurement Act 2063",
            "US prime required",
            "Technical Assistance - US Firm", "1400000", "consultancy", "donor_mandated", "",
        ],
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue()
