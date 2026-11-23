#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aplica_millores.py -- per executar a l'arrel del repo Grigon37/pisos (Codespaces).

Què fa (és idempotent: si ja s'ha aplicat, no torna a tocar res):
  1. Escriu migracio_actiu.sql (columnes `ultima_vista` i `actiu` + 2 funcions).
  2. Retoca cerca_pisos_habitaclia.py i cerca_pisos_fotocasa.py perquè:
       - marquin com a "vistos avui" tots els anuncis que tornen a trobar,
       - desactivin (actiu = false) els que fa >10 dies que no surten, NOMÉS
         de les zones que s'han escombrat senceres i sense errors.
  3. Retoca index.html perquè el panel mostri els pisos més nous primer,
     amagui els inactius i marqui amb "Nou" els de les últimes 48 h.

Ús:
    python aplica_millores.py            # aplica els canvis
    python aplica_millores.py --check    # només diu què faria

ORDRE: primer executa migracio_actiu.sql a Supabase (SQL Editor) i DESPRÉS
fes commit + push (si el panel es publica abans, demana una columna que
encara no existeix i no carrega).
"""
import re, sys
from pathlib import Path

CHECK = "--check" in sys.argv
ARREL = Path(__file__).resolve().parent

SQL = """\
-- Migració: seguiment d'anuncis actius. Executa-la al SQL Editor de Supabase.
alter table public.pisos
  add column if not exists ultima_vista timestamptz,
  add column if not exists actiu boolean not null default true;

update public.pisos set ultima_vista = coalesce(creat_el, now()) where ultima_vista is null;
alter table public.pisos alter column ultima_vista set default now();
create index if not exists pisos_actiu_creat_idx on public.pisos (actiu, creat_el desc, id desc);

-- Marca com a vistos avui els enllaços que el scraper ha tornat a trobar.
create or replace function public.marca_vistos(p_links text[])
returns integer language sql security definer set search_path = public as $$
  with u as (
    update public.pisos set ultima_vista = now(), actiu = true
    where link = any(p_links) returning 1
  ) select count(*)::int from u;
$$;

-- Desactiva els anuncis d'una app+zona que fa més de p_dies que no es veuen.
-- Mai toca els preferits ni els que no venen de Habitaclia/Fotocasa.
create or replace function public.desactiva_antics(p_app text, p_ciutat text, p_zona text, p_dies int default 10)
returns integer language sql security definer set search_path = public as $$
  with u as (
    update public.pisos set actiu = false
    where app = p_app and ciutat = p_ciutat and zona = p_zona
      and actiu and not coalesce(favorit, false)
      and ultima_vista < now() - make_interval(days => p_dies)
    returning 1
  ) select count(*)::int from u;
$$;

-- Només el scraper (clau service_role) pot cridar-les; el panel (anon) no.
revoke all on function public.marca_vistos(text[]) from public, anon, authenticated;
revoke all on function public.desactiva_antics(text, text, text, int) from public, anon, authenticated;
grant execute on function public.marca_vistos(text[]) to service_role;
grant execute on function public.desactiva_antics(text, text, text, int) to service_role;
"""

HELPERS = '''\
# ------------------------------------------------- Seguiment d'anuncis actius
APP_NOM = "%(app)s"
TOTS_LINKS = set()   # tots els enllaços vistos en aquesta execució
ZONES_OK = []        # (ciutat, zona) escombrades senceres i sense errors

def _rpc(nom, payload):
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
    for i in range(0, len(links), 200):
        _rpc("marca_vistos", {"p_links": links[i:i + 200]})

def desactiva_antics(ciutat, zona, dies=10):
    n = _rpc("desactiva_antics", {"p_app": APP_NOM, "p_ciutat": ciutat, "p_zona": zona, "p_dies": dies})
    if n:
        print(f"  {ciutat} / {zona}: {n} anuncis desactivats (fa >{dies} dies que no es veuen)")

'''

def sub1(text, old, new, etiqueta):
    """Substitueix exactament una vegada; falla fort si l'ancora no hi és."""
    if text.count(old) != 1:
        sys.exit(f"ERROR: no trobo l'àncora única «{etiqueta}» (el fitxer ha canviat?).")
    return text.replace(old, new)

def patch_scraper(path, app):
    t = path.read_text(encoding="utf-8")
    if "def marca_vistos" in t:
        print(f"  {path.name}: ja aplicat"); return None
    t = sub1(t, "def inserir(files):", HELPERS % {"app": app} + "def inserir(files):", "def inserir")
    t = sub1(t, "    files, links_vistos, firmes = [], set(), set()\n",
                "    files, links_vistos, firmes = [], set(), set()\n    completa = True\n", "init barrer_zona")
    t = sub1(t, '            print(f"  [pàg {k}] error de xarxa: {e}"); break',
                '            completa = False\n            print(f"  [pàg {k}] error de xarxa: {e}"); break', "error xarxa")
    t = sub1(t, '            print(f"  [pàg {k}] HTTP {r.status_code} — parem"); break',
                '            if k == 1 or r.status_code in (403, 429) or r.status_code >= 500:\n'
                '                completa = False\n'
                '            print(f"  [pàg {k}] HTTP {r.status_code} — parem"); break', "HTTP")
    t, n = re.subn(r'(\n)(            print\(f"  \[pàg \{k\}\] sense anuncis[^\n]*; break)',
                   r'\1            if k == 1: completa = False\n\2', t)
    if n != 1:
        sys.exit("ERROR: no trobo l'àncora «sense anuncis».")
    t = sub1(t, '            if fila["link"] in links_vistos:\n                continue',
                '            TOTS_LINKS.add(fila["link"])\n            if fila["link"] in links_vistos:\n                continue', "links_vistos")
    t = sub1(t, "    return files\n\ndef firmes_existents",
                "    else:\n        completa = False\n"
                '        print("  Avís: zona truncada pel límit de pàgines; no es desactiva res d\'aquesta zona.")\n'
                '    if completa:\n        ZONES_OK.append((cfg["ciutat"], cfg["zona"]))\n'
                "    return files\n\ndef firmes_existents", "return files")
    t = sub1(t, "    existents = firmes_existents()\n",
                "    marca_vistos(sorted(TOTS_LINKS))\n"
                "    for _ciutat, _zona in ZONES_OK:\n"
                "        desactiva_antics(_ciutat, _zona)\n\n"
                "    existents = firmes_existents()\n", "main")
    return t

def patch_html(path):
    t = path.read_text(encoding="utf-8")
    if "actualitzaNous" in t:
        print(f"  {path.name}: ja aplicat"); return None
    t = sub1(t, 'REST+"?select=*&order=id.asc&limit="+PAGE',
                'REST+"?select=*&actiu=eq.true&order=creat_el.desc,id.desc&limit="+PAGE', "sbSelect")
    t = sub1(t, "let data=[], rowIds=[], ciutatFilter", "let data=[], rowIds=[], nousSet=new Set(), ciutatFilter", "let data")
    t = sub1(t, "async function loadFromDB(){ const rows=await sbSelect(); data=rows.map(fromDB); rowIds=rows.map(function(r){return r.id;}); }",
                "function actualitzaNous(rows){ const lim=Date.now()-48*3600*1000; nousSet=new Set(rows.filter(function(r){return r.creat_el&&new Date(r.creat_el).getTime()>lim;}).map(function(r){return r.id;})); }\n"
                "async function loadFromDB(){ const rows=await sbSelect(); data=rows.map(fromDB); rowIds=rows.map(function(r){return r.id;}); actualitzaNous(rows); }", "loadFromDB")
    t = sub1(t, "data=nd; rowIds=ni; render(); renderStats();", "data=nd; rowIds=ni; actualitzaNous(rows); render(); renderStats();", "silentRefresh")
    t = sub1(t, "    +(bi.label?'<span class=\"pill '+bi.pill+'\">'+esc(bi.label)+'</span>':'')",
                "    +(nousSet.has(rowIds[ri])?'<span class=\"pill yes\">Nou</span>':'')\n"
                "    +(bi.label?'<span class=\"pill '+bi.pill+'\">'+esc(bi.label)+'</span>':'')", "pill Nou")
    return t

def main():
    print("Comprovant fitxers..." + (" (mode --check)" if CHECK else ""))
    canvis = {}
    for nom, app in (("cerca_pisos_habitaclia.py", "Habitaclia"), ("cerca_pisos_fotocasa.py", "Fotocasa")):
        n = patch_scraper(ARREL / nom, app)
        if n is not None: canvis[ARREL / nom] = n
    h = patch_html(ARREL / "index.html")
    if h is not None: canvis[ARREL / "index.html"] = h
    sql_path = ARREL / "migracio_actiu.sql"
    if not sql_path.exists() or sql_path.read_text(encoding="utf-8") != SQL:
        canvis[sql_path] = SQL
    if not canvis:
        print("Tot ja estava aplicat."); return
    for p, c in canvis.items():
        print(("  [check] modificaria " if CHECK else "  escric ") + p.name)
        if not CHECK: p.write_text(c, encoding="utf-8")
    if not CHECK:
        print("\nSegüents passos:\n 1) Copia migracio_actiu.sql al SQL Editor de Supabase i executa'l.\n"
              " 2) git add -A && git commit -m 'Seguiment anuncis actius i panel nous primer' && git push")

if __name__ == "__main__":
    main()
