#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cerca de pisos a Habitaclia -> taula Supabase `pisos`.

Barreja TOTES les pàgines d'un barri, filtra pels criteris d'Arnau,
elimina duplicats (per enllaç i per multi-anunciació) i insereix a Supabase.

Ús:
    export SUPABASE_URL="https://tvvtiddstqobecmyaunh.supabase.co"
    export SUPABASE_KEY="<service_role_key>"      # clau de servei (recomanada)
    python cerca_pisos_habitaclia.py bellvitge            # un barri
    python cerca_pisos_habitaclia.py bellvitge 11         # limitar pàgines
    python cerca_pisos_habitaclia.py totes                # tots els de sota

Requereix:  pip install requests beautifulsoup4

NOTA: el parseig d'Habitaclia és el punt més fràgil (poden canviar el HTML).
Si la primera passada no extreu bé algun camp, ajusta parse_targeta(); és
exactament el tipus de retoc que Claude Code fa bé iterant sobre el HTML real.
"""

import os, re, sys, time, unicodedata
from datetime import date
import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://tvvtiddstqobecmyaunh.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_KEY:
    sys.exit("Falta la variable d'entorn SUPABASE_KEY (clau service_role).")

REST = f"{SUPABASE_URL}/rest/v1/pisos"
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    # ignora els que ja hi són (índex únic a `link`)
    "Prefer": "resolution=ignore-duplicates,return=representation",
}

# ------------------------------------------------------------------ Criteris
PREU_SOSTRE      = 340000     # sostre nominal
PREU_SOSTRE_BAND = 391000     # sostre ampliat +15% (s'accepta, es marca)
M2_MIN           = 70
HAB_MIN          = 2
BANYS_MIN        = 1
# Exterior imprescindible: balcó / terrassa / àtic (galería sola NO compta: sol ser interior)
EXTERIOR_KW = ["balc", "terrass", "terraz", "atic", "àtic", "ático"]
# Descarta ocupats, llogats amb contracte i lots d'inversor
EXCLOU_KW = [
    "okupa", "ocupado", "ocupada", "sin posesion", "sin posesión",
    "alquilad", "inquilino", "cesion de remate", "cesión de remate",
    "solo inversores", "solo para inversores", "no disponible para entrar",
    "ruina",
]

# ------------------------------------------------------------------ Barris
# Cada entrada: la URL de la 1a pàgina del llistat GENERAL del barri
# (general, no el filtre "terraza", per no perdre els balcons).
ZONES = {
    "bellvitge": dict(
        ciutat="Hospitalet de Llobregat", zona="Bellvitge",
        transport="Metro Bellvitge (L1)",
        url="https://www.habitaclia.com/pisos-bellvitge-hospitalet_de_llobregat.htm"),
    "santa-eulalia": dict(
        ciutat="Hospitalet de Llobregat", zona="Santa Eulàlia",
        transport="Metro Santa Eulàlia (L1)",
        url="https://www.habitaclia.com/pisos-santa_eulalia-hospitalet_de_llobregat.htm"),
    "collblanc": dict(
        ciutat="Hospitalet de Llobregat", zona="Collblanc",
        transport="Metro Collblanc (L5/L9)",
        url="https://www.habitaclia.com/pisos-collblanc-hospitalet_de_llobregat.htm"),
    "la-torrassa": dict(
        ciutat="Hospitalet de Llobregat", zona="La Torrassa",
        transport="Metro Torrassa (L1)",
        url="https://www.habitaclia.com/pisos-la_torrassa-hospitalet_de_llobregat.htm"),
    # Afegeix més barris/municipis copiant el patró (agafa la URL de la 1a pàgina a Habitaclia)
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ca,es;q=0.9",
}

# ------------------------------------------------------------------ Utils
def sense_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()

def a_int(s: str):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None

def url_pagina(base: str, k: int) -> str:
    """Pàgina 1 = base; pàgina k = ...-{k-1}.htm (esquema d'Habitaclia)."""
    if k == 1:
        return base
    return re.sub(r"\.htm$", f"-{k-1}.htm", base)

# ------------------------------------------------------------------ Parseig
FITXA_RE = re.compile(r"/comprar-[^\"']*-i\d+\.htm")

def parse_pagina(html: str):
    """Retorna una llista de dicts crus (un per anunci de la pàgina)."""
    soup = BeautifulSoup(html, "html.parser")
    vistos, targetes = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if not FITXA_RE.search(href):
            continue
        if href in vistos:
            continue
        # puja fins al contenidor que ja porta mètriques i preu
        cont, text = a, ""
        for _ in range(6):
            cont = cont.parent
            if cont is None:
                break
            text = cont.get_text(" ", strip=True)
            if "m2" in text and "€" in text:
                break
        if not cont or "€" not in text:
            continue
        vistos.add(href)
        titol = a.get_text(" ", strip=True)
        targetes.append(dict(href=href, titol=titol, text=text))
    return targetes

def parse_targeta(t, cfg):
    """Converteix una targeta crua en una fila, o None si no compleix."""
    text = t["text"]
    low  = sense_accents(text)

    if any(kw in low for kw in EXCLOU_KW):
        return None

    m2   = a_int((re.search(r"(\d+)\s*m2", text)   or [None, ""])[1] if re.search(r"(\d+)\s*m2", text) else "")
    hab  = a_int((re.search(r"(\d+)\s*habitaci", low) or [None, ""])[1] if re.search(r"(\d+)\s*habitaci", low) else "")
    bany = a_int((re.search(r"(\d+)\s*bano",     low) or [None, ""])[1] if re.search(r"(\d+)\s*bano", low) else "")
    m_eur = re.search(r"([\d.]+)\s*€\s*/\s*m2", text)
    eur_m2 = float(m_eur.group(1).replace(".", "")) if m_eur else None

    # preu = € més gran que NO sigui €/m2
    text_sense_m2 = re.sub(r"[\d.]+\s*€\s*/\s*m2", " ", text)
    imports = [int(x.replace(".", "")) for x in re.findall(r"([\d][\d.]{3,})\s*€", text_sense_m2)]
    preu = max(imports) if imports else None

    if not (m2 and hab and preu):
        return None
    if m2 < M2_MIN or hab < HAB_MIN or preu > PREU_SOSTRE_BAND:
        return None
    if (bany or 1) < BANYS_MIN:
        return None

    # exterior imprescindible
    quins = [kw for kw in EXTERIOR_KW if kw in low]
    if not quins:
        return None
    if any(k in low for k in ("terrass", "terraz")):
        balco = "TERRASSA"
    elif any(k in low for k in ("atic", "àtic", "ático")):
        balco = "TERRASSA/balcó (àtic)"
    else:
        balco = "BALCÓ"

    ascensor  = "Sí" if "ascensor" in low else "No consta"
    calef     = "Sí" if "calefacc" in low else "No consta"
    aire      = "Sí" if ("aire acond" in low or "climatiz" in low or "bomba de calor" in low) else "No consta"
    contras   = "+15% (dins franja)" if preu > PREU_SOSTRE else ""

    return {
        "ciutat": cfg["ciutat"], "zona": cfg["zona"],
        "carrer": t["titol"][:80] or cfg["zona"],
        "transport": cfg["transport"],
        "preu": preu, "m2": m2,
        "eur_m2": round(eur_m2 if eur_m2 else preu / m2, 2),
        "hab": hab, "balco_terrassa": balco, "banys": bany or 1,
        "pros": text[:180],
        "ascensor": ascensor, "calefaccio": calef, "aire_acondicionat": aire,
        "contras": contras,
        "link": "https://www.habitaclia.com" + t["href"] if t["href"].startswith("/") else t["href"],
        "app": "Habitaclia", "agencia": "", "contacte": "",
        "data_cerca": date.today().isoformat(),
    }

# ------------------------------------------------------------------ Barrido
def barrer_zona(clau, max_pagines=15):
    cfg = ZONES[clau]
    files, links_vistos, firmes = [], set(), set()
    for k in range(1, max_pagines + 1):
        url = url_pagina(cfg["url"], k)
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
        except requests.RequestException as e:
            print(f"  [pàg {k}] error de xarxa: {e}"); break
        if r.status_code != 200:
            print(f"  [pàg {k}] HTTP {r.status_code} — parem"); break
        targetes = parse_pagina(r.text)
        if not targetes:
            print(f"  [pàg {k}] sense anuncis — fi"); break
        nous = 0
        for t in targetes:
            fila = parse_targeta(t, cfg)
            if not fila:
                continue
            if fila["link"] in links_vistos:
                continue
            # anti multi-anunciació: mateixa firma (carrer+m2+preu) => salta
            firma = (sense_accents(fila["carrer"])[:30], fila["m2"], fila["preu"])
            if firma in firmes:
                continue
            links_vistos.add(fila["link"]); firmes.add(firma)
            files.append(fila); nous += 1
        print(f"  [pàg {k}] {len(targetes)} anuncis, {nous} nous vàlids")
        time.sleep(1.2)   # educats amb el servidor
    return files

def inserir(files):
    if not files:
        print("Res a inserir."); return
    # on_conflict pel link (índex únic) -> ignora els que ja hi són
    r = requests.post(REST + "?on_conflict=link", headers=SB_HEADERS, json=files, timeout=60)
    if r.status_code in (200, 201):
        print(f"Inserits/confirmats {len(r.json())} de {len(files)} enviats.")
    else:
        print(f"Error Supabase {r.status_code}: {r.text[:400]}")

# ------------------------------------------------------------------ Main
def main():
    if len(sys.argv) < 2:
        sys.exit(f"Zones: {', '.join(ZONES)} | o 'totes'")
    clau = sys.argv[1]
    max_p = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    claus = list(ZONES) if clau == "totes" else [clau]
    total = []
    for c in claus:
        if c not in ZONES:
            print(f"Zona desconeguda: {c}"); continue
        print(f"== {c} ==")
        total += barrer_zona(c, max_p)
    print(f"\nTotal candidats únics: {len(total)}")
    inserir(total)

if __name__ == "__main__":
    main()
