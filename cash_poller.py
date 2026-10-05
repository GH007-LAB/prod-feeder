# -*- coding: utf-8 -*-
"""
007 Metals - ปิดรอบ 007: poller ทุก 1 นาที (launchd com.007metals.cashpoller)

ทำตาม API_CONTRACT.md หัวข้อ Feeder "ทุก 1 นาที":
  1. cash_tick() — สร้างรอบ / สั่งตัด 16:45 / fallback 5 นาที / ล็อก late
  2. pending_cuts → ดึง DBF สาขานั้นทันที (cash_feed.sync_branch force, ไม่โหลด ARRCPIT)
     → cash_finalize_cut(round_id)  · รวมรอบที่ตัดแล้วกด "ดึงบิลเพิ่ม" (extra_pull: ต่อท้ายใบใหม่)
     · ดึงไม่สำเร็จ = ไม่ finalize ปล่อยให้ tick ตัดเอง
     (auto_fallback ภายใน fallback_minutes) — ไม่ตัดจากข้อมูลที่รู้ว่าพัง
  3. to_export → cash_export_out(id) → เขียน
        All_on_Cloud/AutoExport/sales_report/{BR}/{YYMMDD}_out.json   (YYMMDD จาก out.date)
     แบบ atomic (tmp → os.replace) แล้วค่อย cash_mark_exported(id) — เขียนไม่ได้ = ไม่ mark
     รอบหน้าลองใหม่ (รอบที่ CTO ปลดล็อกแล้วส่งใหม่ exported_at ถูกล้าง → เขียนทับไฟล์เดิม)
  4. locked_late → ลง log "CASH-LATE:" (ช่องทาง LINE ยังรอ CTO เคาะผู้รับ — ดูท้ายไฟล์)
  5. เดือนละครั้ง cash_purge_old() (PDPA ล้างชื่อลูกค้าเก่า 13 เดือน) · stamp ใน state_dir

config: ใช้ ~/007so_push/cfg_{BR}.txt ที่ run_all.sh เขียนทุกรอบ (ตัดสิน local mirror / proxy
และใส่ service key ไว้ใน SUPABASE_KEY แล้ว) — poller จึงไม่ต้องรู้ feeder.env เอง
ที่เขียน out.json: Drive Mirror ของ 007skn0777 (~/ไดรฟ์ของฉัน (…)/All_on_Cloud/AutoExport)
ทางเดียวกับ finny_pos_push.py ที่ launchd เขียนอยู่แล้ว (Mirror = ไฟล์จริง ไม่ใช่ FileProvider)
override ได้ด้วย env CASH_EXPORT_ROOT=<โฟลเดอร์ AutoExport>

log: ลง stdout (plist ชี้เข้า ~/007so_push/feeder.log) — พิมพ์เฉพาะตอนมีงาน/พัง
รอบว่างไม่พิมพ์ (1,440 รอบ/วัน) · heartbeat อยู่ที่ ~/007so_push/cash_poller_last.json
บรรทัด "ERROR: CASH_POLLER ..." → alert_scan.py ส่ง LINE ผู้ดูแลระบบเหมือนสคริปต์อื่น

usage: python3 cash_poller.py   (เทสต์: tests/test_cash_feed.py ใช้ mock RPC เรียก run_once)
"""
import sys, os, json, glob, time, fcntl, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S
import cash_feed as F

BRANCHES = ("BK", "SKN", "PPS")
DRIVE_GLOB = "ไดรฟ์ของฉัน (007skn0777@gmail.com)"


def export_root():
    """โฟลเดอร์ AutoExport ที่เขียนได้ (Mirror ในเครื่อง) — None ถ้าหาไม่เจอ"""
    env = os.environ.get("CASH_EXPORT_ROOT")
    if env:
        return env if os.path.isdir(env) else None
    home = os.path.expanduser("~")
    cands = [os.path.join(home, DRIVE_GLOB, "All_on_Cloud", "AutoExport")]
    cands += [os.path.join(p, "All_on_Cloud", "AutoExport")
              for p in sorted(glob.glob(os.path.join(home, "ไดรฟ์ของฉัน*")))]
    for p in cands:
        if os.path.isdir(p):
            return p
    return None


def load_branch_cfg(branch):
    """cfg_{BR}.txt ของ run_all — อ่านซ้ำ 1 ครั้งถ้าเจอตอน run_all กำลังเขียนทับ (ไฟล์ว่าง)"""
    path = os.path.join(S.state_dir(), "cfg_%s.txt" % branch)
    for attempt in (1, 2):
        try:
            cfg = S.load_config(path)
            if cfg.get("SUPABASE_URL") and cfg.get("SUPABASE_KEY"):
                return cfg
        except (OSError, SystemExit):
            pass
        time.sleep(1)
    raise RuntimeError("อ่าน %s ไม่ได้ (run_all.sh ยังไม่เคยรัน?)" % path)


def write_out(root, out):
    """เขียน sales_report/{BR}/{YYMMDD}_out.json แบบ atomic · คืน path"""
    br = out["branch"]
    d = datetime.date.fromisoformat(out["date"])
    folder = os.path.join(root, "sales_report", br)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s_out.json" % d.strftime("%y%m%d"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def heartbeat(info):
    path = os.path.join(S.state_dir(), "cash_poller_last.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    os.replace(tmp, path)


PURGE_EVERY_DAYS = 30


def maybe_purge(cfg, now=None):
    """เดือนละครั้ง: cash_purge_old() ล้างชื่อลูกค้าเก่ากว่า 13 เดือน (PDPA, contract)
    stamp อยู่ state_dir — ประทับหลังสำเร็จเท่านั้น (พัง = ลองใหม่รอบหน้า แต่ไม่เกินวันละครั้ง)
    คืนผล RPC หรือ None ถ้ายังไม่ถึงรอบ"""
    stamp = os.path.join(S.state_dir(), "cash_purge_last.txt")
    tried = os.path.join(S.state_dir(), "cash_purge_tried.txt")
    now = now or time.time()
    try:
        if now - os.path.getmtime(stamp) < PURGE_EVERY_DAYS * 86400:
            return None
    except OSError:
        pass
    try:
        if now - os.path.getmtime(tried) < 86400:
            return None
    except OSError:
        pass
    with open(tried, "w") as f:
        f.write(datetime.datetime.now(F.TH).isoformat(timespec="seconds"))
    try:
        res = F.RPC(cfg, "cash_purge_old", {}) or {}
    except F.RpcMissing:
        raise
    except Exception as e:
        S.log("ERROR: CASH_POLLER purge ชื่อลูกค้าเก่าไม่สำเร็จ %s: %s" % (type(e).__name__, e))
        return None
    with open(stamp, "w") as f:
        f.write(json.dumps(res, ensure_ascii=False))
    S.log("CASH_PURGE: ล้างชื่อลูกค้าที่ doc_date < %s · cash_doc %s · cash_unpaid_iv %s"
          % (res.get("before"), res.get("cash_doc"), res.get("cash_unpaid_iv")))
    return res


def run_once(cfgs=None, root=None):
    """1 รอบ poller · cfgs = {BR: cfg} (เทสต์ส่งเองได้) · คืนสรุปไว้ทดสอบ/heartbeat"""
    t0 = time.time()
    cfgs = cfgs or {}
    def cfg_of(br):
        if br not in cfgs:
            cfgs[br] = load_branch_cfg(br)
        return cfgs[br]

    any_cfg = cfg_of(BRANCHES[0])       # ใช้แค่ URL/key เรียก tick
    tick = F.RPC(any_cfg, "cash_tick", {}) or {}
    summary = {"at": datetime.datetime.now(F.TH).isoformat(timespec="seconds"),
               "cut": [], "cut_failed": [], "exported": [], "export_failed": [], "late": []}

    # ---- 2) ตัดรอบ: สาขาละครั้งพอ แม้มีหลายรอบค้าง (เช่นรอบเมื่อวานกับวันนี้) ----
    by_branch = {}
    for p in tick.get("pending_cuts") or []:
        by_branch.setdefault(p["branch"], []).append(p)
    for br, rounds in sorted(by_branch.items()):
        try:
            cfg = cfg_of(br)
            # รอ lock ได้ไม่นาน — ถ้า run_all กำลัง sync สาขานี้อยู่ รอบหน้า (1 นาที) ค่อยตัด
            res = F.sync_branch(cfg, force=True, with_unpaid=False, lock_wait=20)
        except Exception as e:
            S.log("ERROR: CASH_POLLER %s ดึง DBF ก่อนตัดรอบไม่สำเร็จ %s: %s — รอ tick ตัดเอง"
                  " (auto_fallback)" % (br, type(e).__name__, e))
            summary["cut_failed"] += [r["round_id"] for r in rounds]
            continue
        for r in rounds:
            try:
                fin = F.RPC(cfg, "cash_finalize_cut", {"p_round_id": r["round_id"]}) or {}
                # รอบที่ตัดแล้วกด "ดึงบิลเพิ่ม" กลับมาเป็น cutting อีกรอบ → finalize ต่อท้ายเฉพาะใบใหม่
                # คืน {extra_pull:true, added} · cut_by คงเดิม
                S.log("CASH_CUT: %s รอบ %s (%s) %s by=%s · เพิ่ม %s ใบ · รวม %s ใบ (ยกมา %s) · "
                      "อ่าน DBF %.1f วิ" % (
                          br, r.get("round_date"), r["round_id"],
                          "ดึงบิลเพิ่ม extra_pull" if fin.get("extra_pull") else "ตัดแล้ว",
                          fin.get("cut_by"), fin.get("added"), fin.get("doc_count"),
                          fin.get("carried_count"), res["read_sec"]))
                if fin.get("already"):
                    S.log("CASH_CUT: %s รอบ %s ไม่อยู่ในสถานะ cutting แล้ว (%s) — ข้าม"
                          % (br, r["round_id"], fin.get("status")))
                summary["cut"].append(r["round_id"])
                if fin.get("extra_pull"):
                    summary.setdefault("extra_pull", []).append(
                        {"round_id": r["round_id"], "added": fin.get("added")})
            except Exception as e:
                S.log("ERROR: CASH_POLLER %s finalize รอบ %s ไม่สำเร็จ %s: %s"
                      % (br, r["round_id"], type(e).__name__, e))
                summary["cut_failed"].append(r["round_id"])

    # ---- 3) ส่งออก out.json ----
    exports = tick.get("to_export") or []
    if exports:
        root = root or export_root()
        if not root:
            S.log("ERROR: CASH_POLLER ไม่พบโฟลเดอร์ AutoExport ใน Drive Mirror — ยังไม่ export %d รอบ"
                  % len(exports))
            summary["export_failed"] += [x["round_id"] for x in exports]
            exports = []
    for x in exports:
        try:
            out = F.RPC(any_cfg, "cash_export_out", {"p_round_id": x["round_id"]})
            if not out or out.get("branch") != x["branch"] or not out.get("date"):
                raise RuntimeError("out.json ไม่ครบ: %r" % (out,))
            path = write_out(root, out)
            F.RPC(any_cfg, "cash_mark_exported", {"p_round_id": x["round_id"]})
            S.log("CASH_EXPORT: %s รอบ %s (%s) -> %s" %
                  (x["branch"], out["date"], x.get("status"), os.path.relpath(path, root)))
            summary["exported"].append(x["round_id"])
        except Exception as e:
            S.log("ERROR: CASH_POLLER export รอบ %s ไม่สำเร็จ %s: %s" % (x["round_id"], type(e).__name__, e))
            summary["export_failed"].append(x["round_id"])

    # ---- 4) เลยเส้นตาย ----
    late = tick.get("locked_late") or []
    if late:
        names = {x["round_id"]: x["branch"] for x in tick.get("to_export") or []}
        for rid in late:
            # ไม่ใช้คำ ERROR — นี่คือเหตุการณ์ธุรกิจ ไม่ใช่ระบบพัง (กัน alert_scan ส่งหาปอนด์)
            S.log("CASH-LATE: %s รอบ %s เลยเส้นตายยังไม่ส่ง — ล็อกแล้ว" % (names.get(rid, "?"), rid))
        summary["late"] = late

    # ---- 5) PDPA: ล้างชื่อลูกค้าเก่าเดือนละครั้ง (ทำท้ายสุด ไม่หน่วงการตัดรอบ) ----
    purged = maybe_purge(any_cfg)
    if purged is not None:
        summary["purge"] = purged

    summary["sec"] = round(time.time() - t0, 2)
    for k in ("created", "auto_cut_requested", "fallback_cut"):
        summary[k] = tick.get(k) or []
    if summary["fallback_cut"]:
        S.log("CASH_TICK: ตัดเองแบบ auto_fallback รอบ %s (feeder ดึง DBF ไม่ทันเวลา)" % summary["fallback_cut"])
    return summary


def main():
    lock_path = os.path.join(S.state_dir(), "cash_poller.lock")
    lf = open(lock_path, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # รอบก่อนยังไม่จบ (เช่นตัดรอบผ่าน proxy 3 สาขาพร้อมกัน) — ข้ามเงียบ ๆ
        return
    try:
        summary = run_once()
        heartbeat(summary)
    except F.RpcMissing as e:
        heartbeat({"at": datetime.datetime.now(F.TH).isoformat(timespec="seconds"),
                   "skip": "ยังไม่มี RPC %s บน Supabase" % e})
    except Exception as e:
        S.log("ERROR: CASH_POLLER %s: %s" % (type(e).__name__, e))
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


if __name__ == "__main__":
    main()

# ยังไม่ทำ (รอ CTO เคาะ): แจ้ง LINE เมื่อ locked_late — ผู้รับ (หัวหน้าสาขา? Finny? ผู้บริหาร?)
# และช่องทาง (hub /api/notify ของ hr-app) · ห้ามใช้บอทตอบในกลุ่ม · ระวังโควตา OA 007 HR
