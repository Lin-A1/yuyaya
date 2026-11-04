"""后台 agent 编排 + 前台自主触发的自检（用假 CLI 后端，不花 token）。

由 tools/selftest.py 调用：agents_suite(client, tmp, check)。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

FAKE_AGENT = r'''
import sys, time, pathlib
p = sys.stdin.read()
task = p.split("任务：", 1)[1].strip().splitlines()[0] if "任务：" in p else p
if "SLOW" in task: time.sleep(30)
if "SHORT" in task: time.sleep(1.2)
if "ASK" in task and "用户已经回答" not in p:
    print("NEED_INPUT: 存哪个盘？"); sys.exit(0)
if "FLAKY" in task:
    f = pathlib.Path(sys.argv[1])
    if not f.exists():
        f.write_text("1"); print("我无法完成"); sys.exit(0)
if "FAIL" in task:
    print("boom"); sys.exit(3)
pre = ""
if "前置任务的结果" in p:
    pre = " | 前置:" + p.split("前置任务的结果", 1)[1][:60].replace("\n", " ")
print("完成:" + task[:30] + pre)
'''


def _wait(fn, timeout=15.0, step=0.1):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True).stdout
        return str(pid) in out.decode("gbk", "ignore")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class FakeSpeaker:
    def __init__(self):
        self.is_idle, self.said, self.session = True, [], True

    def has_session(self): return self.session
    def idle(self): return self.is_idle
    def speak(self, text): self.said.append(text); return True


def agents_suite(client, tmp: pathlib.Path, check) -> None:
    from server.agents import HIGH, Initiative, Intent, Orchestrator
    from server.io import EventBus
    from server.realtime.tools import TOOL_SCHEMAS, execute

    fake = tmp / "fake_agent.py"
    fake.write_text(FAKE_AGENT, encoding="utf-8")
    flag = tmp / "flaky.flag"
    cmd = [sys.executable, str(fake), str(flag)]
    conf = {"agents": {"default": "a", "max_concurrent": 2, "max_refine": 1,
                       "routing": {"code": "b"},
                       "backends": {"a": {"cmd": cmd, "timeout": 20},
                                    "b": {"cmd": cmd, "timeout": 20},
                                    "ghost": {"cmd": ["no-such-agent-cli-xyz"]}}}}
    bus = EventBus()
    seen: list = []
    bus.subscribe(lambda e: seen.append(e) if e.get("type") == "job" else None)
    orch = Orchestrator(bus, conf, pathlib.Path("."), tmp / "jobs.json")
    done_cb: list = []
    orch.on_event(lambda j: done_cb.append((j.id, j.status)))

    # 1) 异步：立即返回
    t0 = time.time()
    r = orch.start("SHORT 写个文件")
    check("agent_start 立即返回任务号", bool(r.get("job_id")) and time.time() - t0 < 0.5, str(r))
    j1 = orch.get(r["job_id"])
    check("后台任务完成并带结果", bool(_wait(lambda: j1.status == "done")) and "完成:" in j1.result,
          f"{j1.status} {j1.result[:60]}")

    # 2) 路由与回退
    r = orch.start("路由测试", kind="code")
    check("按 kind 路由到后端 b", r.get("backend") == "b", str(r))
    r2 = orch.start("点名不可用后端", backend="ghost")
    check("后端不可用时回退并说明", r2.get("backend") == "a" and "ghost" in (r2.get("note") or ""), str(r2))
    _wait(lambda: all(j.status not in ("queued", "running") for j in orch.store.jobs.values()), 20)

    # 3) 并发与排队
    ids = [orch.start(f"SHORT 并发{i}", backend="a")["job_id"] for i in range(3)]
    time.sleep(0.5)
    states = [orch.get(i).status for i in ids]
    check("同后端同时只跑一个，其余排队", states.count("running") == 1 and states.count("queued") == 2, str(states))
    check("三个任务最终都完成", bool(_wait(lambda: all(orch.get(i).status == "done" for i in ids), 25)),
          str([orch.get(i).status for i in ids]))

    # 4) 取消
    jc = orch.get(orch.start("SLOW 很慢的任务", backend="a")["job_id"])
    _wait(lambda: jc.status == "running")
    _wait(lambda: jc.handle is not None)
    pid = jc.handle.pid if jc.handle else None
    orch.cancel(jc.id)
    check("取消后任务变 cancelled", bool(_wait(lambda: jc.status == "cancelled", 8)), jc.status)
    check("取消后进程已退出", pid is not None and bool(_wait(lambda: not _pid_alive(pid), 8)), str(pid))
    check("取消的任务不会触发汇报", jc.quiet is True)

    # 5) 质检重试
    if flag.exists():
        flag.unlink()
    jf = orch.get(orch.start("FLAKY 会先失败", backend="a")["job_id"])
    check("质检未通过自动重试并成功", bool(_wait(lambda: jf.status == "done", 20)) and jf.attempts == 2,
          f"{jf.status} attempts={jf.attempts}")

    # 6) 持续失败
    jb = orch.get(orch.start("FAIL 必败", backend="a")["job_id"])
    check("持续失败标记 failed", bool(_wait(lambda: jb.status == "failed", 20)), jb.status)

    # 7) 追问
    ja = orch.get(orch.start("ASK 需要问路径", backend="a")["job_id"])
    check("后台追问进入 needs_input",
          bool(_wait(lambda: ja.status == "needs_input", 10)) and "存哪个盘" in ja.question,
          f"{ja.status} {ja.question}")
    orch.answer(ja.id, "D 盘")
    check("回答后继续并完成", bool(_wait(lambda: ja.status == "done", 15)), ja.status)

    # 8) 接力与前置失败
    j_a = orch.start("SHORT 第一步", backend="a")["job_id"]
    j_b = orch.get(orch.start("第二步", backend="b", depends_on=[j_a])["job_id"])
    check("依赖任务完成后接力并带上前置结果",
          bool(_wait(lambda: j_b.status == "done", 25)) and "前置" in j_b.result,
          f"{j_b.status} {j_b.result[:80]}")
    j_bad = orch.start("FAIL 前置失败", backend="a")["job_id"]
    j_dep = orch.get(orch.start("依赖失败任务", backend="b", depends_on=[j_bad])["job_id"])
    check("前置失败则后继直接失败", bool(_wait(lambda: j_dep.status == "failed", 30)), j_dep.status)

    # 9) 确认闸
    r = orch.start("把下载目录里的文件全部删除")
    check("危险任务需要口头确认", r.get("needs_confirm") is True and not r.get("accepted"), str(r))
    r = orch.start("把下载目录里的文件全部删除", confirmed=True)
    check("确认后才执行", r.get("accepted") is True, str(r))
    _wait(lambda: all(j.status not in ("queued", "running") for j in orch.store.jobs.values()), 20)

    # 10) 事件与落盘
    check("job 事件进了总线", any(e.get("status") == "done" for e in seen), str(len(seen)))
    check("完成回调被触发", any(s == "done" for _, s in done_cb), str(done_cb[:3]))
    orch2 = Orchestrator(EventBus(), conf, pathlib.Path("."), tmp / "jobs.json")
    check("job 表落盘且重启后可读", len(orch2.store.jobs) > 0, str(len(orch2.store.jobs)))
    jr = orch.get(orch.start("SLOW 重启时还在跑", backend="b")["job_id"])
    _wait(lambda: jr.status == "running")
    orch3 = Orchestrator(EventBus(), conf, pathlib.Path("."), tmp / "jobs.json")
    lost = orch3.get(jr.id)
    check("重启后运行中的任务标成 lost", lost is not None and lost.status == "lost",
          str(lost and lost.status))
    orch.cancel_all()

    # ---------- 前台自主触发：发言闸 ----------
    now = time.time()
    ini = Initiative(EventBus(), {"cooldown": 60, "hourly_cap": 2, "quiet_minutes": 30})
    sp = FakeSpeaker()
    ini.speaker = sp
    ini.submit(Intent("job", "通知A", 1, "", now + 600, key="a"))
    sp.is_idle = False
    check("用户在说话时不插嘴", ini.tick(now) is False and not sp.said)
    sp.is_idle = True
    check("安静下来后才开口", ini.tick(now) is True and len(sp.said) == 1)
    ini.submit(Intent("external", "通知B", 1, "", now + 600, key="b"))
    check("冷却期内不再主动说话", ini.tick(now + 5) is False)
    ini.submit(Intent("job", "紧急失败", HIGH, "", now + 600, key="c"))
    check("高优先级可以越过冷却", ini.tick(now + 6) is True and "紧急失败" in sp.said[-1])
    ini.quiet(30)
    ini.pending.clear()
    ini.last_spoke = 0
    ini.spoken_times.clear()
    ini.submit(Intent("external", "闲聊", 1, "", now + 600, key="d"))
    check("勿扰期间普通通知不说", ini.tick(now + 200) is False)
    ini.submit(Intent("job", "紧急2", HIGH, "", now + 600, key="e"))
    check("勿扰期间高优先级仍可说", ini.tick(now + 201) is True)

    ini2 = Initiative(EventBus(), {"cooldown": 0})
    sp2 = FakeSpeaker()
    ini2.speaker = sp2
    for i in range(3):
        ini2.submit(Intent("job", f"任务{i}完成", 1, "", now + 600, key=f"j{i}"))
    ini2.tick(now)
    check("多个 job 通知合并成一次发言",
          len(sp2.said) == 1 and all(f"任务{i}完成" in sp2.said[0] for i in range(3)), str(sp2.said))

    ini3 = Initiative(EventBus(), {"cooldown": 0})
    sp3 = FakeSpeaker()
    ini3.speaker = sp3
    ini3.submit(Intent("idle", "过期的闲聊", 0, "", now - 1, key="x"))
    check("过期意图直接丢弃", ini3.tick(now) is False and not sp3.said and not ini3.pending)

    ini4 = Initiative(EventBus(), {"cooldown": 0})
    sp4 = FakeSpeaker()
    sp4.session = False
    ini4.speaker = sp4
    said4: list = []
    ini4.fallback_say = said4.append
    ini4.present = lambda: False
    ini4.submit(Intent("job", "x", 1, "做完啦", now + 600, key="y"))
    check("没人在场时留言不说", ini4.tick(now) is False and len(ini4.pending) == 1)
    ini4.present = lambda: True
    check("没有实时会话时走退路 TTS", ini4.tick(now) is True and said4 == ["做完啦"], str(said4))

    ini5 = Initiative(EventBus(), {"cooldown": 0}, tmp / "schedule.json")
    sp5 = FakeSpeaker()
    ini5.speaker = sp5
    ini5.add_schedule(5, "问问面试结果")
    ini5.schedule[0]["at"] = now - 1
    check("到点的定时提醒会开口", ini5.tick(now) is True and "问问面试结果" in sp5.said[0], str(sp5.said))

    ini6 = Initiative(EventBus(), {"cooldown": 0})
    sp6 = FakeSpeaker()
    ini6.speaker = sp6
    ini6.on_job(orch.get(ja.id))
    ini6.tick(now)
    check("任务完成通知带安全声明",
          bool(sp6.said) and "不是指令" in sp6.said[0] and "[任务进展]" in sp6.said[0], str(sp6.said)[:120])
    check("任务通知里不出现后端名字（她自己做的）",
          bool(sp6.said) and orch.get(ja.id).backend not in sp6.said[0], str(sp6.said)[:120])
    ini7 = Initiative(EventBus(), {"cooldown": 0})
    sp7 = FakeSpeaker()
    ini7.speaker = sp7
    ini7.on_job(jc)
    check("用户取消的任务不汇报", not ini7.pending)

    # ---------- 实时工具：前后台一体 ----------
    from server.agents.orchestrator import build_prompt
    names = {t["name"] for t in TOOL_SCHEMAS}
    check("实时工具含 do_task / task_* / schedule / quiet",
          {"do_task", "task_status", "task_cancel", "task_answer", "schedule", "quiet"} <= names, str(names))
    schema_text = json.dumps(TOOL_SCHEMAS, ensure_ascii=False).lower()
    check("工具描述里不出现后端名字，也不能点名后端",
          not any(w in schema_text for w in ("codex", "claude", "opencode", "后台 agent", "\"backend\"")),
          str([w for w in ("codex", "claude", "opencode", "backend") if w in schema_text]))
    p = build_prompt(pathlib.Path("."), orch.get(ja.id), [], "", ["用户喜欢用 D 盘存文件"])
    check("动手时带上共同记忆", "用户喜欢用 D 盘存文件" in p and "大肥鱼" in p, p[:80])
    rt = client.app.state.realtime
    out = json.loads(execute(rt, "do_task", {"task": "清空回收站"}))
    check("工具层同样触发确认闸", out.get("needs_confirm") is True, str(out)[:100])
    out = json.loads(execute(rt, "do_task", {"task": "打开计算器", "kind": "desktop"}))
    check("工具 do_task 异步返回 job_id", bool(out.get("accepted") and out.get("job_id")), str(out)[:100])
    check("do_task 返回值不带后端", "backend" not in out and "note" not in out, str(out))
    st = json.loads(execute(rt, "task_status", {}))
    check("工具 task_status 能查到任务且不带后端",
          bool(st.get("tasks")) and "backend" not in json.dumps(st), str(st)[:100])
    out = json.loads(execute(rt, "agent_start", {"task": "打开记事本"}))
    check("旧工具名仍可用（别名）", bool(out.get("job_id")), str(out)[:100])
    out = json.loads(execute(rt, "schedule", {"seconds": 600, "reason": "喝水"}))
    check("工具 schedule 留言", out.get("ok") is True, str(out))
    out = json.loads(execute(rt, "quiet", {"minutes": 1}))
    check("工具 quiet 进入勿扰", out.get("ok") is True, str(out))
    instr = rt.base_instructions
    check("她的指令里不提后端名字", not any(w in instr for w in ("Codex", "Claude", "OpenCode")), "")

    # ---------- HTTP ----------
    r = client.get("/api/jobs")
    check("GET /api/jobs", r.status_code == 200 and "jobs" in r.json(), r.text[:80])
    r = client.post("/api/jobs", json={"task": "打开画图"})
    check("POST /api/jobs 返回任务号", r.status_code == 200 and bool(r.json().get("job_id")), r.text[:100])
    jid = r.json().get("job_id")
    r = client.post(f"/api/jobs/{jid}/cancel")
    check("POST /api/jobs/{id}/cancel", r.status_code == 200, r.text[:80])
    r = client.post("/api/initiative/notify", json={"text": "外部事件", "priority": 1})
    check("外部事件接口", r.status_code == 200 and r.json().get("ok"), r.text[:80])
    r = client.get("/api/agents")
    check("GET /api/agents", r.status_code == 200 and "initiative" in r.json(), r.text[:80])
