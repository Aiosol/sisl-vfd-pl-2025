#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v1.0
• Reads three CSVs in ./data (fallback to repo/data)
• Fuzzy header matching for all CSVs (now Qty owned won’t miss)
• Safe series‑tag extraction (no NoneType errors)
• Outputs version‑tagged PDF into ./pdf_reports
"""

import os, re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── Optional git‑backup clone into repo/data ─────────
GIT_REPO  = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR = pathlib.Path.cwd() / "repo"
BACKUP    = CLONE_DIR / "data"
if not BACKUP.exists():
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", GIT_REPO, str(CLONE_DIR)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        print("[git] clone skipped (offline)")

DATA_DIR = pathlib.Path.cwd() / "data"
OUT_DIR  = pathlib.Path.cwd() / "pdf_reports"; OUT_DIR.mkdir(exist_ok=True)

def find_csv(token:str):
    return next(DATA_DIR.glob(f"*{token}*.csv"), None) \
        or next(BACKUP.glob(f"*{token}*.csv"), None)

INV_CSV   = find_csv("LAST")
P127_CSV  = find_csv("JULY_2025")
LIST_CSV  = find_csv("Final")
if not all((INV_CSV, P127_CSV, LIST_CSV)):
    sys.exit("❌ One or more CSVs missing in ./data")

# ─── Fuzzy header mapper ───────────────────────────────
def map_cols(df: pd.DataFrame, spec):
    df = df.rename(columns=lambda c: c.strip())
    cols = list(df.columns)
    mapping = {}
    for need, variants in spec.items():
        found = False
        for col in cols:
            low = col.lower()
            # build a list of alternative match-conditions
            options = (
                variants
                if isinstance(variants, list) and isinstance(variants[0], list)
                else [variants]
            )
            for opt in options:
                if isinstance(opt, str):
                    if opt.lower() in low:
                        mapping[col] = need; found = True; break
                else:
                    # opt is list of tokens
                    if all(tok in low for tok in opt):
                        mapping[col] = need; found = True; break
            if found: break
        if not found:
            print("⚠️ Header row:", cols)
            raise ValueError(f"❌ Column '{need}' not found.")
    return df.rename(columns=mapping)

# ─── Load & normalize CSVs ─────────────────────────────
inv   = map_cols(pd.read_csv(INV_CSV), {
    "Model name": [["model","name"], "name", ["material","name"]],
    "Qty owned":  [["qty"], ["quantity"], ["qty","owned"]],
    "Total cost": [["total","cost"]]
})
p127  = map_cols(pd.read_csv(P127_CSV), {
    "Model name": [["model","name"], "name", ["material","name"]],
    "1.27":       "1.27"
})
plist = map_cols(pd.read_csv(LIST_CSV), {
    "Model name": [["model","name"], "name", ["material","name"]],
    "ListPrice":  [["list","price"], "listprice","price list"]
})

# ─── Inventory cleanup & calculations ────────────────
inv["Model"] = (
    inv["Model name"].astype(str)
       .str.split("||").str[-1]
       .str.strip()
)
inv = inv[(inv["Qty owned"]>0) & ~inv["Model"].eq("FR-S520SE-0.2K-19")].copy()
inv["Qty"]       = inv["Qty owned"].astype(int, errors="ignore")
inv["TotalCost"] = (
    inv["Total cost"].astype(str)
       .str.replace(",", "")
       .astype(float)
)
inv["COGS"]       = inv["TotalCost"] / inv["Qty"]
inv["COGS_x1.75"] = inv["COGS"] * 1.75

# ─── Build price lookup maps ───────────────────────────
p127_map  = dict(zip(
    p127["Model name"].str.strip(),
    p127["1.27"].astype(str).str.replace(",", "").astype(float)
))
plist_map = dict(zip(
    plist["Model name"].str.strip(),
    plist["ListPrice"].astype(str).str.replace(",", "").astype(float)
))

def fallback127(m:str):
    capm = re.search(r"-(?:H)?([\d.]+)K", m)
    if not capm: return None
    cap = capm[1] + "K"
    if "720" in m: return p127_map.get(f"FR-E820-{cap}-1")
    if "740" in m: return p127_map.get(f"FR-E840-{cap}-1")

inv["1.27"]      = inv["Model"].apply(lambda m: p127_map.get(m) or fallback127(m))
inv["ListPrice"] = inv["Model"].map(plist_map)

# ─── Discounts & GP% ──────────────────────────────────
inv["Disc20"] = inv["ListPrice"] * 0.80
inv["Disc25"] = inv["ListPrice"] * 0.75
inv["Disc30"] = inv["ListPrice"] * 0.70
inv["GPpct"]  = (inv["ListPrice"] - inv["COGS"]) / inv["COGS"] * 100

# ─── Series & capacity helpers (safe) ────────────────
def series_tag(m:str) -> str:
    if not isinstance(m, str): return ""
    mu = m.upper()
    if "HEL" in mu: return "H"
    match = re.match(r"FR-([A-Z])", m)
    return match.group(1) if match else ""
def capacity_val(m:str) -> float:
    match = re.search(r"-(?:H)?([\d.]+)K", m)
    return float(match[1]) if match else 0.0

inv["Capacity"]    = inv["Model"].apply(capacity_val)
inv["SeriesOrder"] = inv["Model"].apply(series_tag).map(
    {"D":0,"E":1,"F":2,"A":3,"H":4}
).fillna(99)
inv.sort_values(["Capacity","SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0, "SL", range(1, len(inv)+1))

# ─── PDF generation ───────────────────────────────────
class StockPDF(FPDF):
    def header(self):
        self.set_font("Arial","B",16)
        self.cell(0,9,"VFD STOCK LIST",0,1,"C"); self.ln(1)
        self.set_font("Arial","",10)
        self.cell(0,5,datetime.now().strftime("Date: %d %B %Y"),0,1,"C")
        self.cell(0,5,"Smart Industrial Solution Ltd.",0,1,"C"); self.ln(4)
    def footer(self):
        self.set_y(-12)
        self.set_font("Arial","I",8)
        self.cell(0,6,f"Page {self.page_no()}",0,0,"C")

cols = [
    ("SL",8,"C"),("Model",34,"L"),("Qty",8,"C"),
    ("List Price",17,"R"),("20% Disc",17,"R"),("25% Disc",17,"R"),
    ("30% Disc",17,"R"),("GP%",11,"R"),("COGS",17,"R"),
    ("COGS ×1.75",18,"R"),("1.27",17,"R"),
]

def fmt(v):
    return f"{v:,.2f}" if isinstance(v,(int,float)) else str(v)

pdf = StockPDF("P","mm","A4")
pdf.set_margins(0.6*25.4, 15, 0.6*25.4)
pdf.set_auto_page_break(True,15)
pdf.add_page()

pdf.set_font("Arial","B",7)
for title, w, align in cols:
    pdf.cell(w,5,title,1,0,align)
pdf.ln()

pdf.set_font("Arial","",7)
shade = False
for _, r in inv.iterrows():
    pdf.set_fill_color(*(242,)*3 if shade else (255,255,255))
    row = [
        r["SL"], r["Model"], r["Qty"],
        r["ListPrice"], r["Disc20"], r["Disc25"], r["Disc30"],
        f"{r['GPpct']:.2f}%" if pd.notna(r["GPpct"]) else "",
        r["COGS"], r["COGS_x1.75"], r["1.27"]
    ]
    for (_, w, align), val in zip(cols, row):
        pdf.cell(w,5,fmt(val),1,0,align,shade)
    pdf.ln(); shade = not shade

pdf.set_font("Arial","B",7)
pdf.cell(cols[0][1]+cols[1][1],5,"Total",1,0,"R")
pdf.cell(cols[2][1],5,str(inv["Qty"].sum()),1,0,"C")
pdf.cell(sum(w for _,w,_ in cols[3:]),5,"",1,0)

tag      = datetime.now().strftime("%y%m%d")
exist    = glob.glob(str(OUT_DIR/f"SISL_VFD_PL_{tag}_V.*.pdf"))
ver      = max([int(re.search(r"_V\.(\d{2})",f).group(1)) for f in exist], default=4)+1
outfile  = OUT_DIR / f"SISL_VFD_PL_{tag}_V.{ver:02d}.pdf"

pdf.output(str(outfile))
print("✅ Generated:", outfile)
