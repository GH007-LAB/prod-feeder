# -*- coding: utf-8 -*-
"""
007 Metals - ปิดรอบ 007 (cash app): ป้อนเอกสารรับเงินจาก Express -> Supabase

อ่าน ARTRN.DBF (+ ARMAS ชื่อลูกค้า, ARRCPIT ตรวจประกอบ) ของสาขาเดียว แล้วส่งผ่าน RPC
ตาม ~/cash-app/sql/API_CONTRACT.md หัวข้อ Feeder — ห้าม PostgREST upsert ตรง:
  · cash_feed_docs(p_branch, p_docs, p_window_from, p_dbf_mtime) — RE/AI ทุกใบตั้งแต่ window_from
    (+ iv_refs ต่อใบ RE จาก ARRCPIT — ใส่ช่องทางที่เลือกไว้ล่วงหน้าบน IV · 9 ต.ค. 69)
    + เวลาไฟล์ ARTRN บน Drive (→ cash_feed_status ให้หน้าแอปบอกความสดของข้อมูล)
  · cash_feed_unpaid_iv(p_branch, p_rows)             — IV ค้างรับทั้งชุด (แทนที่)

กติกาข้อมูล (ตรวจกับ DBF จริง 5 ต.ค. 69):
  · RECTYP '9' = RE (ใบเสร็จ) · '0' = AI (รับมัดจำ) · '3' = IV — ส่งเข้า cash_doc เฉพาะ RE/AI
    (HS/SR/DR ห้ามส่งตาม contract — HS ที่เจอเป็นขายระหว่างสาขา/Shopee/TikTok)
  · amount = NETAMT (ยอดบนใบ) · docstat ส่งค่าดิบ ('C' = ยกเลิก, RE จริงมีแต่ 'M'/'N')
  · RE มี NETAMT ติดลบได้ (ใบเสร็จที่หัก SR เกินยอด IV) และ 0 ได้ — ส่งตามจริง ไม่กรองทิ้ง
    (contract: ระบบเก็บแต่ไม่ดึงเข้ารอบ ยอด ≤ 0 = เครดิต ไม่ใช่เงินคืน)
  · เลือกใบที่ DOCDAT >= window_from **หรือ** CHGDAT >= window_from
    (CHGDAT ช้ากว่า DOCDAT ได้ถึง 5 วัน = ใบลงย้อนหลัง/แก้ทีหลัง → จับ changed_after_cutoff ได้)
    p_window_from = วันนี้ - CASH_WINDOW_DAYS (ค่าเริ่ม 7) → ฝั่ง SQL ตั้ง missing_in_express
    ให้ใบในฐานที่ doc_date >= window_from แต่ไม่อยู่ใน payload
    **ส่ง window ทุกครั้งที่ไฟล์ครบ** — Express ยกเลิกใบ = ลบ record ทิ้ง (RE ไม่เคยมี DOCSTAT 'C')
    window จึงเป็นทางเดียวที่รู้ว่าใบถูกยกเลิก
  · IV ค้างรับ = RECTYP 3, DOCSTAT <> 'C', REMAMT > 0 · amount = REMAMT (ยอดที่ยังค้าง)
    ARRCPIT ใช้แค่นับว่าใบไหน "มี RE บางส่วนแล้ว" (ลงบันทึก) — ARRCPIT ขึ้น Drive เฉพาะ
    ตอนไฟล์เปลี่ยน REMAMT ใน ARTRN จึงเป็นตัวตัดสินหลัก
  · ไม่มีเวลาสร้างเอกสารใน Express — มีแค่วันที่

กันพัง:
  · DBF ขาดท้าย (Drive/proxy ส่งมาไม่ครบ) → ไม่ส่ง window (ไม่งั้นตั้ง missing ทั้งช่วง)
    และไม่ส่ง IV ค้าง (RPC แทนที่ทั้งชุด = ลบแถวที่ค้างจริงทิ้ง)
  · จำนวนใบในช่วงเดียวกันลดฮวบจากรอบก่อน (>5 ใบ และ >30%) → ยังส่ง window ตาม contract
    แต่ลง ERROR ให้ alert_scan แจ้ง (ยกเลิกทีละมาก ๆ ผิดวิสัย ควรมีคนดู)
  · fingerprint ไม่เปลี่ยน → ไม่ยิง (เหมือน express_sync) · cash_poller ส่ง force=True ข้ามได้
  · RPC ยังไม่ถูกสร้างบน Supabase (PGRST202/404) → ข้ามเงียบ ๆ ไม่ใช่ ERROR
  · lock ต่อสาขา กัน run_all (10 นาที) กับ cash_poller (1 นาที) ยิงสาขาเดียวกันชนกัน

usage: SUPABASE_SERVICE_KEY=<service_role_key> python3 cash_feed.py <config_file>
                            [--dry] [--force] [--today YYYY-MM-DD]
   --dry    อ่าน + พิมพ์สรุป ไม่ยิง Supabase
   --force  ข้าม fingerprint
   ⚠️ RPC ฝั่ง feeder เรียกได้เฉพาะ service_role (cash__is_service) — ส่ง key ทาง env
"""
import sys, os, json, time, hashlib, datetime, fcntl, struct, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S  # reuse PROXY / read_dbf / load_config / state_dir / log / _file_exists

TH = datetime.timezone(datetime.timedelta(hours=7))
DOC_TYPE = {"9": "RE", "0": "AI"}   # RECTYP -> doc_type ของ cash_doc
IV_RECTYP = "3"
CANCELLED = "C"
WINDOW_DAYS_DEFAULT = 7
ARMAS_TTL_MIN_DEFAULT = 60          # อ่าน ARMAS ใหม่ชั่วโมงละครั้ง (ชื่อลูกค้าแทบไม่เปลี่ยน)
DROP_GUARD_MIN = 5                  # ใบหายเกินกี่ใบ + เกิน DROP_GUARD_RATIO ถือว่าอ่าน DBF พลาด
DROP_GUARD_RATIO = 0.30
RPC_TIMEOUT = 20


class RpcMissing(Exception):
    """RPC ยังไม่ถูกสร้างบน Supabase (ยังไม่ได้รัน 00*.sql) — ไม่ใช่ error ของ feeder"""


def norm(s):
    """ตัดช่องว่าง + แปลง NBSP (\\xa0) เป็น space — นิยามเดียวกับ express_sync.norm"""
    return (s or "").replace("\xa0", " ").strip()


def today_th():
    return datetime.datetime.now(TH).date()


# ---------- Supabase RPC (คืน JSON body — sb_request เดิมคืนแค่ status) ----------
def rpc_http(cfg, fn, args):
    url = cfg["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/" + fn
    headers = {
        "apikey": cfg["SUPABASE_KEY"],
        "Authorization": "Bearer " + cfg["SUPABASE_KEY"],
        "Content-Type": "application/json",
    }
    data = json.dumps(args, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    # RPC ฝั่ง feeder ทุกตัว idempotent (upsert / finalize เช็คสถานะ / mark_exported)
    # จึง retry เฉพาะ network error ได้ปลอดภัย — HTTP error ไม่ retry
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as resp:
                body = resp.read()
                return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as e:
            body = e.read()[:400].decode("utf-8", "replace")
            if e.code == 404 or "PGRST202" in body:
                raise RpcMissing(fn)
            raise RuntimeError("RPC %s -> HTTP %s %s" % (fn, e.code, body))
        except (urllib.error.URLError, OSError) as e:
            if attempt == 3:
                raise
            S.log("  network retry %d (%s)" % (attempt, e))
            time.sleep(3 * attempt)


RPC = rpc_http  # เทสต์แทนด้วย mock ได้


# ---------- อ่าน DBF ----------
_BUF = {}


def _memo_read(orig):
    """อ่านไฟล์ครั้งเดียวต่อ process + ตรวจว่าไฟล์ครบตาม header (กัน DBF ขาดท้าย)"""
    def wrapped(path, *a, **kw):
        if path not in _BUF:
            _BUF[path] = orig(path, *a, **kw)
        return _BUF[path]
    return wrapped


if not getattr(S._read_file_retry, "_cash_memo", False):
    S._read_file_retry = _memo_read(S._read_file_retry)
    S._read_file_retry._cash_memo = True


def dbf_complete(path):
    """True ถ้าความยาวไฟล์ >= hdrlen + nrec*reclen (read_dbf เดิม break เงียบ ๆ ถ้าขาด)"""
    buf = S._read_file_retry(path)
    if len(buf) < 12:
        return False
    nrec = struct.unpack("<I", buf[4:8])[0]
    hdrlen = struct.unpack("<H", buf[8:10])[0]
    reclen = struct.unpack("<H", buf[10:12])[0]
    return len(buf) >= hdrlen + nrec * reclen


def dbf_mtime(path):
    """mtime ของไฟล์บน Drive เป็น ISO +07:00 (robocopy ของ 007DBFSync คงเวลาเดิมจาก Express)
    โหมด proxy: Code.gs action=meta คืนแค่ size → None (RPC รับ null ได้)"""
    if S.PROXY:
        return None
    try:
        ts = os.path.getmtime(path)
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(ts, TH).isoformat(timespec="seconds")


def clear_cache():
    _BUF.clear()


def load_names(src, branch, need=None):
    """ชื่อลูกค้า CUSCOD -> CUSNAM — cache ลง state_dir อายุ ARMAS_TTL_MIN
    need = ชุด CUSCOD ที่ต้องมี — ถ้า cache ขาดตัวไหน (ลูกค้าใหม่) อ่าน ARMAS ใหม่ทันที
    ตอนตัดรอบจึงไม่ต้องโหลด ARMAS 5-6MB ทุกครั้ง (ผ่าน proxy กิน ~10 วิ/สาขา)"""
    cache = os.path.join(S.state_dir(), "cash_armas_%s.json" % branch)
    ttl = int(os.environ.get("CASH_ARMAS_TTL_MIN") or ARMAS_TTL_MIN_DEFAULT) * 60
    names = None
    try:
        if time.time() - os.path.getmtime(cache) < ttl:
            with open(cache, encoding="utf-8") as f:
                names = json.load(f)
    except (OSError, ValueError):
        names = None
    if names is not None and not (need and (set(need) - set(names))):
        return names
    names = {}
    for r in S.read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "CUSNAM"}):
        c = (r.get("CUSCOD") or "").strip()
        if c:
            names[c] = norm(r.get("CUSNAM"))
    # ชื่อลูกค้า = ข้อมูลส่วนบุคคล (PDPA) → สิทธิ์ 600 ตั้งแต่ตอนสร้าง (security review L8)
    tmp = cache + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)          # tmp ค้างจากรอบก่อนที่สิทธิ์กว้างกว่า
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False)
    os.replace(tmp, cache)
    return names


def collect(src, branch, today, window_days=WINDOW_DAYS_DEFAULT, with_unpaid=True):
    """อ่าน DBF ของสาขา -> (docs, unpaid, info) ตามรูป payload ของ contract

    docs   : [{doc_no, doc_type, doc_date, customer_code, customer_name, amount, docstat}]
    unpaid : [{doc_no, doc_date, customer_code, customer_name, amount}] หรือ None
    info   : {window_from, complete, partial_iv, ...} ใช้ลง log / ตัดสิน guard
    """
    window_from = today - datetime.timedelta(days=window_days)
    artrn = os.path.join(src, "ARTRN.DBF")
    complete = dbf_complete(artrn)
    raw_docs, raw_iv = [], []
    for r in S.read_dbf(artrn, fields={"RECTYP", "DOCNUM", "DOCDAT", "CHGDAT", "CUSCOD",
                                       "NETAMT", "REMAMT", "DOCSTAT"}):
        t = (r.get("RECTYP") or "").strip()
        doc = (r.get("DOCNUM") or "").strip()
        dat = r.get("DOCDAT")
        if not doc or not dat:
            continue
        if t in DOC_TYPE:
            chg = r.get("CHGDAT")
            if dat >= window_from or (chg and chg >= window_from):
                raw_docs.append((t, doc, dat, r))
        elif t == IV_RECTYP and with_unpaid:
            if (r.get("DOCSTAT") or "").strip() != CANCELLED and float(r.get("REMAMT") or 0) > 0.005:
                raw_iv.append((doc, dat, r))

    need = {(x[3].get("CUSCOD") or "").strip() for x in raw_docs}
    need |= {(x[2].get("CUSCOD") or "").strip() for x in raw_iv}
    need.discard("")
    names = load_names(src, branch, need) if need else {}

    # 007 (CTO 9 ต.ค. 69): RE ปิด IV ใบไหนบ้าง — ARRCPIT: RCPNUM = เลข RE, DOCNUM = เลข IV (RECTYP 3)
    # ส่งเป็น iv_refs ต่อใบ RE ให้ cash_feed_docs ใส่ช่องทางที่พนักงานเลือกไว้ล่วงหน้าบน IV
    iv_by_re, has_re = {}, set()
    rc = os.path.join(src, "ARRCPIT.DBF")
    if S._file_exists(rc):
        for r in S.read_dbf(rc, fields={"RCPNUM", "DOCNUM", "RECTYP"}):
            re_no = (r.get("RCPNUM") or "").strip()
            iv_no = (r.get("DOCNUM") or "").strip()
            if not re_no or not iv_no:
                continue
            has_re.add(iv_no)
            if (r.get("RECTYP") or "").strip() == IV_RECTYP and re_no.startswith("RE") and iv_no.startswith("IV"):
                iv_by_re.setdefault(re_no, set()).add(iv_no)

    docs, seen = [], set()
    for t, doc, dat, r in sorted(raw_docs, key=lambda x: (x[2], x[1])):
        if doc in seen:          # DBF จริงไม่มีเลขซ้ำ แต่ RPC ปฏิเสธทั้งชุดถ้าซ้ำ — กันไว้
            continue
        seen.add(doc)
        cus = (r.get("CUSCOD") or "").strip()
        docs.append({
            "doc_no": doc,
            "doc_type": DOC_TYPE[t],
            "doc_date": dat.isoformat(),
            "customer_code": cus or None,
            "customer_name": names.get(cus) or None,
            "amount": round(float(r.get("NETAMT") or 0), 2),
            "docstat": (r.get("DOCSTAT") or "").strip(),
            "iv_refs": sorted(iv_by_re.get(doc, ())),
        })

    unpaid, partial = None, 0
    if with_unpaid:
        unpaid = []
        for doc, dat, r in sorted(raw_iv, key=lambda x: (x[1], x[0])):
            if doc in has_re:
                partial += 1     # มี RE แล้วแต่ยังค้างบางส่วน
            cus = (r.get("CUSCOD") or "").strip()
            unpaid.append({
                "doc_no": doc,
                "doc_date": dat.isoformat(),
                "customer_code": cus or None,
                "customer_name": names.get(cus) or None,
                "amount": round(float(r.get("REMAMT") or 0), 2),
            })

    in_window = sum(1 for d in docs if d["doc_date"] >= window_from.isoformat())
    return docs, unpaid, {"window_from": window_from.isoformat(), "complete": complete,
                          "in_window": in_window, "partial_iv": partial}


def fp(obj):
    return hashlib.md5(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class branch_lock(object):
    """flock ต่อสาขา — รอได้ไม่เกิน wait วินาที (run_all รอ poller ได้, poller ไม่ควรรอนาน)"""

    def __init__(self, branch, wait=60):
        self.path = os.path.join(S.state_dir(), "cash_feed_%s.lock" % branch)
        self.wait = wait
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "w")
        end = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= end:
                    self.f.close()
                    raise TimeoutError("lock %s ไม่ว่างเกิน %d วิ" % (self.path, self.wait))
                time.sleep(0.5)

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def sync_branch(cfg, force=False, with_unpaid=True, dry=False, today=None, lock_wait=60):
    """อ่าน DBF + ยิง RPC ของสาขาเดียว · คืน dict สรุป (ใช้ทั้ง main และ cash_poller)
    raise เมื่อพัง (ผู้เรียกตัดสินว่าจะ finalize ต่อไหม) · RpcMissing = ยังไม่ deploy SQL"""
    branch = cfg["BRANCH"]
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL"):
        S.PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=branch)
    else:
        S.PROXY.clear()
    today = today or today_th()
    window_days = int(cfg.get("CASH_WINDOW_DAYS") or os.environ.get("CASH_WINDOW_DAYS")
                      or WINDOW_DAYS_DEFAULT)

    with branch_lock(branch, wait=lock_wait):
        clear_cache()   # อ่านสดทุกครั้งที่เข้า lock (poller เรียกหลายสาขาใน process เดียว)
        t0 = time.time()
        if not S._file_exists(os.path.join(src, "ARTRN.DBF")):
            raise RuntimeError("%s ไม่มี ARTRN.DBF" % branch)
        docs, unpaid, info = collect(src, branch, today, window_days, with_unpaid)
        read_sec = time.time() - t0

        state_file = os.path.join(S.state_dir(), "state_cash_%s.json" % branch)
        try:
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}

        # ---- guard: ตัดสินว่าจะส่ง window ไหม ----
        window_from = info["window_from"]
        warn = None
        if not info["complete"]:
            warn = "ARTRN.DBF ไม่ครบตาม header (โหลดไม่จบ?)"
        if warn:
            window_from = None
            S.log("ERROR: CASH_FEED %s %s -> ส่งแบบไม่ตั้งธงหาย (ไม่ส่ง window)" % (branch, warn))
        elif state.get("window_from") == window_from:
            prev = int(state.get("in_window") or 0)
            drop = prev - info["in_window"]
            if drop > DROP_GUARD_MIN and drop > prev * DROP_GUARD_RATIO:
                # ไฟล์ครบตาม header แล้ว → เชื่อไฟล์ (contract ให้ส่ง window ทุกครั้ง) แต่ให้คนดู
                S.log("ERROR: CASH_FEED %s ใบในช่วงเดียวกันลดจาก %d เหลือ %d (ยกเลิก/ลบใน Express?)"
                      " — ส่ง window ตามปกติ ใบที่หายจะถูกตั้ง missing_in_express"
                      % (branch, prev, info["in_window"]))
        # payload ว่าง (ไม่มีใบเลยในช่วง) RPC ปฏิเสธ window → ไม่ส่งอะไรเลยด้านล่าง (send_docs=False)

        mtime = dbf_mtime(os.path.join(src, "ARTRN.DBF"))
        docs_fp = fp([window_from, mtime, docs])
        iv_fp = fp(unpaid) if unpaid is not None else None
        send_docs = bool(docs) and (force or state.get("docs_fp") != docs_fp)
        # IV ค้าง = แทนที่ทั้งชุด → ไฟล์ไม่ครบห้ามส่งเด็ดขาด (จะลบแถวที่ยังค้างจริงทิ้ง)
        send_iv = (unpaid is not None and info["complete"]
                   and (force or state.get("iv_fp") != iv_fp))

        S.log("CASH_FEED: %s RE/AI %d ใบ (ในช่วง %d ตั้งแต่ %s) · IV ค้าง %s (มี RE บางส่วน %d) · "
              "อ่าน %.1f วิ%s%s" % (
                  branch, len(docs), info["in_window"], info["window_from"],
                  "-" if unpaid is None else len(unpaid), info["partial_iv"], read_sec,
                  "" if (send_docs or send_iv) else " · ไม่เปลี่ยน ข้าม",
                  " (DRY)" if dry else ""))
        out = {"branch": branch, "docs": len(docs), "unpaid": None if unpaid is None else len(unpaid),
               "read_sec": round(read_sec, 2), "window_from": window_from,
               "sent_docs": False, "sent_iv": False}
        if dry:
            out.update(payload_docs=docs, payload_iv=unpaid)
            return out

        if send_docs:
            args = {"p_branch": branch, "p_docs": docs}
            if window_from:
                args["p_window_from"] = window_from
            if mtime:
                args["p_dbf_mtime"] = mtime
            res = RPC(cfg, "cash_feed_docs", args) or {}
            out["sent_docs"] = True
            out["feed_result"] = res
            if res.get("newly_flagged") or res.get("newly_missing"):
                S.log("CASH_FEED: %s ธงใหม่ เปลี่ยนหลังตัด %s · หายจาก Express %s" %
                      (branch, res.get("newly_flagged"), res.get("newly_missing")))
            state["docs_fp"] = docs_fp
            if window_from:
                state["window_from"] = window_from
                state["in_window"] = info["in_window"]
        if send_iv:
            RPC(cfg, "cash_feed_unpaid_iv", {"p_branch": branch, "p_rows": unpaid})
            out["sent_iv"] = True
            state["iv_fp"] = iv_fp
        if send_docs or send_iv:
            state["synced_at"] = datetime.datetime.now(TH).isoformat(timespec="seconds")
            tmp = state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, state_file)
        return out


def arg_today(argv):
    for i, a in enumerate(argv):
        if a == "--today" and i + 1 < len(argv):
            return datetime.date.fromisoformat(argv[i + 1])
        if a.startswith("--today="):
            return datetime.date.fromisoformat(a.split("=", 1)[1])
    return None


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    dry = "--dry" in sys.argv
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not dry and not (key and cfg.get("SUPABASE_URL")):
        S.log("CASH_FEED: ข้าม — ไม่มี SUPABASE_SERVICE_KEY (RPC feeder เรียกได้เฉพาะ service role)")
        return
    if key:
        cfg["SUPABASE_KEY"] = key
    try:
        sync_branch(cfg, force="--force" in sys.argv, dry=dry, today=arg_today(sys.argv))
    except RpcMissing as e:
        S.log("CASH_FEED: %s ข้าม — ยังไม่มี RPC %s บน Supabase (ยังไม่รัน cash-app/sql)" %
              (cfg["BRANCH"], e))
    except Exception as e:  # ไม่ให้ run_all ล้มทั้งรอบ — ERROR ให้ alert_scan จับ
        S.log("ERROR: CASH_FEED %s %s: %s" % (cfg["BRANCH"], type(e).__name__, e))


if __name__ == "__main__":
    main()
