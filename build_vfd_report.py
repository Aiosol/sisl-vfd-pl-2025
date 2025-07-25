#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v0.7.1 
(uses local data/ folder first; unified “Model name” headers)

• Clones / pulls the Git repo that contains historical CSVs (fallback only)
• Reads three local CSVs:
      data/VFD_PRICE_LAST.csv
      data/VFD_PRICE_JULY_2025.csv
      data/VFD_Price_SISL_Final.csv
  each with first column  'Model name'
• Excludes zero‑Qty rows and FR‑S520SE‑0.2K‑19
• Calculates COGS, COGS×1.75, List Price, 1.27, discount tiers, GP %
• Sorts by capacity, then D → E → F → A → HEL
• Outputs version‑tagged PDF into ./pdf_reports/
"""

import os, re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── Git clone (fallback / reference only) ─────────────
GIT_REPO    = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR   = pathlib.Path.cwd() / "repo"
DATA_BACKUP = CLONE_DIR / "data"

def git_sync():
    try:
        if CLONE_DIR.exists():
            subprocess.run(
                ["git", "-C", str(CLONE_DIR), "pull", "--ff-only"],
                check=True, stdout=subprocess.DEVNULL
            )
            print("[git] repo updated (backup only)")
        else:
            subprocess.run(
                ["git", "clone", "--depth", "1", GIT_REPO, str(CLONE_DIR)],
                check=True, stdout=subprocess.DEVNULL
            )
            print("[git] repo cloned (backup only)")
    except subprocess.CalledProcessError:
        print("[git] clone/pull failed – continuing with local data/")

git_sync()

# ─── Path configuration ───────────────────────────────
DATA_DIR = pathlib.Path.cwd() / "data"        # primary source
OUT_DIR  = pathlib.Path.cwd() / "pdf_reports"
OUT_DIR.mkdir(exist_ok=True)

# ─── Helper functions ─────────────────────────────────
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

# ─── Locate the three CSVs in local data/ ─────────────
def find_csv(name_fragment):
    matches = list(DATA_DIR.glob(f"*{name_fragment}*.csv"))
    if matches:
        return matches[0]
    # fallback to repo/data if not in local
    matches = list(DATA_BACKUP.glob(f"*{name_fragment}*.csv"))
    return matches[0] if matches else None

inv_csv     = find_csv("LAST")
price127_csv = find_csv("JULY_2025")
listprice_csv = find_csv("Final")

if not all((inv_csv, price127_csv, listprice_csv)):
    sys.exit("❌  One or more CSVs missing – check data/ folder.")

# ─── Load CSVs (expecting 'Model name' header) ────────
inv   = pd.read_csv(inv_csv)
p127  = pd.read_csv(price127_csv)
lp_df = pd.read_csv(listprice_csv)

for df in (inv, p127, lp_df):
    df.rename(columns=lambda c: c.strip(), inplace=True)

# Validate mandatory columns
req_cols = {
    "inv":  {"Model name", "Qty owned", "Total cost"},
    "p127": {"Model name", "1.27"},
    "lp":   {"Model name", "ListPrice"},
}
for tag, cols in req_cols.items():
    df = {"inv": inv, "p127": p127, "lp": lp_df}[tag]
    if not cols.issubset(set(c.strip() for c in df.columns)):
        sys.exit(f"❌  {tag} CSV lacks required columns: {cols}")

# ─── Clean and compute inventory dataframe ────────────
inv["Model"] = inv["Model name"].astype(str).str.split("||").str[-1].str.strip()

inv = inv[
    (inv["Qty owned"] > 0)
    & (~inv["Model"].isin({"FR-S520SE-0.2K-19"}))
].copy()

inv["Qty"]        = inv["Qty owned"].astype(int)
inv["TotalCost"]  = inv["Total cost"].astype(str).str.replace(",", "").astype(float)
inv["COGS"]       = inv["TotalCost"] / inv["Qty"]
inv["COGS_x1.75"] = inv["COGS"] * 1.75

# Map 1.27 prices
p127_map = dict(zip(p127["Model name"].str.strip(),
                    p127["1.27"].astype(str).str.replace(",", "").astype(float)))
inv["1.27"] = inv["Model"].apply(lambda m: p127_map.get(m, fallback127(m, p127_map)))

# Map list prices with simple lookup
lp_map = dict(zip(lp_df["Model name"].str.strip(),
                  lp_df["ListPrice"].astype(str).str.replace(",", "").astype(float)))
inv["ListPrice"] = inv["Model"].map(lp_map)

# Discounts & GP
inv["Disc20"] = inv["ListPrice"] * 0.80
inv["Disc25"] = inv["ListPrice"] * 0.75
inv["Disc30"] = inv["ListPrice"] * 0.70
inv["GPpct"]  = (inv["ListPrice"] - inv["COGS"]) / inv["COGS"] * 100

# Sorting
inv["Capacity"]    = inv["Model"].apply(capacity_val)
order_map          = {"D": 0, "E": 1, "F": 2, "A": 3, "H": 4}
inv["Series"]      = inv["Model"].apply(series_tag)
inv["SeriesOrder"] = inv["Series"].map(order_map).fillna(99)
inv.sort_values(["Capacity", "SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0, "SL", range(1, len(inv) + 1))

# ─── PDF generation ───────────────────────────────────
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
margin_mm = 0.6 * 25.4
pdf.set_margins(margin_mm, 15, margin_mm)
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

# Save with version tag
tag       = datetime.now().strftime("%y%m%d")
existing  = glob.glob(str(OUT_DIR / f"SISL_VFD_PL_{tag}_V.*.pdf"))
pattern   = re.compile(r"_V\.(\d{2})\.pdf$")
versions  = [int(m.group(1)) for f in existing if (m := pattern.search(f))]
outfile   = OUT_DIR / f"SISL_VFD_PL_{tag}_V.{(max(versions) + 1 if versions else 5):02d}.pdf"

pdf.output(str(outfile))
print("✅  Generated:", outfile)
