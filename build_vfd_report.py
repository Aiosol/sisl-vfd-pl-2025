#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v1.4
• Reads three CSVs from ./data (fallback to repo/data)
• Maps fuzzy headers: Model Name, Qty owned, Total cost, 1.27, List Price
• Normalizes model strings (UPPER, no spaces, strip '-1')
• Generates version‑tagged PDF in ./pdf_reports
"""

import re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── Optional git backup into repo/data ─────────────────
REPO_URL  = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR = pathlib.Path.cwd() / "repo"
BACKUP    = CLONE_DIR / "data"
if not BACKUP.exists():
    try:
        subprocess.run(
            ["git","clone","--depth","1",REPO_URL,str(CLONE_DIR)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        print("[git] offline – skip clone")

# ─── Paths ─────────────────────────────────────────────
DATA_DIR = pathlib.Path.cwd() / "data"
OUT_DIR  = pathlib.Path.cwd() / "pdf_reports"; OUT_DIR.mkdir(exist_ok=True)

def find_csv(token: str):
    return next(DATA_DIR.glob(f"*{token}*.csv"), None) \
        or next(BACKUP.glob(f"*{token}*.csv"), None)

INV_CSV   = find_csv("LAST")
P127_CSV  = find_csv("JULY_2025")
LIST_CSV  = find_csv("Final")
if not (INV_CSV and P127_CSV and LIST_CSV):
    sys.exit("❌ Missing one of the three CSVs in ./data")

# ─── Fuzzy header mapping ──────────────────────────────
def map_cols(df, spec):
    df = df.rename(columns=lambda c: c.strip())
    cols = df.columns.tolist()
    mapping = {}
    for need, patterns in spec.items():
        for col in cols:
            low = col.lower()
            for pat in patterns:
                if isinstance(pat, str):
                    if pat.lower() in low:
                        mapping[col] = need; break
                else:
                    # pat is a list of substrings
                    if all(tok.lower() in low for tok in pat):
                        mapping[col] = need; break
            if col in mapping:
                break
        if need not in mapping.values():
            print("⚠️ Header row:", cols)
            raise ValueError(f"❌ Required column '{need}' not found.")
    return df.rename(columns=mapping)

# ─── Load & map CSVs ──────────────────────────────────
inv = map_cols(
    pd.read_csv(INV_CSV),
    {
        "Model Name": [["model","name"], "name"],
        "Qty owned":  [["qty","owned"], "quantity"],
        "Total cost": [["total","cost"]]
    }
)

p127 = map_cols(
    pd.read_csv(P127_CSV),
    {
        "Model Name": [["model","name"], "name"],
        "1.27":       ["1.27"]
    }
)

plist = map_cols(
    pd.read_csv(LIST_CSV),
    {
        # Skip the Serial column automatically
        "Model Name": [["model","name"], "name"],
        "List Price": [["list","price"], "price list"]
    }
)

# ─── Normalize model keys ─────────────────────────────
def normalize(m: str) -> str:
    # strip whitespace, uppercase, remove trailing "-1" if present
    s = re.sub(r"\s+","", str(m)).upper()
    return re.sub(r"-1$", "", s)

inv["__M__"]   = inv["Model Name"].apply(normalize)
p127["__M__"]  = p127["Model Name"].apply(normalize)
plist["__M__"] = plist["Model Name"].apply(normalize)

# ─── Clean & compute inventory metrics ───────────────
inv = inv[inv["Qty owned"].astype(float, errors="ignore") > 0].copy()
inv["Qty"]       = inv["Qty owned"].astype(int, errors="ignore")
inv["TotalCost"] = inv["Total cost"].astype(str).str.replace(",","").astype(float)
inv["COGS"]      = inv["TotalCost"] / inv["Qty"]
inv["COGS×1.75"] = inv["COGS"] * 1.75

# ─── Build price lookup dicts ─────────────────────────
p127_map  = dict(zip(
    p127["__M__"],
    p127["1.27"].astype(str).str.replace(",","").astype(float)
))
plist_map = dict(zip(
    plist["__M__"],
    plist["List Price"].astype(str).str.replace(",","").astype(float)
))

def fallback127(m):
    cap = re.search(r"-(?:H)?([\d.]+)K", m)
    if cap:
        c = cap[1] + "K"
        for prefix in ("FR-E820","FR-E840"):
            val = p127_map.get(f"{prefix}-{c}")
            if val is not None:
                return val
    return None

inv["1.27"]      = inv["__M__"].apply(lambda m: p127_map.get(m) or fallback127(m))
inv["List Price"] = inv["__M__"].map(plist_map)

# ─── Discounts & GP% ─────────────────────────────────
inv["20% Disc"] = inv["List Price"] * 0.80
inv["25% Disc"] = inv["List Price"] * 0.75
inv["30% Disc"] = inv["List Price"] * 0.70
inv["GP%"]      = (inv["List Price"] - inv["COGS"]) / inv["COGS"] * 100

# ─── Capacity & Series for sorting ───────────────────
def cap_val(m: str) -> float:
    match = re.search(r"-(?:H)?([\d.]+)K", m)
    return float(match[1]) if match else 0.0

def series_tag(m: str) -> str:
    if "HEL" in m:
        return "H"
    match = re.match(r"FR-([A-Z])", m)
    return match.group(1) if match else ""

inv["Capacity"]    = inv["__M__"].apply(cap_val)
order_map          = {"D":0,"E":1,"F":2,"A":3,"H":4}
inv["SeriesOrder"] = inv["__M__"].apply(series_tag).map(order_map).fillna(99)

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
    ("SL",8,"C"), ("Model",34,"L"), ("Qty",8,"C"),
    ("List Price",17,"R"), ("20% Disc",17,"R"), ("25% Disc",17,"R"),
    ("30% Disc",17,"R"), ("GP%",11,"R"), ("COGS",17,"R"),
    ("COGS×1.75",18,"R"), ("1.27",17,"R"),
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
shade = False
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

# ─── Save with version tag ────────────────────────────
tag      = datetime.now().strftime("%y%m%d")
existing = glob.glob(str(OUT_DIR/f"SISL_VFD_PL_{tag}_V.*.pdf"))
ver      = max([int(re.search(r"_V\.(\d{2})",f).group(1)) for f in existing], default=4)+1
outfile  = OUT_DIR / f"SISL_VFD_PL_{tag}_V.{ver:02d}.pdf"

pdf.output(str(outfile))
print("✅ Generated:", outfile)
