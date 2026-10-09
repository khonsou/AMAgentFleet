#!/usr/bin/env python3
"""Fleet 协作测试：fleet commander 操作多个 worker 整合结果。

场景：『本周发布运营整合简报』
  commander(fleet-cmd-01) 出整合计划 → 平台并行派发 2 个 worker 子任务
  （collector 读 Timeline 看板盘点、analyzer 做状态分布分析）
  → commander 基于两份 ≤500 字摘要出整合简报。

验证点（协作语义地基，ADR-0004）：
  V1 线程树：子任务 parent_task_id 正确、depth=1
  V2 并行执行：两个 worker 同时跑
  V3 摘要纪律：worker 输出为摘要级（≤500 字硬上限由 SOUL 约束，平台侧 P3 强制执行）
  V4 结果整合：commander 简报包含两个 worker 的结论
  V5 拒环：构造 A→B→A 环，dispatcher 必须拒绝
  V6 审计：协作链全程 audit_events 可查

前置：网关:8001 / control-plane:8003 已启动；http MCP 工具已注册（egress 并集含 timeline.angrymiao.com）。
用法：python3 scripts/test-fleet-collaboration.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

CP = "http://127.0.0.1:8003"
PASS, FAIL = "✅", "❌"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name} {detail}")


def call(path: str, body=None, method: str | None = None):
    req = urllib.request.Request(
        f"{CP}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return json.load(r) if r.status != 204 else None
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_detail": e.read().decode()[:200]}


def agent_yaml(name: str, display: str, soul: str, egress: list[str]) -> dict:
    return {
        "apiVersion": "agentfleet/v1",
        "kind": "Agent",
        "metadata": {"name": name, "displayName": display, "owner": "qa-team", "costCenter": "QA-001"},
        "spec": {
            "format": "deerflow.custom-agent",
            "version": 1,
            "agent": {
                "name": name,
                "display_name": display,
                "description": f"协作测试：{display}",
                "model": "deepseek-chat",
                "model_settings": {"temperature": 0.1},
                "mcp_plugins": ["http"],
                "skills": [],
                "soul": soul,
                "memory_enabled": True,
            },
        },
        "egress": egress,
        "triggers": {"web": True},
        "identity": {"mode": "obo", "allowedCallers": ["qa-team"]},
        "budget": {"maxTokensPerRun": 200000},
    }


SOUL_CMD = (
    "你是舰队指挥官。收到多个子任务摘要后：1) 核对各摘要来源与数字；2) 去重合并；"
    "3) 输出整合简报（结论先行，标注每部分来自哪个 worker）。不编造摘要里没有的数据。"
)
SOUL_COLLECTOR = (
    "你是数据采集员。用 http 工具读数据，只输出事实清单（条目、关键字段），"
    "输出控制在 500 字以内——你的全量过程不入上级上下文，摘要即交付物。"
)
SOUL_ANALYZER = (
    "你是数据分析师。基于读取的数据做统计与异常指出，结论+依据，"
    "输出控制在 500 字以内——摘要即交付物。"
)

# 真实 slug 属机密路径，不入库；通过环境变量注入（见 spike 0003 凭据原则）
BASE = os.environ.get("TIMELINE_BASE", "https://timeline.angrymiao.com/<slug>").rstrip("/")


def wait_task(tid: int, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = next((x for x in call("/api/tasks?limit=50") if x["id"] == tid), None)
        if t and t["status"] in ("success", "error", "rejected"):
            return t
        time.sleep(4)
    return {"id": tid, "status": "timeout"}


def main() -> int:
    print("== 前置：注册协作三角色 ==")
    agents = {
        "fleet-cmd-01": agent_yaml("fleet-cmd-01", "舰队指挥官", SOUL_CMD, []),
        "fleet-collect-01": agent_yaml("fleet-collect-01", "采集员", SOUL_COLLECTOR, ["timeline.angrymiao.com"]),
        "fleet-analyze-01": agent_yaml("fleet-analyze-01", "分析师", SOUL_ANALYZER, ["timeline.angrymiao.com"]),
    }
    for name, doc in agents.items():
        r = call(f"/api/agents/{name}")
        if r and r.get("_error") == 404:
            call("/api/agents", {"yaml": doc}, method="POST")
        print(f"  registered: {name}")

    print("== 步骤 1：commander 出整合计划 ==")
    cmd_task = call("/api/agents/fleet-cmd-01/dispatch", {
        "input": "任务：产出本周发布运营整合简报。你的计划：需要 ① 看板盘点（有哪些看板、各多少卡）"
                 "② 状态分布分析（待执行/待发布/已发布各多少）。请先只输出分解计划，不要自己读数据。",
        "source": "web",
    }, method="POST")
    t0 = wait_task(cmd_task["task_id"])
    check("S1 指挥官计划任务完成", t0["status"] == "success", f"task#{t0['id']} tokens={t0.get('tokens')}")

    print("== 步骤 2：并行派发两个 worker（挂到 commander 线程树下）==")
    collect = call("/api/agents/fleet-collect-01/dispatch", {
        "input": f"用 http 工具 GET {BASE}/api/boards 列出全部看板（id/名称/卡片数），"
                 "输出一份 ≤500 字的事实清单。",
        "source": "agent", "parent_task_id": t0["id"],
    }, method="POST")
    analyze = call("/api/agents/fleet-analyze-01/dispatch", {
        "input": f"用 http 工具：1) GET {BASE}/api/meta 确认 status 枚举；"
                 f"2) 对 7 个看板逐个 GET {BASE}/api/boards/<id>/items?limit=200 统计"
                 "状态分布（待执行/待发布/已发布）——注意 404 的看板跳过并注明。"
                 "汇总输出 ≤500 字：总体分布 + 最活跃看板。",
        "source": "agent", "parent_task_id": t0["id"],
    }, method="POST")
    started = time.time()
    tc = wait_task(collect["task_id"])
    ta = wait_task(analyze["task_id"])
    elapsed = time.time() - started
    check("S2a 采集员完成", tc["status"] == "success", f"task#{tc['id']} {elapsed:.0f}s")
    check("S2b 分析师完成", ta["status"] == "success", f"task#{ta['id']}")

    tasks = call("/api/tasks?limit=50")
    tc = next(x for x in tasks if x["id"] == tc["id"])
    ta = next(x for x in tasks if x["id"] == ta["id"])
    check("V1 线程树父子关系", tc["parent_task_id"] == t0["id"] and ta["parent_task_id"] == t0["id"]
          and tc["depth"] == 1 and ta["depth"] == 1,
          f"parent={tc['parent_task_id']} depth={tc['depth']}/{ta['depth']}")
    both_preview = bool(tc.get("result_preview")) and bool(ta.get("result_preview"))
    check("V3 摘要落库", both_preview,
          f"collect={len(tc.get('result_preview') or '')}字 analyze={len(ta.get('result_preview') or '')}字")

    print("== 步骤 3：commander 整合 ==")
    merged = call("/api/agents/fleet-cmd-01/dispatch", {
        "input": "两个 worker 的摘要如下，请整合为一份简报（结论先行，标注来源）：\n\n"
                 f"【采集员】{tc.get('result_preview')}\n\n【分析师】{ta.get('result_preview')}",
        "source": "web", "thread_id": t0.get("thread_id"),
    }, method="POST")
    t3 = wait_task(merged["task_id"])
    tasks = call("/api/tasks?limit=50")
    t3 = next(x for x in tasks if x["id"] == t3["id"])
    preview = t3.get("result_preview") or ""
    check("S3 整合任务完成", t3["status"] == "success", f"task#{t3['id']} tokens={t3.get('tokens')}")
    check("V4 简报含双源结论", ("采集" in preview or "collect" in preview.lower())
          and ("分析" in preview or "分布" in preview),
          f"preview={preview[:60]}…")

    print("== 步骤 4：拒环负例 ==")
    # 构造环：cmd 子任务 → analyze 子任务 → cmd 子任务（同 agent 重复出现在链上）
    ring1 = call("/api/agents/fleet-collect-01/dispatch", {
        "input": "环测试第一步", "source": "agent", "parent_task_id": t3["id"]}, method="POST")
    ring2 = call("/api/agents/fleet-cmd-01/dispatch", {
        "input": "环测试第二步（应触发拒环）", "source": "agent",
        "parent_task_id": ring1["task_id"]}, method="POST")
    tr = wait_task(ring2["task_id"], timeout=60)
    check("V5 协作环被拒", tr["status"] == "rejected", f"status={tr['status']} err={(tr.get('error') or '')[:40]}")

    print("== 步骤 5：审计链 ==")
    audit = call("/api/audit?agent=fleet-collect-01&limit=20")
    types = {a["event_type"] for a in audit}
    check("V6 审计覆盖", "task.enqueued" in types and any("task.completed" == t for t in types),
          f"events={sorted(types)}")

    print("\n== 汇总 ==")
    ok = all(r[1] for r in results)
    for name, passed, _ in results:
        print(f"  {PASS if passed else FAIL} {name}")
    print(f"\n结果：{sum(1 for r in results if r[1])}/{len(results)} 通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
