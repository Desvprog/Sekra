-- Schema para sincronização opcional de lembretes e timetracking (Sekra) com
-- Supabase.
--
-- Rode este script no SQL Editor do seu projeto Supabase. Espelha o schema
-- SQLite local (backend/lembretes.py, backend/horas.py) — os mesmos campos,
-- com id como UUID e timestamps de auditoria como timestamptz.
--
-- RLS: esta é uma ferramenta single-user (uso pessoal/local). A forma mais
-- simples é NÃO habilitar RLS na tabela (fica acessível por qualquer
-- portador da anon/service key do projeto) e tratar a própria key como
-- segredo — é assim que o app trata a chave (arquivo 0600, nunca logada).
-- Se preferir defesa em profundidade, habilite RLS e use a service_role key
-- no app (nunca a anon key), ou crie uma policy simples baseada em um
-- claim/coluna de usuário caso o projeto Supabase seja compartilhado.

create table if not exists lembretes (
  id uuid primary key
    check (id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  titulo text not null,
  descricao text not null default '',
  -- text (não timestamptz): valor é wall-clock local naive vindo de <input datetime-local>;
  -- timestamptz reinterpretaria como UTC e deslocaria o horário após round-trip.
  data_hora text,
  reuniao text,
  cliente text,
  concluido boolean not null default false,
  -- Recorrência do lembrete: ''|diaria|semanal|mensal. Dado de negócio,
  -- sincronizado. O estado do agendador de notificações (notificado_nivel/
  -- notificado_em) é local-only e NUNCA é enviado (ver backend/sync.py).
  recorrencia text not null default '',
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  deletado_em timestamptz
);

-- Migração para bancos que já criaram a tabela sem recorrência (rodar uma vez):
-- alter table lembretes add column if not exists recorrencia text not null default '';

-- Índice para consultas por atualizado_em (usado no merge LWW da sincronização).
create index if not exists lembretes_atualizado_em_idx on lembretes (atualizado_em);

-- Opcional (defesa em profundidade — ver comentário acima sobre RLS):
-- alter table lembretes enable row level security;
-- create policy "service role only" on lembretes
--   for all using (auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- Timetracking (backend/horas.py): clientes, projetos, apontamentos.
-- ---------------------------------------------------------------------------

create table if not exists clientes (
  id uuid primary key
    check (id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  nome text not null,
  valor_hora double precision not null default 0,
  ativo boolean not null default true,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  deletado_em timestamptz
);

create index if not exists clientes_atualizado_em_idx on clientes (atualizado_em);

create table if not exists projetos (
  id uuid primary key
    check (id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  -- Nullable (modelo Toggl): projeto pode existir sem cliente vinculado.
  cliente_id text,
  nome text not null,
  ativo boolean not null default true,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  deletado_em timestamptz
);

create index if not exists projetos_atualizado_em_idx on projetos (atualizado_em);

create table if not exists apontamentos (
  id uuid primary key
    check (id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  -- Nullable (modelo Toggl): apontamento pode ter só projeto (cliente vem do
  -- projeto) ou nenhum dos dois (lançamento livre).
  cliente_id text,
  projeto_id text,
  descricao text not null default '',
  -- text (não timestamptz): mesmo motivo de lembretes.data_hora — inicio/fim
  -- são wall-clock local naive, e timestamptz deslocaria o horário no round-trip.
  inicio text not null,
  fim text,
  duracao_s integer,
  origem text not null default 'manual',
  reuniao_ref text,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  deletado_em timestamptz
);

create index if not exists apontamentos_atualizado_em_idx on apontamentos (atualizado_em);

-- Migração manual para bancos que já criaram as tabelas com cliente_id NOT
-- NULL (schema anterior ao modelo Toggl): rodar uma vez.
-- alter table projetos alter column cliente_id drop not null;
-- alter table apontamentos alter column cliente_id drop not null;

-- Opcional (defesa em profundidade — ver comentário acima sobre RLS):
-- alter table clientes enable row level security;
-- alter table projetos enable row level security;
-- alter table apontamentos enable row level security;
-- create policy "service role only" on clientes for all using (auth.role() = 'service_role');
-- create policy "service role only" on projetos for all using (auth.role() = 'service_role');
-- create policy "service role only" on apontamentos for all using (auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- Reuniões (backend/sync.py): PUSH-ONLY, diferente das tabelas acima.
--
-- Reuniões vivem no filesystem local (meta.json por pasta), não no SQLite, e
-- só são enviadas para cá quando o usuário marca a reunião individualmente
-- (`sync_habilitado`=True no meta.json) com a sincronização geral também
-- ativa (opt-in duplo). O app NUNCA lê esta tabela de volta (sem pull, sem
-- merge) — é só um espelho de leitura para quem tiver a chave do projeto.
-- NUNCA contém áudio nem hotwords brutas; `transcricao`/`resumo` só vêm
-- preenchidos se os arquivos correspondentes existirem localmente.
-- ---------------------------------------------------------------------------

create table if not exists reunioes (
  id uuid primary key
    check (id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  titulo text not null default '',
  -- text (AAAA-MM-DD), não date/timestamptz: espelha o nome da pasta no
  -- filesystem local, sem conversão de timezone.
  data text not null,
  cliente text not null default '',
  projeto text not null default '',
  duracao_s integer,
  idioma text,
  transcricao text,
  resumo text,
  atualizado_em timestamptz not null default now()
);

create index if not exists reunioes_atualizado_em_idx on reunioes (atualizado_em);

-- Opcional (defesa em profundidade — ver comentário acima sobre RLS):
-- alter table reunioes enable row level security;
-- create policy "service role only" on reunioes for all using (auth.role() = 'service_role');
