-- Migració: seguiment d'anuncis actius. Executa-la al SQL Editor de Supabase.
alter table public.pisos
  add column if not exists ultima_vista timestamptz,
  add column if not exists actiu boolean not null default true,
  -- "Elimina" al panel marca descartat en lloc d'esborrar: així el scraper
  -- no el torna a inserir l'endemà (l'enllaç continua existint a la taula).
  add column if not exists descartat boolean not null default false;

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

-- ABANS del primer cop que corrin els scrapers nous, pots veure quantes files
-- es desactivarien (les zones han d'haver-se escombrat senceres):
--   select app, ciutat, zona, count(*) from public.pisos
--   where actiu and not favorit and ultima_vista < now() - interval '10 days'
--   group by 1, 2, 3 order by 4 desc;

-- Només el scraper (clau service_role) pot cridar-les; el panel (anon) no.
revoke all on function public.marca_vistos(text[]) from public, anon, authenticated;
revoke all on function public.desactiva_antics(text, text, text, int) from public, anon, authenticated;
grant execute on function public.marca_vistos(text[]) to service_role;
grant execute on function public.desactiva_antics(text, text, text, int) to service_role;
