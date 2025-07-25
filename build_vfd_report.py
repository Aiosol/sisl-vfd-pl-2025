#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v0.7.2
• Uses local ./data folder first; repo/data as fallback
• Accepts either “Model name” **or** “Name” in inventory CSV
• Excludes zero‑Qty rows and FR‑S520SE‑0.2K‑19
• Computes price tiers, GP %, and exports a version‑tagged PDF
"""

import os, re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── Git clone (backup only) ───────────────────────────
GIT_REPO    = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR   = pathlib.Path.cwd() / "repo"
DATA_BACKUP = CLONE_DIR / "data"

def git_sync():
    try:
        if CLONE_DIR.exists():
            subprocess.run(["git", "-C", str(CLONE_DIR), "pull", "--ff-only"],
                           check=True, stdout=subprocess.DEVNULL)
            print("[git] repo updated (backup only)")
        else:
            subprocess.run(["git", "clone", "--depth", "1", GIT_REPO, str(CLONE_DIR)],
                           check=True, stdout=subprocess.DEVNULL)
            print("[git] repo cloned (backup only)")
    except subprocess.CalledProcessError:
        print("[git] clone/pull failed – continuing with local data/")

git_sync()

# ─── Paths ─────────────────────────────────────────────
DATA_DIR = pathlib.Path.cwd() / "data"
OUT_DIR  = pathlib.Path.cwd() / "pdf_reports"
OUT_DIR.mkdir(exist_ok=True)

# ─── Helpers ───────────────────────────────────────────
def money(v):      return f"{float(v):,.2f}" if pd.notna(v) else ""
def series_tag(m): return "H" if "HEL" in m.upper() else re.match(r"FR-([A-Z])", m)[1]
def capacity_val(m): 
    cap = re.search(r"-(?:H)?([\d.]+)K", m)
    return float(cap[1]) if cap else 0.0
def fallback127(model, mp):
    cap = re.search(r"-(?:H)?([\d.]+)K", model)
    if not cap: return None
    cap = cap[1]+"K"
    if "720" in model: return mp.get(f"FR-E820-{cap}-1")
    if "740" in model: return mp.get(f"FR-E840-{cap}-1")

# ─── Locate CSVs ───────────────────────────────────────
def find_csv(token):
    local = list(DATA_DIR.glob(f"*{token}*.csv"))
    if local: return local[0]
    backup = list(DATA_BACKUP.glob(f"*{token}*.csv"))
    return backup[0] if backup else None

inv_csv      = find_csv("LAST")
price127_csv = find_csv("JULY_2025")
list_csv     = find_csv("Final")
if not all((inv_csv, price127_csv, list_csv)):
    sys.exit("❌  Missing CSV(s) in data/")

# ─── Load with flexible header mapping ─────────────────
def load_inventory(fp):
    df = pd.read_csv(fp)
    df.rename(columns=lambda c: c.strip(), inplace=True)
    hdr_map = {
        "name":        "Model name",
        "model":       "Model name",
        "qty":         "Qty owned",
        "qty owned":   "Qty owned",
        "total cost":  "Total cost",
        "totalcost":   "Total cost",
    }
    df.rename(columns={c: hdr_map.get(c.lower(), c) for c in df.columns}, inplace=True)
    need = {"Model name", "Qty owned", "Total cost"}
    if not need.issubset(df.columns):
        raise ValueError(f"Inventory CSV missing columns {need - set(df.columns)}")
    return df

inv   = load_inventory(inv_csv)
p127  = pd.read_csv(price127_csv).rename(columns=str.strip)
lp_df = pd.read_csv(list_csv).rename(columns=str.strip)

# ─── Inventory clean‑up & numeric cols ─────────────────
inv["Model"] = inv["Model name"].astype(str).str.split("||").str[-1].str.strip()
inv = inv[(inv["Qty owned"] > 0) & ~inv["Model"].eq("FR-S520SE-0.2K-19")].copy()
inv["Qty"]        = inv["Qty owned"].astype(int)
inv["TotalCost"]  = inv["Total cost"].astype(str).str.replace(",", "").astype(float)
inv["COGS"]       = inv["TotalCost"] / inv["Qty"]
inv["COGS_x1.75"] = inv["COGS"] * 1.75

# Map 1.27 and List prices
p127_map = dict(zip(p127["Model name"].str.strip(),
                    p127["1.27"].astype(str).str.replace(",", "").astype(float)))
lp_map   = dict(zip(lp_df["Model name"].str.strip(),
                    lp_df["ListPrice"].astype(str).str.replace(",", "").astype(float)))

inv["1.27"]      = inv["Model"].apply(lambda m: p127_map.get(m, fallback127(m, p127_map)))
inv["ListPrice"] = inv["Model"].map(lp_map)

inv["Disc20"] = inv["ListPrice"] * 0.80
inv["Disc25"] = inv["ListPrice"] * 0.75
inv["Disc30"] = inv["ListPrice"] * 0.70
inv["GPpct"]  = (inv["ListPrice"] - inv["COGS"]) / inv["COGS"] * 100

# Sorting
inv["Capacity"]    = inv["Model"].apply(capacity_val)
order = {"D":0,"E":1,"F":2,"A":3,"H":4}
inv["SeriesOrder"] = inv["Model"].apply(series_tag).map(order).fillna(99)
inv.sort_values(["Capacity","SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0, "SL", range(1, len(inv)+1))

# ─── PDF generation ───────────────────────────────────
class StockPDF(FPDF):
    def header(self):
        self.set_font("Arial","B",16)
        self.cell(0,9,"VFD STOCK LIST",0,1,"C")
        self.set_font("Arial","",10)
        self.cell(0,5,datetime.now().strftime("Date: %d %B %Y"),0,1,"C")
        self.cell(0,5,"Smart Industrial Solution Ltd.",0,1,"C")
        self.ln(4)
    def footer(self):
        self.set_y(-12); self.set_font("Arial","I",8)
        self.cell(0,6,f"Page {self.page_no()}",0,0,"C")

cols = [("SL",8,"C"),("Model",34,"L"),("Qty",8,"C"),
        ("List Price",17,"R"),("20% Disc",17,"R"),("25% Disc",17,"R"),
        ("30% Disc",17,"R"),("GP%",11,"R"),("COGS",17,"R"),
        ("COGS ×1.75",18,"R"),("1.27",17,"R")]

pdf = StockPDF("P","mm","A4")
margin_mm = 0.6*25.4
pdf.set_margins(margin_mm,15,margin_mm)
pdf.set_auto_page_break(True,15)
pdf.add_page()

pdf.set_font("Arial","B",7)
for t,w,a in cols: pdf.cell(w,5,t,1,0,a)
pdf.ln()

pdf.set_font("Arial","",7)
shade=False
for _,r in inv.iterrows():
    pdf.set_fill_color(*(242,)*3) if shade else pdf.set_fill_color(255,255,255)
    cells=[r["SL"],r["Model"],r["Qty"],r["ListPrice"],r["Disc20"],r["Disc25"],
           r["Disc30"],f"{r['GPpct']:.2f}%" if pd.notna(r["GPpct"]) else "",
           r["COGS"],r["COGS_x1.75"],r["1.27"]]
    for (__,w,a),v in zip(cols,cells): pdf.cell(w,5,money(v) if isinstance(v,(int,float)) else str(v),1,0,a,shade)
    pdf.ln(); shade=not shade

pdf.set_font("Arial","B",7)
pdf.cell(cols[0][1]+cols[1][1],5,"Total",1,0,"R")
pdf.cell(cols[2][1],5,str(inv["Qty"].sum()),1,0,"C")
pdf.cell(sum(w for _,w,_ in cols[3:]),5,"",1,0)

tag=datetime.now().strftime("%y%m%d")
existing=glob.glob(str(OUT_DIR/f"SISL_VFD_PL_{tag}_V.*.pdf"))
ver=max([int(re.search(r"_V\.(\d{2})",f).group(1)) for f in existing],default=4)+1
outfile=OUT_DIR/f"SISL_VFD_PL_{tag}_V.{ver:02d}.pdf"
pdf.output(str(outfile))
print("✅  Generated:", outfile)
