#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cerca de pisos a Fotocasa -> taula Supabase `pisos`.

A diferència d'Habitaclia, Fotocasa incrusta a cada pàgina de resultats un
bloc JSON (<script id="__initial_props__">) amb les dades netes i
estructurades de cada anunci (preu exacte, m2, habitacions, banys, adreça,
si està ocupat/llogat/subhastat...). Això fa que aquest script sigui molt
més robust que el d'Habitaclia: no cal "llegir" text renderitzat.

Ús:
    export SUPABASE_URL="https://tvvtiddstqobecmyaunh.supabase.co"
    export SUPABASE_KEY="<service_role_key>"      # clau de servei (recomanada)
    python cerca_pisos_fotocasa.py bellvitge            # una zona
    python cerca_pisos_fotocasa.py bellvitge 11         # limitar pàgines
    python cerca_pisos_fotocasa.py totes                # totes les de sota

Requereix:  pip install requests beautifulsoup4

NOTA: si Fotocasa canvia l'estructura del JSON (__initial_props__ ->
initialSearch -> result -> realEstates), cal ajustar extreure_fila(); és
exactament el tipus de retoc que Claude Code fa bé iterant amb el JSON real.
"""

import json, os, re, sys, time
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
# (han de coincidir amb els de cerca_pisos_habitaclia.py)
PREU_SOSTRE      = 340000
PREU_SOSTRE_BAND = 391000
M2_MIN           = 70
HAB_MIN          = 2
BANYS_MIN        = 1
PREU_MIN_REALISTA = 20000  # salvaguarda (vegeu cerca_pisos_habitaclia.py)

TIPUS_CA = {
    "Flat": "Pis", "Duplex": "Dúplex", "Attic": "Àtic",
    "GroundFloorWithGarden": "Planta baixa", "SemidetachedHouse": "Casa aparellada",
    "House_Chalet": "Casa", "Loft": "Loft", "Studio": "Estudi",
}

# ------------------------------------------------------------------ Zones
# Cada entrada: la URL de la 1a pàgina del llistat GENERAL de la zona.
# A diferència d'Habitaclia, a Fotocasa els barris petits d'un mateix
# districte (p.ex. Sants, Poble-sec, Hostafrancs...) no tenen cerca pròpia:
# només es pot cercar pel districte sencer (Sants-Montjuïc). Per això aquí
# hi ha menys entrades de Barcelona ciutat que a l'script d'Habitaclia.
ZONES = {
    "bellvitge": dict(
        ciutat="Hospitalet de Llobregat", zona="Bellvitge - El Gornal - Granvia LH",
        transport="Metro Bellvitge (L1)",
        url="https://www.fotocasa.es/es/comprar/viviendas/l-hospitalet-de-llobregat/bellvitge-el-gornal-granvia-lh/l"),
    "santa-eulalia": dict(
        ciutat="Hospitalet de Llobregat", zona="Santa Eulàlia",
        transport="Metro Santa Eulàlia (L1)",
        url="https://www.fotocasa.es/es/comprar/viviendas/l-hospitalet-de-llobregat/santa-eulalia/l"),
    "collblanc-la-torrassa": dict(
        ciutat="Hospitalet de Llobregat", zona="Collblanc - La Torrassa",
        transport="Metro Collblanc (L5/L9) / Torrassa (L1)",
        url="https://www.fotocasa.es/es/comprar/viviendas/l-hospitalet-de-llobregat/collblanc-la-torrassa/l"),
    # --- Barcelona: Fotocasa només permet cercar pel districte sencer ---
    "sants-montjuic": dict(
        ciutat="Barcelona", zona="Sants-Montjuïc",
        transport="Metro L1/L2/L3/L5/L9/L10, Estació de Sants (Rodalies)",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/sants-montjuic/l"),
    # --- Municipis propers amb bona connexió FGC/Rodalies ---
    "sant-cugat": dict(
        ciutat="Sant Cugat del Vallès", zona="Sant Cugat del Vallès",
        transport="FGC Barcelona-Vallès S1/S2 (~20-25 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-cugat-del-valles/todas-las-zonas/l"),
    "rubi": dict(
        ciutat="Rubí", zona="Rubí",
        transport="FGC Barcelona-Vallès S1 (~30 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/rubi/todas-las-zonas/l"),
    "sabadell": dict(
        ciutat="Sabadell", zona="Sabadell",
        transport="FGC Barcelona-Vallès S1/S2 (~35-40 min); Rodalies R4",
        url="https://www.fotocasa.es/es/comprar/viviendas/sabadell/todas-las-zonas/l"),
    "terrassa": dict(
        ciutat="Terrassa", zona="Terrassa",
        transport="FGC Barcelona-Vallès S1 (~45-50 min); Rodalies R4",
        url="https://www.fotocasa.es/es/comprar/viviendas/terrassa/todas-las-zonas/l"),
    "molins-de-rei": dict(
        ciutat="Molins de Rei", zona="Molins de Rei",
        transport="FGC Llobregat-Anoia (~20 min a Pl. Espanya); Rodalies R4",
        url="https://www.fotocasa.es/es/comprar/viviendas/molins-de-rei/todas-las-zonas/l"),
    "sant-feliu-de-llobregat": dict(
        ciutat="Sant Feliu de Llobregat", zona="Sant Feliu de Llobregat",
        transport="FGC Llobregat-Anoia (~20 min a Pl. Espanya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-feliu-de-llobregat/todas-las-zonas/l"),
    "sant-joan-despi": dict(
        ciutat="Sant Joan Despí", zona="Sant Joan Despí",
        transport="FGC Llobregat-Anoia (~15 min); Metro L9/L10 (Can Boixeres)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-joan-despi/todas-las-zonas/l"),
    "cornella-de-llobregat": dict(
        ciutat="Cornellà de Llobregat", zona="Cornellà de Llobregat",
        transport="Metro L5, FGC Llobregat-Anoia i Rodalies R2 (~15-20 min)",
        url="https://www.fotocasa.es/es/comprar/viviendas/cornella-de-llobregat/todas-las-zonas/l"),
    "esplugues-de-llobregat": dict(
        ciutat="Esplugues de Llobregat", zona="Esplugues de Llobregat",
        transport="Metro L5 (Can Vidalet); sense FGC/Rodalies directe",
        url="https://www.fotocasa.es/es/comprar/viviendas/esplugues-de-llobregat/todas-las-zonas/l"),
    "martorell": dict(
        ciutat="Martorell", zona="Martorell",
        transport="FGC Llobregat-Anoia (terminal) i Rodalies R4 (~35-40 min)",
        url="https://www.fotocasa.es/es/comprar/viviendas/martorell/todas-las-zonas/l"),
    "sant-boi-de-llobregat": dict(
        ciutat="Sant Boi de Llobregat", zona="Sant Boi de Llobregat",
        transport="Rodalies R2 Sud (~20-25 min a Barcelona Sants)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-boi-de-llobregat/todas-las-zonas/l"),
    "sant-vicenc-dels-horts": dict(
        ciutat="Sant Vicenç dels Horts", zona="Sant Vicenç dels Horts",
        transport="Rodalies R4 (~25-30 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-vicenc-dels-horts/todas-las-zonas/l"),
    "castellbisbal": dict(
        ciutat="Castellbisbal", zona="Castellbisbal",
        transport="FGC Llobregat-Anoia, ramal (~30 min); oferta escassa",
        url="https://www.fotocasa.es/es/comprar/viviendas/castellbisbal/todas-las-zonas/l"),
    # Afegeix més municipis copiant el patró (agafa la URL de la 1a pàgina a Fotocasa)
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ca,es;q=0.9",
}

# ------------------------------------------------------------------ Utils
def url_pagina(base: str, k: int) -> str:
    """Pàgina 1 = base; pàgina k = base + "/k"."""
    return base if k == 1 else f"{base}/{k}"

def treu_json(html: str):
    """Extreu i parseja el bloc <script id="__initial_props__"> de la pàgina."""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("script", id="__initial_props__")
    if not node or not node.string:
        return None
    try:
        return json.loads(node.string)
    except json.JSONDecodeError:
        return None

def extreu_llistat(data: dict):
    """Retorna (realEstates, count_total) o (None, 0) si l'estructura no hi és."""
    try:
        result = data["initialSearch"]["result"]
        return result.get("realEstates") or [], result.get("count", 0)
    except (KeyError, TypeError):
        return None, 0

# ------------------------------------------------------------------ Parseig
def parse_targeta(item: dict, cfg: dict):
    """Converteix un objecte realEstate cru en una fila, o None si no compleix."""
    if item.get("isOccupied") or item.get("isRentedWithTenants") or item.get("isAuctioned"):
        return None

    fk = {f["key"]: f["value"] for f in (item.get("features") or []) if f.get("key")}
    m2   = fk.get("surface")
    hab  = fk.get("rooms")
    bany = fk.get("bathrooms")
    preu = item.get("rawPrice")

    if not (m2 and hab and preu):
        return None
    if preu < PREU_MIN_REALISTA:
        return None
    if m2 < M2_MIN or hab < HAB_MIN or preu > PREU_SOSTRE_BAND:
        return None
    if (bany or 1) < BANYS_MIN:
        return None

    subtype = item.get("buildingSubtype") or ""
    te_terrassa = "terrace" in fk or "yard" in fk or "private_garden" in fk
    te_balco = "balcony" in fk
    es_atic = subtype == "Attic"
    if not (te_terrassa or te_balco or es_atic):
        return None  # exterior imprescindible
    balco_terrassa = "TERRASSA" if te_terrassa else ("TERRASSA/balcó (àtic)" if es_atic else "BALCÓ")

    addr = item.get("address") or {}
    zona_fina = addr.get("district") or addr.get("neighborhood") or cfg["zona"]
    tipus = TIPUS_CA.get(subtype, subtype or "Pis")
    desc = (item.get("description") or "").strip().replace("\n", " ")
    carrer = f"{tipus} a {zona_fina.strip()}" + (f" — {desc[:60]}" if desc else "")

    detail = (item.get("detail") or {}).get("es-ES") or (item.get("detailWithParams") or {}).get("es-ES")
    if not detail:
        return None
    link = "https://www.fotocasa.es" + detail

    return {
        "ciutat": cfg["ciutat"], "zona": cfg["zona"],
        "carrer": carrer[:80],
        "transport": cfg["transport"],
        "preu": preu, "m2": m2,
        "eur_m2": round(preu / m2, 2),
        "hab": hab, "balco_terrassa": balco_terrassa, "banys": bany or 1,
        "pros": desc[:180],
        "ascensor": "Sí" if "elevator" in fk else "No consta",
        "calefaccio": "Sí" if "heating" in fk else "No consta",
        "aire_acondicionat": "Sí" if "air_conditioner" in fk else "No consta",
        "contras": "+15% (dins franja)" if preu > PREU_SOSTRE else "",
        "link": link,
        "app": "Fotocasa",
        "agencia": item.get("clientAlias") or "",
        "contacte": item.get("phone") or "",
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
        data = treu_json(r.text)
        realEstates, total = (extreu_llistat(data) if data else (None, 0))
        if not realEstates:
            print(f"  [pàg {k}] sense anuncis (o JSON no trobat) — fi"); break
        nous = 0
        for item in realEstates:
            fila = parse_targeta(item, cfg)
            if not fila:
                continue
            if fila["link"] in links_vistos:
                continue
            firma = (fila["carrer"][:30], fila["m2"], fila["preu"])
            if firma in firmes:
                continue
            links_vistos.add(fila["link"]); firmes.add(firma)
            files.append(fila); nous += 1
        print(f"  [pàg {k}] {len(realEstates)} anuncis, {nous} nous vàlids (total zona: {total})")
        if k * 30 >= total:
            break
        time.sleep(1.2)   # educats amb el servidor
    return files

def inserir(files):
    if not files:
        print("Res a inserir."); return
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
