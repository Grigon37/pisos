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
    python cerca_pisos_fotocasa.py auto                 # les que toquen avui (ús diari)
    SENSE_ESCRIURE=1 python cerca_pisos_fotocasa.py auto  # prova: llegeix però no escriu res

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
    "CountryHouse": "Casa rural", "Penthouse": "Àtic",
}

# ------------------------------------------------------------------ Zones
# Cada entrada: URL base de la 1a pàgina del llistat GENERAL de la zona
# (sense filtres: url_pagina() hi afegeix preu, m2 i habitacions).
#   cada   = cada quants dies s'escombra amb l'opció 'auto' (1 = cada dia):
#            Barcelona i el Barcelonès nord cada dia; la resta cada 2-3 dies
#            segons la distància a Barcelona.
#   barris = la zona de cada pis és el seu barri (el que diu el portal). Es
#            desactiven anuncis d'una ciutat només quan s'han escombrat senceres
#            TOTES les seves zones amb barris=True (p.ex. els 10 districtes de BCN).
ZONES = {
    # --- Barcelona (Fotocasa només permet cercar per districte; la zona de cada pis és el seu barri)
    # --- i Barcelonès nord: cada dia
    "bcn-ciutat-vella": dict(
        ciutat="Barcelona", zona="Ciutat Vella", cada=1, barris=True,
        transport="Metro L1/L3/L4, Rodalies (Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/ciutat-vella/l"),
    "bcn-eixample": dict(
        ciutat="Barcelona", zona="Eixample", cada=1, barris=True,
        transport="Metro L1-L5, Rodalies (Pg. de Gràcia)",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/eixample/l"),
    "bcn-sants-montjuic": dict(
        ciutat="Barcelona", zona="Sants-Montjuïc", cada=1, barris=True,
        transport="Estació de Sants (Rodalies), Metro L1/L3/L5/L9/L10",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/sants-montjuic/l"),
    "bcn-les-corts": dict(
        ciutat="Barcelona", zona="Les Corts", cada=1, barris=True,
        transport="Metro L3/L5/L9 Sud, Tram",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/les-corts/l"),
    "bcn-sarria-sant-gervasi": dict(
        ciutat="Barcelona", zona="Sarrià-Sant Gervasi", cada=1, barris=True,
        transport="FGC S1/S2/L6/L7, Metro L3",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/sarria-sant-gervasi/l"),
    "bcn-gracia": dict(
        ciutat="Barcelona", zona="Gràcia", cada=1, barris=True,
        transport="Metro L3/L4/L5, FGC (Gràcia)",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/gracia/l"),
    "bcn-horta-guinardo": dict(
        ciutat="Barcelona", zona="Horta-Guinardó", cada=1, barris=True,
        transport="Metro L3/L4/L5",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/horta-guinardo/l"),
    "bcn-nou-barris": dict(
        ciutat="Barcelona", zona="Nou Barris", cada=1, barris=True,
        transport="Metro L1/L3/L4/L5/L11",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/nou-barris/l"),
    "bcn-sant-andreu": dict(
        ciutat="Barcelona", zona="Sant Andreu", cada=1, barris=True,
        transport="Metro L1/L9/L10, Rodalies (Sant Andreu, la Sagrera)",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/sant-andreu/l"),
    "bcn-sant-marti": dict(
        ciutat="Barcelona", zona="Sant Martí", cada=1, barris=True,
        transport="Metro L1/L2/L4, Rodalies (el Clot)",
        url="https://www.fotocasa.es/es/comprar/viviendas/barcelona-capital/sant-marti/l"),
    "badalona": dict(
        ciutat="Badalona", zona="Badalona", cada=1, barris=True,
        transport="Metro L2 (Badalona Pompeu Fabra), Rodalies R1",
        url="https://www.fotocasa.es/es/comprar/viviendas/badalona/todas-las-zonas/l"),
    "sant-adria": dict(
        ciutat="Sant Adrià de Besòs", zona="Sant Adrià de Besòs", cada=1, barris=True,
        transport="Rodalies R1, Metro L2, Tram T5/T6",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-adria-de-besos/todas-las-zonas/l"),
    "montgat": dict(
        ciutat="Montgat", zona="Montgat", cada=1, barris=True,
        transport="Rodalies R1 (~20 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/montgat/todas-las-zonas/l"),
    "santa-coloma": dict(
        ciutat="Santa Coloma de Gramenet", zona="Santa Coloma de Gramenet", cada=1, barris=True,
        transport="Metro L1 (Fondo) i L9 Nord",
        url="https://www.fotocasa.es/es/comprar/viviendas/santa-coloma-de-gramenet/todas-las-zonas/l"),
    # --- L'Hospitalet, cada 2 dies
    "bellvitge": dict(
        ciutat="Hospitalet de Llobregat", zona="Bellvitge - El Gornal - Granvia LH", cada=2,
        transport="Metro Bellvitge (L1)",
        url="https://www.fotocasa.es/es/comprar/viviendas/l-hospitalet-de-llobregat/bellvitge-el-gornal-granvia-lh/l"),
    "santa-eulalia": dict(
        ciutat="Hospitalet de Llobregat", zona="Santa Eulàlia", cada=2,
        transport="Metro Santa Eulàlia (L1)",
        url="https://www.fotocasa.es/es/comprar/viviendas/l-hospitalet-de-llobregat/santa-eulalia/l"),
    "collblanc-la-torrassa": dict(
        ciutat="Hospitalet de Llobregat", zona="Collblanc - La Torrassa", cada=2,
        transport="Metro Collblanc (L5/L9) / Torrassa (L1)",
        url="https://www.fotocasa.es/es/comprar/viviendas/l-hospitalet-de-llobregat/collblanc-la-torrassa/l"),
    # --- Municipis propers: cada 2-3 dies segons la distància
    "sant-cugat": dict(
        ciutat="Sant Cugat del Vallès", zona="Sant Cugat del Vallès", cada=2, barris=True,
        transport="FGC Barcelona-Vallès S1/S2 (~20-25 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-cugat-del-valles/todas-las-zonas/l"),
    "rubi": dict(
        ciutat="Rubí", zona="Rubí", cada=3, barris=True,
        transport="FGC Barcelona-Vallès S1 (~30 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/rubi/todas-las-zonas/l"),
    "sabadell": dict(
        ciutat="Sabadell", zona="Sabadell", cada=3, barris=True,
        transport="FGC Barcelona-Vallès S1/S2 (~35-40 min); Rodalies R4",
        url="https://www.fotocasa.es/es/comprar/viviendas/sabadell/todas-las-zonas/l"),
    "terrassa": dict(
        ciutat="Terrassa", zona="Terrassa", cada=3, barris=True,
        transport="FGC Barcelona-Vallès S1 (~45-50 min); Rodalies R4",
        url="https://www.fotocasa.es/es/comprar/viviendas/terrassa/todas-las-zonas/l"),
    "molins-de-rei": dict(
        ciutat="Molins de Rei", zona="Molins de Rei", cada=2, barris=True,
        transport="FGC Llobregat-Anoia (~20 min a Pl. Espanya); Rodalies R4",
        url="https://www.fotocasa.es/es/comprar/viviendas/molins-de-rei/todas-las-zonas/l"),
    "sant-feliu-de-llobregat": dict(
        ciutat="Sant Feliu de Llobregat", zona="Sant Feliu de Llobregat", cada=2, barris=True,
        transport="FGC Llobregat-Anoia (~20 min a Pl. Espanya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-feliu-de-llobregat/todas-las-zonas/l"),
    "sant-joan-despi": dict(
        ciutat="Sant Joan Despí", zona="Sant Joan Despí", cada=2, barris=True,
        transport="FGC Llobregat-Anoia (~15 min); Metro L9/L10 (Can Boixeres)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-joan-despi/todas-las-zonas/l"),
    "cornella-de-llobregat": dict(
        ciutat="Cornellà de Llobregat", zona="Cornellà de Llobregat", cada=2, barris=True,
        transport="Metro L5, FGC Llobregat-Anoia i Rodalies R2 (~15-20 min)",
        url="https://www.fotocasa.es/es/comprar/viviendas/cornella-de-llobregat/todas-las-zonas/l"),
    "esplugues-de-llobregat": dict(
        ciutat="Esplugues de Llobregat", zona="Esplugues de Llobregat", cada=2, barris=True,
        transport="Metro L5 (Can Vidalet); sense FGC/Rodalies directe",
        url="https://www.fotocasa.es/es/comprar/viviendas/esplugues-de-llobregat/todas-las-zonas/l"),
    "martorell": dict(
        ciutat="Martorell", zona="Martorell", cada=3, barris=True,
        transport="FGC Llobregat-Anoia (terminal) i Rodalies R4 (~35-40 min)",
        url="https://www.fotocasa.es/es/comprar/viviendas/martorell/todas-las-zonas/l"),
    "sant-boi-de-llobregat": dict(
        ciutat="Sant Boi de Llobregat", zona="Sant Boi de Llobregat", cada=2, barris=True,
        transport="Rodalies R2 Sud (~20-25 min a Barcelona Sants)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-boi-de-llobregat/todas-las-zonas/l"),
    "sant-vicenc-dels-horts": dict(
        ciutat="Sant Vicenç dels Horts", zona="Sant Vicenç dels Horts", cada=3, barris=True,
        transport="Rodalies R4 (~25-30 min a Pl. Catalunya)",
        url="https://www.fotocasa.es/es/comprar/viviendas/sant-vicenc-dels-horts/todas-las-zonas/l"),
    "castellbisbal": dict(
        ciutat="Castellbisbal", zona="Castellbisbal", cada=3, barris=True,
        transport="FGC Llobregat-Anoia, ramal (~30 min); oferta escassa",
        url="https://www.fotocasa.es/es/comprar/viviendas/castellbisbal/todas-las-zonas/l"),
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ca,es;q=0.9",
}

# ------------------------------------------------------------------ Utils
# Filtres aplicats pel servidor de Fotocasa (comprovat: redueix p.ex. Terrassa de
# ~2.300 a ~1.400 resultats) i ordre per data de publicació (els nous primer).
# parse_targeta() continua aplicant els criteris complets.
FILTRE_URL = f"?maxPrice={PREU_SOSTRE_BAND}&minSurface={M2_MIN}&minRooms={HAB_MIN}&sortType=publicationDate"

def url_pagina(base: str, k: int) -> str:
    """Pàgina 1 = base; pàgina k = base + "/k"; els filtres van al final."""
    return (base if k == 1 else f"{base}/{k}") + FILTRE_URL

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

    zona = (addr.get("neighborhood") or "").strip() if cfg.get("barris") else ""
    return {
        "ciutat": cfg["ciutat"], "zona": zona or cfg["zona"],
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

def dies_retirada(cfg):
    """Dies sense veure un anunci abans de desactivar-lo: com a mínim 10, i
    prou per cobrir 4 passades de la zona (si una passada falla, no passa res)."""
    return max(10, 4 * cfg.get("cada", 1))

def toca_avui(clau):
    """Rotació sense estat: les zones amb el mateix `cada` s'alternen per ordre
    de ZONES (1a, 2a, 3a...), així les grans (Terrassa, Sabadell...) no coincideixen."""
    cada = ZONES[clau].get("cada", 1)
    mateixes = [c for c, z in ZONES.items() if z.get("cada", 1) == cada]
    return (date.today().toordinal() + mateixes.index(clau)) % cada == 0

# ------------------------------------------------------------------ Xarxa / identitat
# Els portals canvien el text de l'URL d'un mateix anunci (el carrer a
# Habitaclia, les característiques a Fotocasa) però no el seu identificador.
ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/d$"   # Habitaclia
    r"|-i(\d+)\.htm$"                                                     # Habitaclia (format antic)
    r"|/(\d{6,})/d$"                                                       # Fotocasa
)

def id_anunci(link):
    """Identificador estable d'un anunci (o el mateix link si no se'n reconeix cap)."""
    m = ID_RE.search(link or "")
    if not m:
        return link
    n, g = next((i, g) for i, g in enumerate(m.groups()) if g)
    return f"{n}:{g}"

def baixa(url, intents=3):
    """GET amb reintents (espera 20 s, 40 s) per a errors temporals: 429, 5xx o xarxa."""
    for i in range(intents):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code != 429 and r.status_code < 500:
                return r
            motiu, err = f"HTTP {r.status_code}", None
        except requests.RequestException as e:
            motiu, err = f"error de xarxa: {e}", e
        if i < intents - 1:
            print(f"  ({motiu}; reintent en {20 * (i + 1)} s)")
            time.sleep(20 * (i + 1))
    if err:
        raise err
    return r

# ------------------------------------------------------------------ Barrido
def barrer_zona(clau, max_pagines=60):
    cfg = ZONES[clau]
    files, links_vistos, firmes = [], set(), set()
    completa = True
    for k in range(1, max_pagines + 1):
        url = url_pagina(cfg["url"], k)
        try:
            r = baixa(url)
        except requests.RequestException as e:
            completa = False
            print(f"::warning::{clau}: pàg {k}, error de xarxa ({e}); avui no es desactiva res d'aquesta zona")
            break
        if r.status_code != 200:
            if k == 1 or r.status_code in (403, 429) or r.status_code >= 500:
                completa = False
                print(f"::warning::{clau}: pàg {k}, HTTP {r.status_code}; avui no es desactiva res d'aquesta zona")
            print(f"  [pàg {k}] HTTP {r.status_code} — parem"); break
        data = treu_json(r.text)
        realEstates, total = (extreu_llistat(data) if data else (None, 0))
        if not realEstates:
            if k == 1:
                completa = False
                print(f"::warning::{clau}: 0 anuncis a la pàg 1 (URL trencada o bloqueig?)")
            print(f"  [pàg {k}] sense anuncis (o JSON no trobat) — fi"); break
        nous = 0
        for item in realEstates:
            # vist = surt al llistat, encara que no passi el filtre (evita falsos "retirats")
            det = (item.get("detail") or {}).get("es-ES") or (item.get("detailWithParams") or {}).get("es-ES")
            if det:
                TOTS_LINKS.add("https://www.fotocasa.es" + det)
            fila = parse_targeta(item, cfg)
            if not fila:
                continue
            if fila["zona"] != cfg["zona"]:
                ZONA_DE[fila["link"]] = fila["zona"]     # barri segons el portal
            FIRMES_VISTES.add((fila["ciutat"], fila["m2"], fila["preu"]))
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
    else:
        completa = False
        print("  Avís: zona truncada pel límit de pàgines; no es desactiva res d'aquesta zona.")
    if completa:
        ZONES_OK.append(clau)
    return files

def firmes_existents():
    """(ciutat, m2, preu) de totes les files ja desades a Supabase -- de
    qualsevol font (Habitaclia, Fotocasa...). Mateixa ciutat + mateixa
    superfície + mateix preu exacte és, a la pràctica, el mateix pis
    anunciat per una altra agència/portal; s'evita duplicar-lo.
    Retorna la llista de files {app, ciutat, zona, m2, preu, link}, o None si no es pot
    llegir. Pagina de 1000 en 1000 (PostgREST no en torna més per petició)."""
    out, off = [], 0
    try:
        while True:
            r = requests.get(REST + f"?select=app,ciutat,zona,m2,preu,link&order=id.asc&limit=1000&offset={off}",
                             headers=SB_HEADERS, timeout=30)
            r.raise_for_status()
            lot = r.json()
            out.extend(lot)
            if len(lot) < 1000:
                return out
            off += 1000
    except requests.RequestException as e:
        print(f"Avís: no s'ha pogut llegir Supabase ({e}).")
        return None

# ------------------------------------------------- Seguiment d'anuncis actius
APP_NOM = "Fotocasa"
TOTS_LINKS = set()   # tots els enllaços vistos en aquesta execució
ZONES_OK = []        # claus de les zones escombrades senceres i sense errors
FIRMES_VISTES = set() # (ciutat, m2, preu) de tots els pisos vàlids vistos, repetits inclosos
ZONA_DE = {}         # link -> barri (segons el portal) dels pisos de zones amb barris=True
SIMULACIO = os.environ.get("SENSE_ESCRIURE") == "1"   # prova: llegeix però no escriu a Supabase

def _rpc(nom, payload):
    if SIMULACIO:
        return 0
    h = {k: v for k, v in SB_HEADERS.items() if k != "Prefer"}
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{nom}", headers=h, json=payload, timeout=60)
        if r.status_code in (200, 201):
            return r.json()
        print(f"Avís: {nom} ha fallat ({r.status_code}): {r.text[:200]} -- s'ha executat migracio_actiu.sql?")
    except requests.RequestException as e:
        print(f"Avís: {nom} ha fallat: {e}")
    return None

def marca_vistos(links):
    """True només si TOTS els lots s'han marcat bé."""
    ok = True
    for i in range(0, len(links), 200):
        if _rpc("marca_vistos", {"p_links": links[i:i + 200]}) is None:
            ok = False
    return ok

def desactiva_antics(ciutat, zona, dies=10):
    n = _rpc("desactiva_antics", {"p_app": APP_NOM, "p_ciutat": ciutat, "p_zona": zona, "p_dies": dies})
    if n:
        print(f"  {ciutat} / {zona}: {n} anuncis desactivats (fa >{dies} dies que no es veuen)")

def desactiva_zones(zones_bd):
    """Zones normals: desactiva per (ciutat, zona). Zones amb barris=True: per
    ciutat, i només si TOTES les seves zones s'han escombrat senceres avui;
    llavors es revisen totes les zones que aquella ciutat té a la BD."""
    fetes = set(ZONES_OK)
    for c in ZONES_OK:
        cfg = ZONES[c]
        if not cfg.get("barris"):
            desactiva_antics(cfg["ciutat"], cfg["zona"], dies_retirada(cfg))
    grups = {}
    for c, cfg in ZONES.items():
        if cfg.get("barris"):
            grups.setdefault(cfg["ciutat"], []).append(c)
    for ciutat, claus in grups.items():
        if not all(c in fetes for c in claus):
            if any(c in fetes for c in claus):
                print(f"  {ciutat}: no totes les zones s'han escombrat senceres; avui no es desactiva res.")
            continue
        dies = max(dies_retirada(ZONES[c]) for c in claus)
        for zona in sorted(zones_bd.get(ciutat, ())):
            desactiva_antics(ciutat, zona, dies)

def reetiqueta(canvis):
    """Posa el barri a pisos ja desats que tenien una zona genèrica o antiga.
    canvis = {link_desat: barri}. Un PATCH per barri (lots de 40 enllaços)."""
    if not canvis:
        return
    if SIMULACIO:
        print(f"(simulació) es posaria el barri a {len(canvis)} pisos ja desats."); return
    per_barri, n = {}, 0
    for link, barri in canvis.items():
        per_barri.setdefault(barri, []).append(link)
    h = {k: v for k, v in SB_HEADERS.items() if k != "Prefer"}
    for barri, links in per_barri.items():
        for i in range(0, len(links), 40):
            lot = links[i:i + 40]
            filtre = "in.(" + ",".join('"' + l.replace('"', '') + '"' for l in lot) + ")"
            try:
                r = requests.patch(REST, params={"link": filtre}, headers=h, json={"zona": barri}, timeout=60)
                if r.status_code in (200, 204):
                    n += len(lot)
                else:
                    print(f"Avís: no s'ha pogut posar el barri ({r.status_code}): {r.text[:200]}")
            except requests.RequestException as e:
                print(f"Avís: no s'ha pogut posar el barri: {e}")
    print(f"Barri actualitzat a {n} pisos ja desats.")

def inserir(files):
    if not files:
        print("Res a inserir."); return True
    if SIMULACIO:
        print(f"(simulació) s'inseririen {len(files)} pisos."); return True
    r = requests.post(REST + "?on_conflict=link", headers=SB_HEADERS, json=files, timeout=60)
    if r.status_code in (200, 201):
        print(f"Inserits/confirmats {len(r.json())} de {len(files)} enviats.")
        return True
    print(f"Error Supabase {r.status_code}: {r.text[:400]}")
    return False

# ------------------------------------------------------------------ Main
def main():
    if len(sys.argv) < 2:
        sys.exit(f"Zones: {', '.join(ZONES)} | o 'totes' | o 'auto' (les que toquen avui)")
    clau = sys.argv[1]
    max_p = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    if clau == "auto":
        claus = [c for c in ZONES if toca_avui(c)]
        print(f"Zones d'avui ({len(claus)} de {len(ZONES)}): {', '.join(claus)}\n")
    elif clau == "totes":
        claus = list(ZONES)
    else:
        claus = [clau]
    total = []
    for c in claus:
        if c not in ZONES:
            print(f"Zona desconeguda: {c}"); continue
        print(f"== {c} ==")
        total += barrer_zona(c, max_p)
    print(f"\nTotal candidats únics (dins d'aquesta execució): {len(total)}")

    existents = firmes_existents()
    if existents is None:
        sys.exit("ERROR: no s'ha pogut llegir Supabase; no s'insereix ni es desactiva res.")
    per_firma, per_id, zones_bd = {}, {}, {}
    for row in existents:
        per_firma.setdefault((row.get("ciutat"), row.get("m2"), row.get("preu")), row.get("link"))
        per_id.setdefault(id_anunci(row.get("link")), []).append((row.get("link"), row.get("zona")))
        if row.get("app") == APP_NOM and row.get("zona"):
            zones_bd.setdefault(row.get("ciutat"), set()).add(row["zona"])
    # Un anunci vist avui amb una URL nova manté viva la fila que ja tenim
    # (amb les seves notes), en lloc de desactivar-la i crear-ne una de nova.
    for l in list(TOTS_LINKS):
        TOTS_LINKS.update(link for link, _ in per_id.get(id_anunci(l), []))
    # El mateix pis anunciat amb un altre enllaç (multi-anunciació, una altra
    # font): la fila desada continua viva encara que el seu enllaç ja no surti.
    for firma in FIRMES_VISTES:
        if firma in per_firma:
            TOTS_LINKS.add(per_firma[firma])
    # Pisos ja desats amb una zona genèrica (la ciutat, el districte) o antiga: posa'ls el barri.
    canvis = {}
    for l, barri in ZONA_DE.items():
        for link, zona in per_id.get(id_anunci(l), []):
            if zona != barri:
                canvis[link] = barri
    sense_duplicar, mateix_id = [], 0
    for f in total:
        if id_anunci(f["link"]) in per_id:
            mateix_id += 1           # ja és a la BD (amb aquesta URL o una d'anterior)
            continue
        ja = per_firma.get((f["ciutat"], f["m2"], f["preu"]))
        if ja:
            TOTS_LINKS.add(ja)       # mateix pis d'una altra font: la fila desada continua viva
        else:
            sense_duplicar.append(f)
    duplicats = len(total) - len(sense_duplicar) - mateix_id
    print(f"Ja eren a la BD: {mateix_id}.")
    if duplicats:
        print(f"Descartats {duplicats} per coincidir (ciutat+m2+preu) amb un pis d'una altra font.")

    if not TOTS_LINKS:
        sys.exit("ERROR: 0 anuncis llegits en totes les zones (bloqueig o canvi de web?).")
    if marca_vistos(sorted(TOTS_LINKS)):
        desactiva_zones(zones_bd)
    else:
        print("Avís: marca_vistos ha fallat; avui no es desactiva res.")
    reetiqueta(canvis)

    if not inserir(sense_duplicar):
        sys.exit(1)

if __name__ == "__main__":
    main()
