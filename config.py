# =============================================================================
# config.py — NL Scenario Modelling Copilot
# =============================================================================

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not found. Check .env in project root.")

MODEL      = "claude-sonnet-4-6"
MAX_TOKENS = 2048

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

ACTUALS_FILE     = DATA_DIR / "actuals_ytd.csv"
DRIVERS_FILE     = DATA_DIR / "driver_table.csv"
OPERATIONAL_FILE = DATA_DIR / "operational_actuals.csv"
HEADCOUNT_FILE   = DATA_DIR / "headcount_schedule.csv"
CUSTOMER_FILE    = DATA_DIR / "customer_targets.csv"
AUDIT_LOG        = OUTPUT_DIR / "audit_log.jsonl"

DEFAULT_ENTITY = "Valencia Operations"
FORECAST_HORIZON = 6    # months to forecast forward

# ── Reused Project 2 engine constants (the copied engine imports these) ──────
MAX_COGS_MARGIN  = 1.0          # COGS cannot exceed 100% of Revenue
MAX_REVENUE      = 10_000_000   # flag if any single month exceeds this
REVENUE_ITEMS = ["Revenue"]
COGS_ITEMS    = ["COGS"]
OPEX_ITEMS    = ["Personnel Cost", "Marketing Spend", "IT Infrastructure", "R&D Expense"]

VALID_DRIVER_TYPES = [
    "seasonal_yoy", "margin_pct", "headcount_driven",
    "cac_driven", "growth_pct", "fixed",
]

# ── The model's line items and their driver types (the legal action space) ───
LINE_ITEMS = {
    "Revenue":           "seasonal_yoy",
    "COGS":              "margin_pct",
    "Personnel Cost":    "headcount_driven",
    "Marketing Spend":   "cac_driven",
    "IT Infrastructure": "fixed",
    "R&D Expense":       "growth_pct",
}

# ── Scenario operations and validation rules ─────────────────────────────────
# Each operation lists the driver types it is legal for, the value type, and
# sane bounds. The validator enforces these before anything runs.
VALIDATION_RULES = {
    "scale_driver": {
        "legal":  {"seasonal_yoy", "margin_pct", "growth_pct", "fixed"},
        "bounds": (0.0, 3.0), "value_type": "float",
        "desc":   "multiply a driver value by a factor (0.8 = cut 20 percent)",
    },
    "set_driver": {
        "legal":  {"seasonal_yoy", "margin_pct", "growth_pct", "fixed"},
        "bounds": None, "value_type": "float",   # per-driver bounds below
        "desc":   "replace a driver value outright (0.08 = set to 8 percent)",
    },
    "scale_schedule": {
        "legal":  {"headcount_driven", "cac_driven"},
        "bounds": (0.0, 3.0), "value_type": "float",
        "desc":   "scale the schedule quantity (hires or target customers)",
    },
    "shift_schedule": {
        "legal":  {"headcount_driven", "cac_driven"},
        "bounds": (-6, 6), "value_type": "int",
        "desc":   "shift the schedule later or earlier by whole months",
    },
}

# Per-driver bounds for set_driver (an absolute replacement value)
SET_BOUNDS = {
    "seasonal_yoy": (-1.0, 2.0),   # annual YoY growth fraction
    "margin_pct":   (0.0, 1.0),    # a margin cannot exceed 100 percent
    "growth_pct":   (-1.0, 2.0),   # monthly growth fraction
    "fixed":        (0.0, 1e9),    # an absolute euro amount
}

# Verbs that imply direction, used to sanity-check the sign of extracted values
NEG_VERBS = {"cut", "reduce", "drop", "lower", "slip", "delay",
             "fall", "decrease", "shrink", "trim"}
POS_VERBS = {"raise", "increase", "grow", "add", "accelerate",
             "boost", "expand", "bump"}

SCENARIO_MAX_CHANGES = 5   # FP&A guidance: keep scenarios to a few key drivers
