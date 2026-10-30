"""Agent Fleet Control Plane（P2）。

组织级包装层：Registry（agents/ YAML 一等公民 ↔ DeerFlow agents_api）+
统一任务入口（Admin API → Dispatcher → 网关 run）+ Inbox（休眠唤醒）+
Scheduler 薄层（YAML cron ↔ 网关 scheduled-tasks API）。

设计依据：deepseek-agent-fleet-mvp-design.md §7/§8/§9、ADR-0004/0008、
spike 0002 结论（单一共享组织作用域 = 单服务身份单存储桶）。

安全红线（AGENTS.md）：审计只追加；args 只存 digest；凭证不进本服务。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

from . import fap_context

PG_DSN = os.environ.get("FLEET_PG_DSN", "postgresql://postgres@127.0.0.1:5432/deerflow")
GATEWAY = os.environ.get("FLEET_GATEWAY_URL", "http://127.0.0.1:8001")
AGENTS_DIR = os.environ.get(
    "FLEET_AGENTS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("fleet.control-plane")

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
    return _pool


SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet_agents (
    name            TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    owner           TEXT NOT NULL DEFAULT '',
    cost_center     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'registered'
                    CHECK (status IN ('registered','running','hibernated','terminated')),
    yaml_sha256     TEXT NOT NULL DEFAULT '',
    config_version  INTEGER NOT NULL DEFAULT 1,
    triggers        JSONB NOT NULL DEFAULT '{}',
    identity        JSONB NOT NULL DEFAULT '{}',
    budget          JSONB NOT NULL DEFAULT '{}',
    synced_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fleet_tasks (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL REFERENCES fleet_agents(name),
    input           TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'web'
                    CHECK (source IN ('web','cron','im','agent','inbox','admin')),
    thread_id       TEXT,
    parent_task_id  BIGINT,
    depth           INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','dispatched','running','success','error','rejected')),
    run_id          TEXT,
    tokens          BIGINT,
    result_preview  TEXT,
    error           TEXT,
    idempotency_key TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at   TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fleet_tasks_agent ON fleet_tasks(agent_name, status);
CREATE TABLE IF NOT EXISTS fleet_inbox (
    id          BIGSERIAL PRIMARY KEY,
    agent_name  TEXT NOT NULL REFERENCES fleet_agents(name),
    payload     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'agent',
    thread_id   TEXT,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','dispatched','acked')),
    task_id     BIGINT REFERENCES fleet_tasks(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fleet_inbox_agent ON fleet_inbox(agent_name, status);
CREATE TABLE IF NOT EXISTS audit_events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_name  TEXT,
    thread_id   TEXT,
    actor       TEXT NOT NULL DEFAULT 'system',
    event_type  TEXT NOT NULL,
    args_digest TEXT NOT NULL DEFAULT '',
    tokens      BIGINT,
    cost_cny    NUMERIC(12,6),
    detail      JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_events(agent_name, ts);
CREATE TABLE IF NOT EXISTS fleet_mcp_servers (
    name        TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'stdio',
    command     TEXT NOT NULL,
    args        JSONB NOT NULL DEFAULT '[]',
    env         JSONB NOT NULL DEFAULT '{}',
    secret_keys JSONB NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 轻量迁移：agent 级外联声明（接入 = agent 能力，平台只强制出口）
ALTER TABLE fleet_agents ADD COLUMN IF NOT EXISTS egress JSONB NOT NULL DEFAULT '[]';
-- ADR-0010 任务池：task（验收单元）加 pool_status；loop/comment 新表
ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS pool_status TEXT NOT NULL DEFAULT 'open'
    CHECK (pool_status IN ('open','review','done'));
ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS accepted_loop_id BIGINT;
-- ADR-0028：Task 保存用户选模与后代策略；Loop 保存不含秘密的实例快照。
ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS model_profile TEXT;
ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS subagent_model_policy TEXT NOT NULL DEFAULT 'auto'
    CHECK (subagent_model_policy IN ('auto','inherit'));
CREATE TABLE IF NOT EXISTS fleet_task_loops (
    id            BIGSERIAL PRIMARY KEY,
    task_id       BIGINT NOT NULL REFERENCES fleet_tasks(id),
    claimant_type TEXT NOT NULL DEFAULT 'agent' CHECK (claimant_type IN ('agent','human')),
    claimant      TEXT NOT NULL,
    thread_id     TEXT,
    status        TEXT NOT NULL DEFAULT 'claimed'
                  CHECK (status IN ('claimed','running','delivered','returned','accepted','archived')),
    deliverable   TEXT,
    tokens        BIGINT,
    version       INTEGER NOT NULL DEFAULT 1,
    error         TEXT,
    resume_comment TEXT,
    created_by    TEXT NOT NULL DEFAULT 'human',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at  TIMESTAMPTZ
);
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS model_profile TEXT;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS model_snapshot JSONB;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS model_selected_by TEXT;
-- ADR-0012：打回意见随 Loop 持久化，成功交付后才清除。
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS resume_comment TEXT;
-- 异常恢复：不改变 Task/Loop 业务状态，只记录可持久化的恢复现场。
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS failure_kind TEXT;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS continuation_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS recovery_action TEXT;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS running_action TEXT;
ALTER TABLE fleet_task_loops ADD COLUMN IF NOT EXISTS last_checkpoint_id TEXT;
CREATE INDEX IF NOT EXISTS idx_loops_task ON fleet_task_loops(task_id, status);
CREATE TABLE IF NOT EXISTS fleet_task_comments (
    id         BIGSERIAL PRIMARY KEY,
    task_id    BIGINT NOT NULL REFERENCES fleet_tasks(id),
    loop_id    BIGINT REFERENCES fleet_task_loops(id),
    author     TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON fleet_task_comments(task_id);
CREATE TABLE IF NOT EXISTS fleet_cron_fires (
    agent_name    TEXT NOT NULL,
    cron_idx      INTEGER NOT NULL,
    last_fired_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (agent_name, cron_idx)
);
-- FAP v1（ADR-0011 H4）：写操作幂等键，24h 去重窗口
CREATE TABLE IF NOT EXISTS fap_idempotency_keys (
    key            TEXT NOT NULL,
    actor          TEXT NOT NULL,
    method         TEXT NOT NULL,
    path           TEXT NOT NULL,
    request_digest TEXT NOT NULL DEFAULT '',
    status_code    INTEGER,
    response_json  JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, actor)
);
ALTER TABLE fap_idempotency_keys ADD COLUMN IF NOT EXISTS request_digest TEXT NOT NULL DEFAULT '';
-- ADR-0012：NULL response 表示执行中占位，完成后回填，5xx/异常删除。
ALTER TABLE fap_idempotency_keys ALTER COLUMN status_code DROP NOT NULL;
ALTER TABLE fap_idempotency_keys ALTER COLUMN response_json DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fap_idem_created ON fap_idempotency_keys(created_at);
-- ADR-0018：系统 Fleet 操作 PAT 的 Vault 密文；原始 token 不落库。
CREATE TABLE IF NOT EXISTS fleet_system_credentials (
    name         TEXT PRIMARY KEY,
    pat_id       TEXT NOT NULL UNIQUE REFERENCES personal_access_tokens(id),
    ciphertext   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def init_db() -> None:
    async with (await pool()).acquire() as conn:
        await conn.execute(SCHEMA)
        # 轻量迁移：2026-10-09 给 fleet_tasks 补 tokens / result_preview / updated_at 列
        await conn.execute("ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS tokens BIGINT")
        await conn.execute("ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS result_preview TEXT")
        await conn.execute("ALTER TABLE fleet_tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        # 池化后 task 的 agent 可能未注册（开放任务/人工）：外键降级为软引用
        await conn.execute("ALTER TABLE fleet_tasks DROP CONSTRAINT IF EXISTS fleet_tasks_agent_name_fkey")
        # 一次性：human 认领过的 task 同步 agent_name='human'（认领语义统一，2026-10-09）
        await conn.execute("""
            UPDATE fleet_tasks SET agent_name='human'
            WHERE agent_name='unassigned' AND EXISTS (
                SELECT 1 FROM fleet_task_loops
                WHERE task_id=fleet_tasks.id AND claimant_type='human')""")
        # ADR-0012：同一认领者对同一 task 终身只有一个 loop。存量重复行不得
        # 由启动迁移静默删除（可能包含 thread、交付或验收历史）。事务化认领
        # 已能阻止新增重复；含历史重复的库先告警并暂缓索引，清理后重启自动补齐。
        duplicates = await conn.fetch("""
            SELECT task_id, claimant_type, claimant, count(*) AS n
            FROM fleet_task_loops
            GROUP BY task_id, claimant_type, claimant
            HAVING count(*) > 1 LIMIT 20""")
        if duplicates:
            summary = ", ".join(
                f"task={r['task_id']} {r['claimant_type']}:{r['claimant']}×{r['n']}"
                for r in duplicates)
            logger.warning(
                "fleet_task_loops 存在历史重复认领，唯一索引暂缓创建；"
                "不会自动删除历史，请备份并人工清理后重启：%s", summary)
        else:
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_fleet_task_loops_claimant
                ON fleet_task_loops(task_id, claimant_type, claimant)""")
    logger.info("schema ready")


async def audit(
    event_type: str,
    *,
    agent_name: str | None = None,
    thread_id: str | None = None,
    actor: str = "system",
    args: Any = None,
    tokens: int | None = None,
    cost_cny: float | None = None,
    detail: dict | None = None,
) -> None:
    # FAP H5：写操作自报 source/actor 透传进 detail（QA-3 方案 a）
    merged = dict(detail or {})
    src = fap_context.fap_source.get()
    act = fap_context.fap_actor.get()
    if src is not None:
        merged.setdefault("source", src)
    if act is not None:
        merged.setdefault("actor", act)
    digest = hashlib.sha256(
        json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32] if args is not None else ""
    async with (await pool()).acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_events
               (agent_name, thread_id, actor, event_type, args_digest, tokens, cost_cny, detail)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            agent_name, thread_id, actor, event_type, digest, tokens, cost_cny,
            json.dumps(merged),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
