#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v0.7 (unified “Model name” headers)

• Clones / pulls the price‑list repo each run
• Excludes zero‑Qty rows and model FR‑S520SE‑0.2K‑19
• Calculates COGS, COGS×1.75, List Price, 1.27, discount tiers, GP %
• Sorts by capacity, then D → E → F → A → HEL
• Saves a version‑tagged PDF into ./pdf_reports/
"""

import os, re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── GIT SYNC ───────────────────────────────────────────
GIT_REPO    = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR   = pathlib.Path.cwd() / "repo"
DATA_SUBDIR = CLONE_DIR / "data"

def git_sync():
    if CLONE_DIR.exists():
        try:
            subprocess.run(
                ["git", "-C", str(CLONE_DIR), "pull", "--ff-only"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            print("[git] repo updated")
        except subprocess.CalledProcessError as e:
            print("[git] pull failed – using existing clone:", e.stderr.decode().strip())
    else:
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", GIT_REPO, str(CLONE_DIR)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            print("[git] repo cloned")
        except subprocess.CalledProcessError as e:
            print("[git] clone failed – falling back to local ‘data/’:", e.stderr.decode().strip())

git_sync()

# ─── CONFIG ────────────────────────────────────────────
DATA_DIR   = str(DATA_SUBDIR) if DATA_SUBDIR.exists() else "data"
OUT_DIR    = "pdf_reports"
MARGIN_IN  = 0.6                                   # inches
ROW_H      = 5                                     # mm
HDR_FONT   = BODY_FONT = 7

# ─── HELPERS ───────────────────────────────────────────
def money(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return ""

def series_tag(model):
    if "HEL" in model.upper():
        return "H"
    m = re.match(r"FR-([A-Z])", model)
    return m.group(1) if m else ""

def capacity_val(model):
    m = re.search(r"-(?:H)?([\d.]+)K", model)
    return float(m.group(1)) if m else 0.0

def fallback127(model, lookup):
    m = re.search(r"-(?:H)?([\d.]+)K", model)
    cap = m.group(1) + "K" if m else None
    if not cap:
        return None
    if "720" in model:
        return lookup.get(f"FR-E820-{cap}-1")
    if "740" in model:
        return lookup.get(f"FR-E840-{cap}-1")
    return None

# ─── LOCATE CSVs ───────────────────────────────────────
paths = glob.glob(os.path.join(DATA_DIR, "*.csv"))
find_hdr = lambda p: pd.read_csv(p, nrows=0).columns.str.strip().tolist()

inv_csv = price127_csv = listprice_csv = None
for p in paths:
    hdr = set(h.lower() for h in find_hdr(p))
    if {"qty owned", "total cost"}.issubset(hdr):
        inv_csv = p
    elif "1.27" in hdr:
        price127_csv = p
    elif {"listprice", "model name"}.issubset(hdr):
        listprice_csv = p

if not all((inv_csv, price127_csv, listprice_csv)):
    sys.exit("❌  One or more CSVs missing or mis‑named – aborting.")

# ─── LOAD CSVs ─────────────────────────────────────────
inv   = pd.read_csv(inv_csv)
p127  = pd.read_csv(price127_csv)
lp_df = pd.read_csv(listprice_csv)

# Standardise headers
inv.rename(columns=lambda c: c.strip(), inplace=True)
p127.rename(columns=lambda c: c.strip(), inplace=True)
lp_df.rename(columns=lambda c: c.strip(), inplace=True)

# Model text clean‑up
inv["Model"] = (
    inv["Model name"]
    .astype(str)
    .apply(lambda s: s.split("||")[-1].strip())  # remove any ‘100 || …’ remnants
)

# Inventory filters
inv = inv[
    (inv["Qty owned"] > 0)
    & (~inv["Model"].isin({"FR-S520SE-0.2K-19"}))
].copy()

# Core numeric cols
inv["Qty"]        = inv["Qty owned"].astype(int)
inv["TotalCost"]  = inv["Total cost"].astype(str).str.replace(",", "").astype(float)
inv["COGS"]       = inv["TotalCost"] / inv["Qty"]
inv["COGS_x1.75"] = inv["COGS"] * 1.75

# 1.27 mapping
p127_map = dict(
    zip(
        p127["Model name"].str.strip(),
        p127["1.27"].astype(str).str.replace(",", "").astype(float)
    )
)
inv["1.27"] = inv["Model"].apply(lambda m: p127_map.get(m, fallback127(m, p127_map)))

# List‑price mapping
lp_map = dict(
    zip(
        lp_df["Model name"].str.strip(),
        lp_df["ListPrice"].astype(str).str.replace(",", "").astype(float)
    )
)
def list_price(model):
    if model in lp_map:
        return lp_map[model]
    # cross‑series fall‑back (D/E/F/A mapping) – optional: keep if useful
    cap_m = re.search(r"-(?:H)?([\d.]+)K", model)
    if not cap_m:
        return None
    cap = cap_m.group(1) + "K"
    if any(t in model for t in ("D720", "E720", "E820")):
        return lp_map.get(f"FR-A820-{cap}-1") or lp_map.get(f"FR-E820-{cap}-1")
    if any(t in model for t in ("D740", "E740", "E840")):
        return lp_map.get(f"FR-A840-{cap}-1") or lp_map.get(f"FR-E840-{cap}-1")
    return None

inv["ListPrice"] = inv["Model"].apply(list_price)

# Discounts & GP
inv["Disc20"] = inv["ListPrice"] * 0.80
inv["Disc25"] = inv["ListPrice"] * 0.75
inv["Disc30"] = inv["ListPrice"] * 0.70
inv["GPpct"]  = (inv["ListPrice"] - inv["COGS"]) / inv["COGS"] * 100

# Sorting helpers
inv["Capacity"]    = inv["Model"].apply(capacity_val)
series_order       = {"D": 0, "E": 1, "F": 2, "A": 3, "H": 4}
inv["Series"]      = inv["Model"].apply(series_tag)
inv["SeriesOrder"] = inv["Series"].map(series_order).fillna(99)

inv.sort_values(["Capacity", "SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0, "SL", range(1, len(inv) + 1))

# ─── PDF OUTPUT ────────────────────────────────────────
class StockPDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 16)
        self.cell(0, 8, "VFD STOCK LIST", 0, 1, "C")
        self.ln(1)
        self.set_font("Arial", "", 10)
        self.cell(0, 5, datetime.now().strftime("Date: %d %B, %Y"), 0, 1, "C")
        self.cell(0, 5, "Smart Industrial Solution Ltd.", 0, 1, "C")
        self.ln(4)
    def footer(self):
        self.set_y(-12)
        self.set_font("Arial", "I", 8)
        self.cell(0, 6, f"Page {self.page_no()}", 0, 0, "C")

cols = [
    ("SL", 8, "C"), ("Model", 34, "L"), ("Qty", 8, "C"),
    ("List Price", 17, "R"), ("20% Disc", 17, "R"), ("25% Disc", 17, "R"),
    ("30% Disc", 17, "R"), ("GP%", 11, "R"), ("COGS", 17, "R"),
    ("COGS ×1.75", 18, "R"), ("1.27", 17, "R"),
]

pdf = StockPDF("P", "mm", "A4")
mm = MARGIN_IN * 25.4
pdf.set_margins(mm, 15, mm)
pdf.set_auto_page_break(True, 15)
pdf.add_page()

pdf.set_font("Arial", "B", HDR_FONT)
for title, w, ali in cols:
    pdf.cell(w, ROW_H, title, 1, 0, ali)
pdf.ln()

pdf.set_font("Arial", "", BODY_FONT)
shade = False
for _, r in inv.iterrows():
    fill = 242 if shade else 255
    pdf.set_fill_color(fill, fill, fill)
    pdf.cell(cols[0][1], ROW_H, str(int(r["SL"])),      1, 0, "C", shade)
    pdf.cell(cols[1][1], ROW_H, r["Model"],             1, 0, "L", shade)
    pdf.cell(cols[2][1], ROW_H, str(int(r["Qty"])),     1, 0, "C", shade)
    pdf.cell(cols[3][1], ROW_H, money(r["ListPrice"]),  1, 0, "R", shade)
    pdf.cell(cols[4][1], ROW_H, money(r["Disc20"]),     1, 0, "R", shade)
    pdf.cell(cols[5][1], ROW_H, money(r["Disc25"]),     1, 0, "R", shade)
    pdf.cell(cols[6][1], ROW_H, money(r["Disc30"]),     1, 0, "R", shade)
    pdf.cell(cols[7][1], ROW_H,
             f"{r['GPpct']:.2f}%" if pd.notna(r["GPpct"]) else "", 1, 0, "R", shade)
    pdf.cell(cols[8][1], ROW_H, money(r["COGS"]),       1, 0, "R", shade)
    pdf.cell(cols[9][1], ROW_H, money(r["COGS_x1.75"]), 1, 0, "R", shade)
    pdf.cell(cols[10][1], ROW_H, money(r["1.27"]),      1, 0, "R", shade)
    pdf.ln()
    shade = not shade

# Totals row
pdf.set_font("Arial", "B", BODY_FONT)
pdf.cell(cols[0][1] + cols[1][1], ROW_H, "Total", 1, 0, "R")
pdf.cell(cols[2][1], ROW_H, str(int(inv["Qty"].sum())), 1, 0, "C")
pdf.cell(sum(w for _, w, _ in cols[3:]), ROW_H, "", 1, 0)

# ─── Save PDF ──────────────────────────────────────────
os.makedirs(OUT_DIR, exist_ok=True)
tag      = datetime.now().strftime("%y%m%d")
existing = glob.glob(f"{OUT_DIR}/SISL_VFD_PL_{tag}_V.*.pdf")
pattern  = re.compile(r"_V\.(\d{2})\.pdf$")
vers     = [int(m.group(1)) for f in existing if (m := pattern.search(f))]
outfile  = f"SISL_VFD_PL_{tag}_V.{(max(vers) + 1 if vers else 5):02d}.pdf"

pdf.output(os.path.join(OUT_DIR, outfile))
print("✅  Generated:", outfile)
