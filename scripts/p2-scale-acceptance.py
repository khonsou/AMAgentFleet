#!/usr/bin/env python3
"""P2 规模化验收驱动（docs/dev/p2-scale-acceptance.md）。

用法：python3 scripts/p2-scale-acceptance.py [A|B|C|all]
  A  用例 A：10 agent 批量注册生命周期（含派发冒烟/改配置/删除）
  B  用例 B：cron 准时（观察 3 个周期，约 8 分钟）
  C  用例 C：休眠/唤醒全链路
  all 顺序执行 A→B→C

判定全 ✅ 退出码 0；任一 ❌ 退出码 1（已执行部分不自动清理，见文档清理节）。
前置：PG:5432 / 网关:8001 / Console:8002 / control-plane:8003 已启动。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error

CP = "http://127.0.0.1:8003"
GW = "http://127.0.0.1:8001"
PASS, FAIL = "✅", "❌"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name} {detail}", flush=True)


def call(base: str, path: str, body=None, method: str | None = None):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        r = urllib.request.urlopen(req, timeout=30)
        payload = r.read().decode()
        return r.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            payload = json.loads(payload)
        except Exception:
            pass
        return e.code, payload


def agent_yaml(name: str, display: str, soul: str, cron: list | None = None) -> dict:
    doc = {
        "apiVersion": "agentfleet/v1",
        "kind": "Agent",
        "metadata": {"name": name, "displayName": display,
                     "owner": "qa-team", "costCenter": "QA-001"},
        "spec": {
            "format": "deerflow.custom-agent",
            "version": 1,
            "agent": {
                "name": name,
                "display_name": display,
                "description": f"P2 规模化验收测试 agent {name}",
                "model": "deepseek-chat",
                "model_settings": {"temperature": 0.1},
                "skills": None,
                "soul": soul,
                "memory_enabled": True,
            },
        },
        "egress": [],
        "triggers": {"web": True, "im": False, "agentCallable": True,
                     "cron": cron or []},
        "identity": {"mode": "obo", "allowedCallers": ["qa-team"]},
        "budget": {"maxTokensPerRun": 200000, "dailyTokenCap": 2000000},
    }
    return doc


def wait_loop(task_id: int, timeout: int = 300) -> dict | None:
    """轮询 task 详情直到出现 delivered/accepted loop 或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, d = call(CP, f"/api/tasks/{task_id}")
        loops = (d or {}).get("loops") or []
        done = [l for l in loops if l["status"] in ("delivered", "accepted")]
        if done:
            return done[0]
        err = [l for l in loops if l.get("error")]
        if err and all(l["status"] == "claimed" for l in loops):
            time.sleep(5)
            continue
        time.sleep(5)
    return None


# ---------------------------------------------------------------- 用例 A

def case_a() -> None:
    print("== 用例 A：10 agent 批量注册生命周期 ==")
    souls = {i: f"你是 scale-{i:02d}，P2 验收测试 agent。收到任何指令都用一句话中文如实回复，不要调用任何工具。"
             for i in range(1, 11)}

    # A1 批量注册
    codes = []
    for i in range(1, 11):
        name = f"scale-{i:02d}"
        s, _ = call(CP, "/api/agents", {"yaml": agent_yaml(name, f"规模测试{i:02d}", souls[i])})
        codes.append(s)
    check("A1 批量注册 10 个", all(c == 201 for c in codes), f"codes={codes}")

    # A2 Registry 列表
    _, agents = call(CP, "/api/agents")
    mine = {a["name"]: a for a in agents or [] if a["name"].startswith("scale-")}
    ok = (len(mine) >= 10
          and all(a["status"] == "registered" for a in mine.values())
          and all(a["config_version"] == 1 for a in mine.values()))
    check("A2 Registry 状态/版本", ok, f"count={len(mine)}")

    # A3 网关侧抽查
    s, g = call(GW, "/api/agents/scale-05")
    check("A3 网关同步 scale-05", s == 200 and (g or {}).get("name") == "scale-05",
          f"http={s}")

    # A4 派发冒烟（scale-01..03 各一句）
    delivered = 0
    for i in (1, 2, 3):
        name = f"scale-{i:02d}"
        s, r = call(CP, f"/api/agents/{name}/dispatch",
                    {"input": "回复：已就位。", "source": "web", "actor": "qa-scale-test"})
        if s != 202:
            check(f"A4 dispatch {name}", False, f"http={s} {r}")
            continue
        loop = wait_loop(r["task_id"], timeout=240)
        if loop and loop.get("deliverable"):
            delivered += 1
        else:
            check(f"A4 {name} 交付", False, f"task={r['task_id']} loop={loop and loop['status']}")
    check("A4 三个派发冒烟交付", delivered == 3, f"delivered={delivered}/3")

    # A5 改配置
    doc = agent_yaml("scale-04", "规模测试04", souls[4] + "（v2 配置已更新）")
    s, r = call(CP, "/api/agents/scale-04", {"yaml": doc}, method="PUT")
    _, agents = call(CP, "/api/agents")
    v2 = next((a for a in agents or [] if a["name"] == "scale-04"), {})
    check("A5 PUT 改配置版本+1", s == 200 and v2.get("config_version") == 2,
          f"http={s} v={v2.get('config_version')}")

    # A6 删除
    s, _ = call(CP, "/api/agents/scale-06", method="DELETE")
    s1, _ = call(CP, "/api/agents/scale-06")
    s2, _ = call(GW, "/api/agents/scale-06")
    check("A6 DELETE 双端消失", s == 204 and s1 == 404 and s2 == 404,
          f"cp_del={s} cp_get={s1} gw_get={s2}")


# ---------------------------------------------------------------- 用例 B

REPO = "/Users/linan/Documents/Kimi/Workspaces/AMAgentFleet"


def sql(stmt: str) -> None:
    import subprocess
    subprocess.run(
        ["console/backend/.venv/bin/python", "-c",
         "import asyncio,asyncpg,sys\n"
         "async def m():\n"
         "    c=await asyncpg.connect('postgresql://postgres@127.0.0.1:5432/deerflow')\n"
         "    await c.execute(sys.argv[1])\n"
         "    await c.close()\n"
         "asyncio.run(m())", stmt],
        cwd=REPO, check=True)


def marker_tasks(mark: str) -> list[dict]:
    _, tasks = call(CP, "/api/tasks?limit=200")
    return [t for t in tasks or [] if t.get("source") == "cron"
            and mark in (t.get("input") or "")]


def restart_control_plane() -> None:
    import subprocess
    subprocess.run(["bash", "-c",
                    "kill $(lsof -tiTCP:8003 -sTCP:LISTEN) 2>/dev/null; sleep 1; "
                    "cd control-plane && nohup ../console/backend/.venv/bin/python -m uvicorn "
                    "app.main:app --host 127.0.0.1 --port 8003 >> ../console/logs/control-plane.log 2>&1 &"],
                   cwd=REPO)


def case_b() -> None:
    print("== 用例 B：cron 准时（全新 agent / 节奏对账 / 重启续投，约 25 分钟）==")
    NAME = "scale-07b"
    MARK = "cron 报时 v2"

    # B0 清场：上一轮残留（agent + cron 基线行 + open 态遗留任务）
    call(CP, "/api/agents/scale-07", method="DELETE")
    sql("DELETE FROM fleet_cron_fires WHERE agent_name IN ('scale-07','scale-07b')")
    for t in marker_tasks("cron 报时"):
        if t.get("pool_status") == "open":
            call(CP, f"/api/tasks/{t['id']}/close", method="POST")
    check("B0 清场上轮残留", True)

    # B1 首见不补投：全新 agent 第一轮扫描只记基线不投递
    doc = agent_yaml(NAME, "规模测试07b",
                     "你是 cron 应答员：收到 [验收] 任务只回复 pong，不要调用任何工具。",
                     cron=[{"cron": "*/5 * * * *", "prompt": f"[验收] {MARK}：回复 pong"}])
    s, _ = call(CP, "/api/agents", {"yaml": doc})
    check("B1 注册带 cron agent", s in (200, 201), f"http={s}")
    time.sleep(95)
    n0 = len(marker_tasks(MARK))
    check("B1 首见不补投", n0 == 0, f"marker 任务数={n0}")

    # B2 观察窗内恰好 3 次新触发
    fired: list[int] = []
    deadline = time.time() + 60 * 18
    while time.time() < deadline and len(fired) < 3:
        for t in marker_tasks(MARK):
            if t["id"] not in fired:
                fired.append(t["id"])
        time.sleep(20)
    fired.sort()
    check("B2 观察窗内恰好 3 次触发", len(fired) == 3, f"fired={fired}")

    # B3 节奏对账：相邻触发间隔 300s±105s；所属 Agent 认领；交付非空
    from datetime import datetime
    times = []
    for tid in fired:
        _, d = call(CP, f"/api/tasks/{tid}")
        t = (d or {}).get("task") or {}
        loops = (d or {}).get("loops") or []
        times.append(datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")))
        claimed = loops[0]["claimant"] if loops else None
        loop = wait_loop(tid, timeout=300) or {}
        ok = claimed == NAME and bool((loop.get("deliverable") or "").strip())
        check(f"B3 cron 任务 {tid} 所属 Agent 认领+交付非空", ok,
              f"claimant={claimed} loop={loop.get('status')}")
    if len(times) == 3:
        gaps = [(times[i + 1] - times[i]).total_seconds() for i in range(2)]
        ok = all(195 <= g <= 405 for g in gaps)
        check("B3 触发节奏 300s±105s", ok, f"间隔={gaps}")

    # B4 重启续投：重启 control-plane 后 7 分钟内恰好 1 次新触发（不漏不双）
    print("  … B4 重启 control-plane 观察续投 …", flush=True)
    restart_control_plane()
    time.sleep(5)
    s, _ = call(CP, "/api/health")
    check("B4 重启后健康", s == 200, f"http={s}")
    before = set(fired)
    deadline = time.time() + 60 * 7
    while time.time() < deadline:
        new = [t["id"] for t in marker_tasks(MARK) if t["id"] not in before]
        if new:
            fired.extend(new)
            break
        time.sleep(15)
    total = len(marker_tasks(MARK))
    check("B4 重启后恰好续投 1 次", total == len(before) + 1,
          f"重启前 {len(before)} → 总 {total}")

    # B5 摘 cron：PUT 摘除后 200s 无新触发
    doc = agent_yaml(NAME, "规模测试07b", "你是 cron 应答员。")
    s, _ = call(CP, f"/api/agents/{NAME}", {"yaml": doc}, method="PUT")
    n_before = len(marker_tasks(MARK))
    time.sleep(200)
    n_after = len(marker_tasks(MARK))
    check("B5 摘除 cron 后无新触发", s == 200 and n_after == n_before,
          f"http={s} {n_before}→{n_after}")

    # B6 清场
    call(CP, f"/api/agents/{NAME}", method="DELETE")
    sql(f"DELETE FROM fleet_cron_fires WHERE agent_name='{NAME}'")
    for t in marker_tasks(MARK):
        if t.get("pool_status") == "open":
            call(CP, f"/api/tasks/{t['id']}/close", method="POST")
    check("B6 清场完成", True)


# ---------------------------------------------------------------- 用例 C

def case_c() -> None:
    print("== 用例 C：休眠/唤醒全链路 ==")
    # scale-08/09 已由用例 A 注册；缺则补注册
    _, agents = call(CP, "/api/agents")
    have = {a["name"] for a in agents or []}
    for i in (8, 9):
        name = f"scale-{i:02d}"
        if name not in have:
            doc = agent_yaml(name, f"规模测试{i:02d}",
                             f"你是 {name}，P2 验收测试 agent。收到消息用一句话中文回复要点。")
            call(CP, "/api/agents", {"yaml": doc})

    # C1 休眠
    s, _ = call(CP, "/api/agents/scale-08/stop", method="POST")
    _, agents = call(CP, "/api/agents")
    st = next((a["status"] for a in agents or [] if a["name"] == "scale-08"), None)
    check("C1 stop → hibernated", s == 200 and st == "hibernated", f"http={s} status={st}")

    # C2 hibernated dispatch 拒
    s, r = call(CP, "/api/agents/scale-08/dispatch",
                {"input": "不应执行", "source": "web", "actor": "qa-scale-test"})
    check("C2 hibernated dispatch 409", s == 409, f"http={s}")

    # C3 hibernated inbox 拒
    s, r = call(CP, "/api/agents/scale-08/inbox", {"payload": "不应唤醒", "source": "agent"})
    rejected = s >= 400 or (isinstance(r, dict) and (
        "hibernat" in str(r).lower() or "terminat" in str(r).lower()))
    check("C3 hibernated inbox 拒绝/不落 loop", rejected, f"http={s} {str(r)[:120]}")

    # C4 休眠期改配置
    doc = agent_yaml("scale-08", "规模测试08", "你是 scale-08（休眠期 v2 配置）。")
    s, _ = call(CP, "/api/agents/scale-08", {"yaml": doc}, method="PUT")
    _, agents = call(CP, "/api/agents")
    v = next((a for a in agents or [] if a["name"] == "scale-08"), {})
    check("C4 休眠期改配置", s == 200 and v.get("config_version", 0) >= 2,
          f"http={s} v={v.get('config_version')}")

    # C5 拉起恢复
    s, _ = call(CP, "/api/agents/scale-08/start", method="POST")
    s2, r = call(CP, "/api/agents/scale-08/dispatch",
                 {"input": "回复：休眠唤醒链路已恢复。", "source": "web", "actor": "qa-scale-test"})
    loop = wait_loop(r.get("task_id", 0), timeout=240) if s2 == 202 else None
    check("C5 start 恢复 + 新配置生效",
          s == 200 and s2 == 202 and bool(loop and loop.get("deliverable")),
          f"start={s} dispatch={s2} loop={loop and loop['status']}")

    # C6 inbox 攒批（3 条 → 1 task 合并执行）
    for j in range(1, 4):
        call(CP, "/api/agents/scale-09/inbox",
             {"payload": f"[验收消息{j}] 第{j}条：记住数字{j}", "source": "agent"})
    time.sleep(12)  # flush_loop 5s + 入队
    _, tasks = call(CP, "/api/tasks?limit=100")
    inbox_tasks = [t for t in tasks or [] if t.get("source") == "inbox"
                   and t.get("agent_name") == "scale-09"
                   and "验收消息" in (t.get("input") or "")]
    merged = len(inbox_tasks) == 1 and all(f"第{j}条" in inbox_tasks[0]["input"] for j in (1, 2, 3))
    check("C6 3 条 inbox 攒批为 1 task", merged,
          f"tasks={len(inbox_tasks)}")

    # C6/C8 唤醒执行交付
    if inbox_tasks:
        loop = wait_loop(inbox_tasks[0]["id"], timeout=300)
        deliv = (loop or {}).get("deliverable") or ""
        hit = all(f"数字{j}" in deliv or f"第{j}" in deliv for j in (1, 2, 3))
        check("C8 攒批唤醒后交付含全部要点", bool(loop and loop.get("deliverable")) and hit,
              f"loop={loop and loop['status']} 交付[:80]={deliv[:80]}")

    # C7 agent_message A2A 唤醒
    s, r = call(CP, "/api/agents/fleet-cmd-01/dispatch",
                {"input": "【A2A 唤醒测试】请用 agent_message 工具给 scale-09 发一条消息："
                          "『[验收] 来自指挥官的唤醒问候，回复你收到了。』然后直接回复已发送。",
                 "source": "web", "actor": "qa-scale-test"})
    ok = s == 202
    deadline = time.time() + 240
    msg_task = None
    while ok and time.time() < deadline:
        _, tasks = call(CP, "/api/tasks?limit=100")
        cands = [t for t in tasks or [] if t.get("source") == "inbox"
                 and t.get("agent_name") == "scale-09"
                 and "指挥官的唤醒问候" in (t.get("input") or "")]
        if cands:
            msg_task = cands[0]
            break
        time.sleep(10)
    check("C7 agent_message 入池唤醒 scale-09", bool(msg_task),
          f"task={msg_task and msg_task['id']}")
    if msg_task:
        loop = wait_loop(msg_task["id"], timeout=300)
        check("C7 唤醒任务交付", bool(loop and loop.get("deliverable")),
              f"loop={loop and loop['status']}")


# ---------------------------------------------------------------- main

def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("A", "all"):
        case_a()
    if which in ("B", "all"):
        case_b()
    if which in ("C", "all"):
        case_c()
    print("\n== 汇总 ==")
    failed = 0
    for name, ok, detail in results:
        print(f"  {PASS if ok else FAIL} {name} {detail}")
        failed += 0 if ok else 1
    print(f"共 {len(results)} 项，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
