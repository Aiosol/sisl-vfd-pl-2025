#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · FINAL
• Reads 3 CSVs from ./data:
    - VFD_PRICE_LAST.csv        (cols: Model Name, Qty owned, Total cost)
    - VFD_PRICE_JULY_2025.csv   (cols: Model Name, 1.27)
    - VFD_Price_SISL_Final.csv  (cols: Serial, Model Name, List Price)
• Normalises model keys (upper, no spaces, strip trailing -1)
• Calculates COGS, COGS×1.75, List Price, 1.27, discounts, GP%
• Sorts by capacity then series D→E→F→A→H
• Outputs version‑tagged PDF into ./pdf_reports/
"""

import re, glob, sys, pathlib, subprocess
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── Optional git backup clone into repo/data ─────────
REPO = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE = pathlib.Path("repo")
BACKUP = CLONE / "data"
if not BACKUP.exists():
    try:
        subprocess.run(
            ["git","clone","--depth","1",REPO,str(CLONE)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        pass  # offline

# ─── Paths ─────────────────────────────────────────────
DATA_DIR = pathlib.Path("data")
OUT_DIR  = pathlib.Path("pdf_reports"); OUT_DIR.mkdir(exist_ok=True)

INV_CSV   = DATA_DIR / "VFD_PRICE_LAST.csv"
P127_CSV  = DATA_DIR / "VFD_PRICE_JULY_2025.csv"
LIST_CSV  = DATA_DIR / "VFD_Price_SISL_Final.csv"
for f in (INV_CSV, P127_CSV, LIST_CSV):
    if not f.exists():
        sys.exit(f"❌ Missing {f}")

# ─── Helper to normalise model strings ────────────────
def norm(m: str) -> str:
    s = str(m).upper().strip()
    s = re.sub(r"\s+", "", s)         # remove all spaces
    return re.sub(r"-1$", "", s)      # strip trailing "-1"

# ─── Load & clean inventory ───────────────────────────
inv = pd.read_csv(INV_CSV, dtype=str).applymap(lambda x: x.strip() if isinstance(x,str) else x)
# map columns to known names
inv.rename(columns={
    "Model Name": "ModelName",
    "Qty owned":  "QtyOwned",
    "Total cost": "TotalCost",
}, inplace=True)
# sanity check
for c in ("ModelName","QtyOwned","TotalCost"):
    if c not in inv.columns:
        sys.exit(f"❌ Inventory missing column {c}")

inv["__M__"]      = inv["ModelName"].apply(norm)
inv = inv[inv["QtyOwned"].astype(float) > 0].copy()
inv["Qty"]        = inv["QtyOwned"].astype(int)
inv["TotalCostF"] = inv["TotalCost"].str.replace(",","").astype(float)
inv["COGS"]       = inv["TotalCostF"] / inv["Qty"]
inv["COGS×1.75"]  = inv["COGS"] * 1.75

# ─── Load & clean 1.27 list ───────────────────────────
p127 = pd.read_csv(P127_CSV, dtype=str).applymap(lambda x: x.strip() if isinstance(x,str) else x)
p127.rename(columns={"Model Name":"ModelName","1.27":"Price127"}, inplace=True)
if "ModelName" not in p127.columns or "Price127" not in p127.columns:
    sys.exit("❌ 1.27 file missing expected columns")
p127["__M__"] = p127["ModelName"].apply(norm)
p127["Price127F"] = p127["Price127"].str.replace(",","").astype(float)

# ─── Load & clean list‑price map ──────────────────────
plist = pd.read_csv(LIST_CSV, dtype=str).applymap(lambda x: x.strip() if isinstance(x,str) else x)
plist.rename(columns={"Model Name":"ModelName","List Price":"ListPrice"}, inplace=True)
if "ModelName" not in plist.columns or "ListPrice" not in plist.columns:
    sys.exit("❌ List‑price file missing expected columns")
plist["__M__"]      = plist["ModelName"].apply(norm)
plist["ListPriceF"] = plist["ListPrice"].str.replace(",","").astype(float)

# ─── Build lookup dicts ───────────────────────────────
p127_map  = dict(zip(p127["__M__"], p127["Price127F"]))
plist_map = dict(zip(plist["__M__"], plist["ListPriceF"]))

def fallback127(m: str):
    cap = re.search(r"-(?:H)?([\d.]+)K", m)
    if not cap: return None
    key720 = f"FR-E820-{cap[1]}K"
    key740 = f"FR-E840-{cap[1]}K"
    return p127_map.get(key720) or p127_map.get(key740)

inv["1.27"]      = inv["__M__"].apply(lambda m: p127_map.get(m) or fallback127(m))
inv["List Price"]= inv["__M__"].map(plist_map)

# ─── Discounts & GP% ──────────────────────────────────
inv["20% Disc"] = inv["List Price"] * 0.80
inv["25% Disc"] = inv["List Price"] * 0.75
inv["30% Disc"] = inv["List Price"] * 0.70
inv["GP%"]      = (inv["List Price"] - inv["COGS"]) / inv["COGS"] * 100

# ─── Capacity & series for sorting ───────────────────
def cap_val(m: str) -> float:
    cap = re.search(r"-(?:H)?([\d.]+)K", m)
    return float(cap[1]) if cap else 0.0

def series_tag(m: str) -> str:
    if "HEL" in m: return "H"
    mo = re.match(r"FR-([A-Z])", m)
    return mo.group(1) if mo else ""

inv["Capacity"]    = inv["__M__"].apply(cap_val)
order = {"D":0,"E":1,"F":2,"A":3,"H":4}
inv["SeriesOrder"] = inv["__M__"].apply(series_tag).map(order).fillna(99)
inv.sort_values(["Capacity","SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0, "SL", range(1, len(inv)+1))

# ─── PDF output ───────────────────────────────────────
class StockPDF(FPDF):
    def header(self):
        self.set_font("Arial","B",16)
        self.cell(0,8,"VFD STOCK LIST",0,1,"C")
        self.ln(1)
        self.set_font("Arial","",10)
        self.cell(0,5,datetime.now().strftime("Date: %d %B %Y"),0,1,"C")
        self.cell(0,5,"Smart Industrial Solution Ltd.",0,1,"C")
        self.ln(4)
    def footer(self):
        self.set_y(-12)
        self.set_font("Arial","I",8)
        self.cell(0,6,f"Page {self.page_no()}",0,0,"C")

cols = [
    ("SL",8,"C"),("Model",34,"L"),("Qty",8,"C"),
    ("List Price",17,"R"),("20% Disc",17,"R"),("25% Disc",17,"R"),
    ("30% Disc",17,"R"),("GP%",11,"R"),("COGS",17,"R"),
    ("COGS×1.75",18,"R"),("1.27",17,"R"),
]

def fmt(x):
    return f"{x:,.2f}" if isinstance(x,(int,float)) else str(x)

pdf = StockPDF("P","mm","A4")
pdf.set_margins(0.6*25.4,15,0.6*25.4)
pdf.set_auto_page_break(True,15)
pdf.add_page()

pdf.set_font("Arial","B",7)
for title,w,a in cols:
    pdf.cell(w,5,title,1,0,a)
pdf.ln()

pdf.set_font("Arial","",7)
shade=False
for _,r in inv.iterrows():
    pdf.set_fill_color(*(242,)*3 if shade else (255,255,255))
    row = [
        r["SL"], r["__M__"], r["Qty"],
        r["List Price"], r["20% Disc"], r["25% Disc"], r["30% Disc"],
        f"{r['GP%']:.2f}%" if pd.notna(r["GP%"]) else "",
        r["COGS"], r["COGS×1.75"], r["1.27"]
    ]
    for (_,w,a),v in zip(cols,row):
        pdf.cell(w,5,fmt(v),1,0,a,shade)
    pdf.ln(); shade = not shade

pdf.set_font("Arial","B",7)
pdf.cell(cols[0][1]+cols[1][1],5,"Total",1,0,"R")
pdf.cell(cols[2][1],5,str(inv["Qty"].sum()),1,0,"C")
pdf.cell(sum(w for _,w,_ in cols[3:]),5,"",1,0)

tag      = datetime.now().strftime("%y%m%d")
existing = glob.glob(f"{OUT_DIR}/SISL_VFD_PL_{tag}_V.*.pdf")
vers     = [int(re.search(r"_V\.(\d{2})",f).group(1)) for f in existing]
outfile  = OUT_DIR / f"SISL_VFD_PL_{tag}_V.{(max(vers)+1 if vers else 5):02d}.pdf"

pdf.output(str(outfile))
print("✅ Generated:", outfile)
