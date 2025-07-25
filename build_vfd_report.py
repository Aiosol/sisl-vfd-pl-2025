#!/usr/bin/env python3
"""
SISL VFD Stock Report Generator · v1.3
• Reads three CSVs from ./data (repo/data as fallback)
• Robust header matching
• Normalises all model strings (upper‑case, no spaces) before look‑ups
• Outputs version‑tagged PDF into ./pdf_reports
"""

import os, re, glob, subprocess, sys, pathlib
from datetime import datetime
import pandas as pd
from fpdf import FPDF

# ─── Clone backup repo (optional) ──────────────────────
REPO_URL = "https://github.com/Aiosol/sisl-vfd-report.git"
CLONE_DIR = pathlib.Path.cwd() / "repo"
BACKUP = CLONE_DIR / "data"
if not BACKUP.exists():
    try:
        subprocess.run(["git","clone","--depth","1",REPO_URL,str(CLONE_DIR)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[git] offline – skipping clone")

DATA_DIR = pathlib.Path.cwd() / "data"
OUT_DIR  = pathlib.Path.cwd() / "pdf_reports"; OUT_DIR.mkdir(exist_ok=True)

def find_csv(token:str):
    return next(DATA_DIR.glob(f"*{token}*.csv"), None) \
        or next(BACKUP.glob(f"*{token}*.csv"), None)

INV_CSV   = find_csv("LAST")
P127_CSV  = find_csv("JULY_2025")
LIST_CSV  = find_csv("Final")
if not all((INV_CSV, P127_CSV, LIST_CSV)):
    sys.exit("❌  One or more CSVs missing in ./data")

# ─── Helper: fuzzy‑map columns ─────────────────────────
def map_cols(df: pd.DataFrame, spec: dict[str, list]):
    df = df.rename(columns=lambda c: c.strip())
    cols = df.columns.tolist()
    mapping={}
    for need, opts in spec.items():
        for col in cols:
            low=col.lower()
            matched=False
            for opt in opts:
                if isinstance(opt,str) and opt in low:
                    matched=True; break
                if isinstance(opt,list) and all(t in low for t in opt):
                    matched=True; break
            if matched: mapping[col]=need; break
        if need not in mapping.values():
            print("⚠️ header row:", cols)
            raise ValueError(f"Column '{need}' not found.")
    return df.rename(columns=mapping)

# ─── Load all three CSVs ───────────────────────────────
inv   = map_cols(pd.read_csv(INV_CSV), {
         "Model name":[["model","name"], ["material","name"], "name"],
         "Qty owned" :[["qty","owned"], ["quantity"], "qty"],
         "Total cost":[["total","cost"]]
       })
p127  = map_cols(pd.read_csv(P127_CSV), {
         "Model name":[["model","name"], "name"],
         "1.27"      :["1.27"]
       })
plist = map_cols(pd.read_csv(LIST_CSV), {
         "Model name":[["model","name"], "name"],
         "ListPrice" :[["list","price"], "listprice", ["price","list"]]
       })

# ─── Normalise model strings everywhere ───────────────
norm = lambda s: re.sub(r"\s+","", str(s).upper())

inv["Model"]       = inv["Model name"].apply(norm)
p127["Model name"] = p127["Model name"].apply(norm)
plist["Model name"]= plist["Model name"].apply(norm)

# clean inventory + numeric fields
inv = inv[(inv["Qty owned"]>0) & ~inv["Model"].eq(norm("FR-S520SE-0.2K-19"))].copy()
inv["Qty"]        = inv["Qty owned"].astype(int, errors="ignore")
inv["TotalCost"]  = inv["Total cost"].astype(str).str.replace(",","").astype(float)
inv["COGS"]       = inv["TotalCost"]/inv["Qty"]
inv["COGS_x1.75"] = inv["COGS"]*1.75

# ─── Build price look‑ups using normalised keys ───────
p127_map  = dict(zip(
    p127["Model name"],
    p127["1.27"].astype(str).str.replace(",","").astype(float)
))
plist_map = dict(zip(
    plist["Model name"],
    plist["ListPrice"].astype(str).str.replace(",","").astype(float)
))

def fallback127(m):
    capm=re.search(r"-(?:H)?([\d.]+)K",m)
    if not capm: return None
    cap=capm[1]+"K"
    if "720" in m: return p127_map.get(norm(f"FR-E820-{cap}-1"))
    if "740" in m: return p127_map.get(norm(f"FR-E840-{cap}-1"))

inv["1.27"]      = inv["Model"].apply(lambda m: p127_map.get(m) or fallback127(m))
inv["ListPrice"] = inv["Model"].map(plist_map)

# Discounts & GP
inv["Disc20"] = inv["ListPrice"]*0.80
inv["Disc25"] = inv["ListPrice"]*0.75
inv["Disc30"] = inv["ListPrice"]*0.70
inv["GPpct"]  = (inv["ListPrice"]-inv["COGS"])/inv["COGS"]*100

# capacity / series for sorting
cap_val=lambda m: float(re.search(r"-(?:H)?([\d.]+)K",m)[1]) if re.search(r"-(?:H)?([\d.]+)K",m) else 0
series = lambda m: "H" if "HEL" in m else (re.match(r"FR-([A-Z])",m)[1] if re.match(r"FR-([A-Z])",m) else "")
inv["Capacity"]    = inv["Model"].apply(cap_val)
order={"D":0,"E":1,"F":2,"A":3,"H":4}
inv["SeriesOrder"] = inv["Model"].apply(series).map(order).fillna(99)

inv.sort_values(["Capacity","SeriesOrder"], inplace=True, ignore_index=True)
inv.insert(0,"SL",range(1,len(inv)+1))

# ─── PDF ------------------------------------------------
class StockPDF(FPDF):
    def header(self):
        self.set_font("Arial","B",16)
        self.cell(0,9,"VFD STOCK LIST",0,1,"C")
        self.set_font("Arial","",10)
        self.cell(0,5,datetime.now().strftime("Date: %d %B %Y"),0,1,"C")
        self.cell(0,5,"Smart Industrial Solution Ltd.",0,1,"C")
        self.ln(4)
    def footer(self):
        self.set_y(-12)
        self.set_font("Arial","I",8)
        self.cell(0,6,f"Page {self.page_no()}",0,0,"C")

cols=[("SL",8,"C"),("Model",34,"L"),("Qty",8,"C"),
      ("List Price",17,"R"),("20% Disc",17,"R"),("25% Disc",17,"R"),
      ("30% Disc",17,"R"),("GP%",11,"R"),("COGS",17,"R"),
      ("COGS ×1.75",18,"R"),("1.27",17,"R")]

fmt=lambda v: f"{v:,.2f}" if isinstance(v,(int,float)) else str(v)

pdf=StockPDF("P","mm","A4")
pdf.set_margins(0.6*25.4,15,0.6*25.4)
pdf.set_auto_page_break(True,15)
pdf.add_page()

pdf.set_font("Arial","B",7)
for t,w,a in cols: pdf.cell(w,5,t,1,0,a)
pdf.ln()

pdf.set_font("Arial","",7); shade=False
for _,r in inv.iterrows():
    pdf.set_fill_color(*(242,)*3 if shade else (255,255,255))
    row=[r["SL"],r["Model"],r["Qty"],r["ListPrice"],r["Disc20"],r["Disc25"],
         r["Disc30"],f"{r['GPpct']:.2f}%" if pd.notna(r["GPpct"]) else "",
         r["COGS"],r["COGS_x1.75"],r["1.27"]]
    for (_,w,a),v in zip(cols,row):
        pdf.cell(w,5,fmt(v),1,0,a,shade)
    pdf.ln(); shade=not shade

pdf.set_font("Arial","B",7)
pdf.cell(cols[0][1]+cols[1][1],5,"Total",1,0,"R")
pdf.cell(cols[2][1],5,str(inv["Qty"].sum()),1,0,"C")
pdf.cell(sum(w for _,w,_ in cols[3:]),5,"",1,0)

tag=datetime.now().strftime("%y%m%d")
existing=glob.glob(str(OUT_DIR/f"SISL_VFD_PL_{tag}_V.*.pdf"))
ver=max([int(re.search(r"_V\.(\d{2})",f).group(1)) for f in existing], default=4)+1
outfile=OUT_DIR/f"SISL_VFD_PL_{tag}_V.{ver:02d}.pdf"
pdf.output(str(outfile))
print("✅ Generated:", outfile)
