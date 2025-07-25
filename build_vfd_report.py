#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v0.6 (git‑enabled)

– Automatically clones / pulls the Git repo that contains the CSVs  
– Excludes zero‑Qty and model FR‑S520SE‑0.2K‑19  
– Calculates COGS, COGS×1.75, List Price, 1.27, discounts, GP %  
– Sort order: capacity then D → E → F → A → HEL  
– Outputs version‑tagged PDF to ./pdf_reports/
"""

import os, re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── GIT SYNC (clone / pull) ───────────────────────────
GIT_REPO   = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR  = pathlib.Path.cwd() / "repo"      # local clone path
DATA_SUBDIR = CLONE_DIR / "data"              # CSVs are here

def git_sync():
    """Clone once or pull latest CSVs each run."""
    if CLONE_DIR.exists():
        try:
            subprocess.run(
                ["git", "-C", str(CLONE_DIR), "pull", "--ff-only"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            print("[git] repo updated.")
        except subprocess.CalledProcessError as e:
            print("[git] pull failed, using existing clone:", e.stderr.decode().strip())
    else:
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", GIT_REPO, str(CLONE_DIR)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            print("[git] repo cloned.")
        except subprocess.CalledProcessError as e:
            print("[git] clone failed, falling back to local data:", e.stderr.decode().strip())

git_sync()

# ─── CONFIG ────────────────────────────────────────────
DATA_DIR  = str(DATA_SUBDIR) if DATA_SUBDIR.exists() else "data"
OUT_DIR   = "pdf_reports"
MARGIN_INCH, ROW_H = 0.6, 5   # inch → mm row height
HDR_FONT, BODY_FONT = 7, 7

# ─── UTIL ──────────────────────────────────────────────
def money(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return ""

# ─── LOCATE THE 3 CSVs ─────────────────────────────────
paths = glob.glob(os.path.join(DATA_DIR, "*.csv"))
inv_csv = price127_csv = listprice_csv = None

for p in paths:
    if os.path.basename(p).lower() == "vfd_price_sisl_final.csv":
        listprice_csv = p
        break

for p in paths:
    hdr = pd.read_csv(p, nrows=0).columns.str.strip().tolist()
    if {"Qty owned", "Total cost"}.issubset(hdr):
        inv_csv = p
    elif "1.27" in hdr:
        price127_csv = p

if not listprice_csv:
    leftovers = [p for p in paths if p not in (inv_csv, price127_csv)]
    listprice_csv = leftovers[0] if leftovers else None

if not all((inv_csv, price127_csv, listprice_csv)):
    sys.exit("Inventory, 1.27, or list‑price CSV missing after git‑sync.")

# ─── PARSE MASTER LIST‑PRICE CSV ───────────────────────
def parse_listprice(fp):
    df = pd.read_csv(fp, dtype=str, keep_default_na=False).applymap(str.strip)
    mp = {}
    for _, row in df.iterrows():
        cells = [c for c in row if c]
        for i, c in enumerate(cells):
            if c.startswith("FR-"):
                model = c.split()[0]
                for nxt in cells[i + 1:]:
                    if re.fullmatch(r"[\d,]+(?:\.\d+)?", nxt):
                        mp[model] = float(nxt.replace(",", ""))
                        break
    return mp

# ─── FALLBACK LOOKUPS ──────────────────────────────────
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

def list_price(model, lp):
    """Return list price, using cross‑series fallback if needed."""
    if model in lp:
        return lp[model]

    mcap = re.search(r"-(?:H)?([\d.]+)K", model)
    family = mcap.group(1) + "K" if mcap else None
    if family:
        if any(t in model for t in ("D720", "D720S", "E720", "E820")):
            return lp.get(f"FR-A820-{family}-1") or lp.get(f"FR-E820-{family}-1")
        if any(t in model for t in ("D740", "E740", "E840")):
            return lp.get(f"FR-A840-{family}-1") or lp.get(f"FR-E840-{family}-1")

    m = re.match(r"FR-([A-Z])", model)
    if m and family:
        series = m.group(1)
        for alt in "AEFD":
            if alt == series:
                continue
            alt_model = re.sub(r"FR-[A-Z]", f"FR-{alt}", model, 1)
            if alt_model in lp:
                return lp[alt_model]

    return None

# ─── SERIES / CAPACITY HELPERS ─────────────────────────
def series_tag(model):
    if re.search(r"FR-HEL", model, re.I):
        return "H"
    m = re.match(r"FR-([A-Z])", model)
    return m.group(1) if m else ""

def capacity_val(model):
    m = re.search(r"-(?:H)?([\d.]+)K", model)
    return float(m.group(1)) if m else 0.0

# ─── LOAD & TRANSFORM DATA ─────────────────────────────
lp_map = parse_listprice(listprice_csv)

inv = pd.read_csv(inv_csv)
inv.columns = inv.columns.str.strip()

col_src = "Name" if "Name" in inv.columns else "Model"
inv["Model"] = (
    inv[col_src]
    .astype(str)
    .apply(lambda s: s.split("||")[-1].strip())
    .replace({"FR-D720S-025-NA": "FR-D720S-0.4K"})
)

inv = inv[
    (inv["Qty owned"] > 0)
    & ~inv["Model"].isin({"FR-S520SE-0.2K-19"})
]

inv["Qty"]        = inv["Qty owned"].astype(int)
inv["TotalCost"]  = inv["Total cost"].str.replace(",", "").astype(float)
inv["COGS"]       = inv["TotalCost"] / inv["Qty"]
inv["COGS_x1.75"] = inv["COGS"] * 1.75

p127 = pd.read_csv(price127_csv)
p127_map = dict(
    zip(
        p127.iloc[:, 0].str.strip(),
        p127.iloc[:, 1].astype(str).str.replace(",", "").astype(float),
    )
)
inv["1.27"]      = inv["Model"].apply(lambda m: p127_map.get(m, fallback127(m, p127_map)))
inv["Series"]    = inv["Model"].apply(series_tag)
inv["ListPrice"] = inv["Model"].apply(lambda m: list_price(m, lp_map))

inv["Disc20"] = inv["ListPrice"] * 0.80
inv["Disc25"] = inv["ListPrice"] * 0.75
inv["Disc30"] = inv["ListPrice"] * 0.70
inv["GPpct"]  = (inv["ListPrice"] - inv["COGS"]) / inv["COGS"] * 100

inv["Capacity"]    = inv["Model"].apply(capacity_val)
order_map          = {"D": 0, "E": 1, "F": 2, "A": 3, "H": 4}
inv["SeriesOrder"] = inv["Series"].map(order_map).fillna(99)
inv.sort_values(["Capacity", "SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0, "SL", range(1, len(inv) + 1))

# ─── PDF OUTPUT ─────────────────────────────────────────
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
mm = MARGIN_INCH * 25.4
pdf.set_margins(mm, 15, mm)
pdf.set_auto_page_break(True, 15)
pdf.add_page()

pdf.set_font("Arial", "B", HDR_FONT)
for title, width, align in cols:
    pdf.cell(width, ROW_H, title, 1, 0, align)
pdf.ln()

pdf.set_font("Arial", "", BODY_FONT)
shade = False
for _, r in inv.iterrows():
    fill = 242 if shade else 255
    pdf.set_fill_color(fill, fill, fill)

    pdf.cell(cols[0][1], ROW_H, str(int(r["SL"])), 1, 0, "C", shade)
    pdf.cell(cols[1][1], ROW_H, r["Model"], 1, 0, "L", shade)
    pdf.cell(cols[2][1], ROW_H, str(int(r["Qty"])), 1, 0, "C", shade)
    pdf.cell(cols[3][1], ROW_H, money(r["ListPrice"]), 1, 0, "R", shade)
    pdf.cell(cols[4][1], ROW_H, money(r["Disc20"]), 1, 0, "R", shade)
    pdf.cell(cols[5][1], ROW_H, money(r["Disc25"]), 1, 0, "R", shade)
    pdf.cell(cols[6][1], ROW_H, money(r["Disc30"]), 1, 0, "R", shade)
    pdf.cell(cols[7][1], ROW_H, f"{r['GPpct']:.2f}%" if pd.notna(r["GPpct"]) else "", 1, 0, "R", shade)
    pdf.cell(cols[8][1], ROW_H, money(r["COGS"]), 1, 0, "R", shade)
    pdf.cell(cols[9][1], ROW_H, money(r["COGS_x1.75"]), 1, 0, "R", shade)
    pdf.cell(cols[10][1], ROW_H, money(r["1.27"]), 1, 0, "R", shade)

    pdf.ln()
    shade = not shade

pdf.set_font("Arial", "B", BODY_FONT)
pdf.cell(cols[0][1] + cols[1][1], ROW_H, "Total", 1, 0, "R")
pdf.cell(cols[2][1], ROW_H, str(int(inv["Qty"].sum())), 1, 0, "C")
pdf.cell(sum(w for _, w, _ in cols[3:]), ROW_H, "", 1, 0)

# ─── version‑tagged filename ───────────────────────────
os.makedirs(OUT_DIR, exist_ok=True)
tag = datetime.now().strftime("%y%m%d")
existing = glob.glob(f"{OUT_DIR}/SISL_VFD_PL_{tag}_V.*.pdf")
pattern  = re.compile(r"_V\.(\d{2})\.pdf$")
vers     = [int(m.group(1)) for f in existing if (m := pattern.search(os.path.basename(f)))]
outfile  = f"SISL_VFD_PL_{tag}_V.{(max(vers) + 1 if vers else 5):02d}.pdf"

pdf.output(os.path.join(OUT_DIR, outfile))
print("Generated:", outfile)
