# -*- coding: utf-8 -*-
"""
007 Metals - SO Push (Production Process realtime feed)
อ่าน SO ใหม่/ที่เปลี่ยนจาก DBF ของ Express ที่ต้นทาง -> upsert ตรงเข้า Supabase
ออกแบบให้รันบนเครื่องสาขาผ่าน Task Scheduler ทุก 2 นาที (zero-dependency, pure Python 3)

usage:  python so_push.py <config_file> [--dry]
        --dry = อ่าน+เทีบแคโต้ delta อย่างเดียว ไม่ยิงขึ้น Supabase (ทดสอบ)

config file (KEY=VALUE):
  BRANCH=SKN
  SRC=Z:\\skn2569
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_KEY=eyJ...        (anon key เท่านั้น — ห้ามใช้ service_role)
  WINDOW_DAYS=45             (มองย้อน SO กี่วัน)

state/log เก็บที่ %LOCALAPPDATA%\\007so_push\\ (ไม่ปนโฟลเดอร์ Drive)
"""
import sys, os, struct, json, datetime, hashlib, base64, urllib.request, urllib.error, urllib.parse, time

# ---------- Drive proxy mode (Apps Script) — เลี่ยง local FileProvider mount ----------
# macOS FileProvider (Google Drive Desktop) อ่านไม่ได้จาก background daemon (launchd/cron)
# ไม่ว่าจะให้ Full Disk Access ยังไง — ต้องดึงผ่าน HTTPS แทน (ดู CTO_SETUP.md)
PROXY = {}  # set in main(): {'url':..., 'token':..., 'branch':...} ถ้าตั้ง PROXY_URL ใน config
# Apps Script โหลดไฟล์เต็มจาก Drive ทุก request อยู่ดี (ไม่มี state ข้าม invocation) — แบ่ง chunk เล็ก
# มีแต่เสีย (ยิงซ้ำหลายรอบ) ตั้งให้ใหญ่พอที่ไฟล์ปัจจุบันทั้งหมด (~22MB) จบในคำขอเดียว
# ยังกัน chunk loop ไว้เป็น safety net เผื่อไฟล์ในอนาคตใหญ่กว่านี้
PROXY_CHUNK = 40 * 1024 * 1024

def _proxy_get(params, attempts=5, delay=3, timeout=60):
    qs = "&".join("%s=%s" % (k, urllib.parse.quote(str(v), safe="")) for k, v in params.items())
    url = PROXY["url"] + "?" + qs
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError) as e:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)

def _fetch_via_proxy(filename):
    base_params = {"token": PROXY["token"], "branch": PROXY["branch"], "file": filename}
    meta = json.loads(_proxy_get(dict(base_params, action="meta")))
    size = meta["size"]
    parts = []
    offset = 0
    while offset < size:
        b64 = _proxy_get(dict(base_params, offset=offset, length=PROXY_CHUNK))
        parts.append(base64.b64decode(b64))
        offset += PROXY_CHUNK
    return b"".join(parts)

def _proxy_exists(filename):
    try:
        meta = json.loads(_proxy_get({"token": PROXY["token"], "branch": PROXY["branch"],
                                       "file": filename, "action": "meta"}, attempts=3, delay=2))
        return "size" in meta
    except Exception:
        return False

def _file_exists(path):
    if PROXY:
        return _proxy_exists(os.path.basename(path))
    return os.path.exists(path)

# ---------- pure-python DBF reader (โครงเดียวกับ sopo_live_feeder.py ที่พิสูจน์แล้ว) ----------
def _read_file_retry(path, attempts=8, delay=3):
    if PROXY:
        return _fetch_via_proxy(os.path.basename(path))
    # Google Drive FileProvider บางครั้งคืน PermissionError/OSError(EDEADLK) ชั่วขณะ
    # (daemon เพิ่งเปิดไฟล์ครั้งแรก, หรือ Drive กำลัง cold-start) — อ่านทั้งไฟล์เข้า memory
    # เป็นก้อนเดียว แล้ว retry ทั้งก้อนถ้าพัง กันไม่ให้ deadlock โผล่กลางทางระหว่าง parse
    for attempt in range(attempts):
        try:
            with open(path, "rb") as f:
                return f.read()
        except (PermissionError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(delay)

def read_dbf(path, fields=None, encoding="cp874"):
    buf = _read_file_retry(path)
    nrec = struct.unpack("<I", buf[4:8])[0]
    hdrlen = struct.unpack("<H", buf[8:10])[0]
    reclen = struct.unpack("<H", buf[10:12])[0]
    fdefs = []
    nfields = (hdrlen - 33) // 32
    pos0 = 32
    for _ in range(nfields):
        fd = buf[pos0:pos0 + 32]
        pos0 += 32
        if fd[0:1] == b"\r":
            break
        name = fd[0:11].split(b"\x00")[0].decode("ascii", "replace")
        ftype = fd[11:12].decode("ascii", "replace")
        flen = fd[16]
        fdefs.append((name, ftype, flen))
    off = hdrlen
    for _ in range(nrec):
        rec = buf[off:off + reclen]
        off += reclen
        if len(rec) < reclen:
            break
        if rec[0:1] == b"*":
            continue
        row, pos = {}, 1
        for name, ftype, flen in fdefs:
            raw = rec[pos:pos + flen]
            pos += flen
            if fields is not None and name not in fields:
                continue
            if ftype in ("N", "F"):
                s = raw.strip()
                try:
                    row[name] = float(s) if s else 0.0
                except ValueError:
                    row[name] = 0.0
            elif ftype == "B":         # VFP double (8-byte LE)
                row[name] = struct.unpack("<d", raw)[0] if len(raw) == 8 else 0.0
            elif ftype == "I":         # VFP int32
                row[name] = struct.unpack("<i", raw)[0] if len(raw) == 4 else 0
            elif ftype == "Y":         # VFP currency
                row[name] = struct.unpack("<q", raw)[0] / 10000.0 if len(raw) == 8 else 0.0
            elif ftype == "D":
                s = raw.strip()
                if len(s) == 8 and s.isdigit():
                    try:
                        row[name] = datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                    except ValueError:
                        row[name] = None
                else:
                    row[name] = None
            else:
                row[name] = raw.decode(encoding, "replace").strip()
        yield row

# ---------- helpers ----------
def log(msg):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)

def load_config(path):
    cfg = {"WINDOW_DAYS": "45"}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip().upper()] = v.strip()
    if not cfg.get("BRANCH"):
        raise SystemExit("config missing BRANCH")
    if not cfg.get("SRC") and not cfg.get("PROXY_URL"):
        raise SystemExit("config missing SRC (or PROXY_URL for proxy mode)")
    return cfg

def state_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "007so_push")
    os.makedirs(d, exist_ok=True)
    return d

def d2s(d):
    return d.isoformat() if isinstance(d, datetime.date) else None

# ---------- Supabase REST (urllib, no deps) ----------
def sb_request(cfg, method, path, payload=None, prefer=None):
    url = cfg["SUPABASE_URL"].rstrip("/") + path
    headers = {
        "apikey": cfg["SUPABASE_KEY"],
        "Authorization": "Bearer " + cfg["SUPABASE_KEY"],
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            raise RuntimeError("HTTP %s %s %s -> %s %s" % (method, path[:60], e.code, e.reason, body))
        except (urllib.error.URLError, OSError) as e:
            if attempt == 3:
                raise
            log("  network retry %d (%s)" % (attempt, e))
            time.sleep(3 * attempt)

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

# ---------- v2: สต็อคคอยล์ ZZC -> coil_stock ----------
def read_dbf_fieldnames(path):
    buf = _read_file_retry(path)
    hdrlen = struct.unpack("<H", buf[8:10])[0]
    names = []
    pos0 = 32
    for _ in range((hdrlen - 33) // 32):
        fd = buf[pos0:pos0 + 32]
        pos0 += 32
        if fd[0:1] == b"\r":
            break
        names.append(fd[0:11].split(b"\x00")[0].decode("ascii", "replace"))
    return names

def push_stock(cfg, branch, src, now_iso, dry):
    # หาไฟล์ master สต็อคของ Express + ฟิลด์ยอดคงเหลือ (ตรวจชื่อฟิลด์จริงตอนรัน — ต่างเวอร์ชันชื่อไม่เหมือนกัน)
    path = None
    for c in ("STMAS.DBF", "ICMAS.DBF", "STOCK.DBF"):
        p = os.path.join(src, c)
        if _file_exists(p):
            path = p
            break
    if not path:
        log("STOCK: no stock DBF found in %s" % src)
        return
    fields = read_dbf_fieldnames(path)
    balf = next((b for b in ("TOTBAL", "QTYBAL", "BALQTY", "ONHAND", "STKBAL", "NETBAL") if b in fields), None)
    if "STKCOD" not in fields or not balf:
        log("STOCK: fields not recognized in %s -> %s" % (os.path.basename(path), ",".join(fields)[:400]))
        return
    rows = []
    for r in read_dbf(path, fields={"STKCOD", balf}):
        stk = (r.get("STKCOD") or "").strip()
        if not stk.startswith("ZZC"):
            continue
        rows.append({"branch": branch, "coil_sku": stk,
                     "totbal": round(float(r.get(balf) or 0), 2), "synced_at": now_iso})
    sf = os.path.join(state_dir(), "stock_%s.json" % branch)
    fp = hashlib.md5(json.dumps(sorted([(x["coil_sku"], x["totbal"]) for x in rows]),
                                ensure_ascii=False).encode()).hexdigest()
    try:
        old = json.load(open(sf, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if old.get("fp") == fp:
        return
    log("STOCK: %d ZZC rows from %s field %s%s" % (len(rows), os.path.basename(path), balf, " (DRY)" if dry else ""))
    if dry:
        return
    sb_request(cfg, "DELETE", "/rest/v1/coil_stock?branch=eq.%s" % branch)
    for batch in chunks(rows, 500):
        sb_request(cfg, "POST", "/rest/v1/coil_stock?on_conflict=branch,coil_sku",
                   batch, prefer="resolution=merge-duplicates,return=minimal")
    json.dump({"fp": fp}, open(sf, "w", encoding="utf-8"))
    log("STOCK: pushed %d rows OK" % len(rows))

# ---------- main ----------
def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = load_config(sys.argv[1])
    dry = ("--dry" in sys.argv) or not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_KEY")
    branch = cfg["BRANCH"]
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL"):
        PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=branch)
    window = int(cfg["WINDOW_DAYS"])
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=window)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ---- lock กันรันซ้อน ----
    lock = os.path.join(state_dir(), "lock_%s.txt" % branch)
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 170:
        log("SKIP: previous run still active")
        return
    open(lock, "w").write(str(os.getpid()))

    try:
        if not _file_exists(os.path.join(src, "OESO.DBF")):
            raise SystemExit("source not found: " + (PROXY.get("url") or src))

        # ---- v2: สต็อคคอยล์ ZZC (พังได้โดยไม่กระทบ SO push) ----
        try:
            push_stock(cfg, branch, src, now_iso, dry)
        except Exception as e:
            log("STOCK: error %s" % e)

        # ---- อ่านชื่อลูกค้า ----
        names = {}
        for r in read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "PRENAM", "CUSNAM"}):
            names[r.get("CUSCOD", "")] = (r.get("PRENAM", "") + " " + r.get("CUSNAM", "")).strip()

        # ---- อ่าน SO header ในหน้าต่างเวลา ----
        heads = {}
        for r in read_dbf(os.path.join(src, "OESO.DBF"),
                          fields={"SONUM", "SODAT", "DLVDAT", "CUSCOD", "SLMCOD",
                                  "NETAMT", "DOCSTAT", "YOUREF"}):
            sod = r.get("SODAT")
            if not sod or sod < cutoff:
                continue
            so = (r.get("SONUM") or "").strip()
            if not so:
                continue
            cus = (r.get("CUSCOD") or "").strip()
            heads[so] = {
                "branch": branch, "sonum": so,
                "sodat": d2s(sod), "dlvdat": d2s(r.get("DLVDAT")),
                "cuscod": cus, "cusnam": names.get(cus, cus) or cus,
                "slmcod": (r.get("SLMCOD") or "").strip(),
                "netamt": round(float(r.get("NETAMT") or 0), 2),
                "docstat": (r.get("DOCSTAT") or "").strip(),
                "youref": (r.get("YOUREF") or "").strip(),
            }

        # ---- อ่านรายการ (seq = ลำดับแถวต่อเอกสารตามตำแหน่งไฟล์ — convention เดียวกับ gen_sopo) ----
        items = {}
        for r in read_dbf(os.path.join(src, "OESOIT.DBF"),
                          fields={"SONUM", "STKCOD", "STKDES", "ORDQTY", "REMQTY", "TQUCOD"}):
            so = (r.get("SONUM") or "").strip()
            if so not in heads:
                continue
            L = items.setdefault(so, [])
            L.append({
                "branch": branch, "sonum": so, "seq": len(L) + 1,
                "stkcod": (r.get("STKCOD") or "").strip(),
                "stkdes": (r.get("STKDES") or "").strip(),
                "ordqty": round(float(r.get("ORDQTY") or 0), 2),
                "remqty": round(float(r.get("REMQTY") or 0), 2),
                "unit": (r.get("TQUCOD") or "").strip(),
            })

        # ---- delta เทียบ state ----
        state_file = os.path.join(state_dir(), "state_%s.json" % branch)
        try:
            state = json.load(open(state_file, encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        changed = []
        for so, h in heads.items():
            fp = hashlib.md5(json.dumps([h, items.get(so, [])],
                                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if state.get(so) != fp:
                changed.append((so, fp))
        log("%s: %d SO in window, %d changed%s" %
            (branch, len(heads), len(changed), " (DRY)" if dry else ""))

        if not changed:
            return
        if dry:
            for so, _ in changed[:10]:
                log("  would push %s %s (%d items)" %
                    (so, heads[so]["cusnam"][:24], len(items.get(so, []))))
            if len(changed) > 10:
                log("  ... +%d more" % (len(changed) - 10))
            return

        # ---- push: header upsert เป็น batch ----
        for batch in chunks([dict(heads[so], synced_at=now_iso) for so, _ in changed], 200):
            sb_request(cfg, "POST", "/rest/v1/so_live?on_conflict=branch,sonum",
                       batch, prefer="resolution=merge-duplicates,return=minimal")

        # ---- push: items — ลบชุดเก่าของ SO ที่เปลี่ยน (กันแถวที่ถูกลบใน Express ค้าง) แล้ว insert ใหม่ ----
        for batch in chunks([so for so, _ in changed], 40):
            solist = ",".join('"%s"' % s for s in batch)
            sb_request(cfg, "DELETE",
                       "/rest/v1/so_item_live?branch=eq.%s&sonum=in.(%s)" % (branch, solist))
        all_items = [it for so, _ in changed for it in items.get(so, [])]
        for batch in chunks([dict(it, synced_at=now_iso) for it in all_items], 500):
            sb_request(cfg, "POST", "/rest/v1/so_item_live?on_conflict=branch,sonum,seq",
                       batch, prefer="resolution=merge-duplicates,return=minimal")

        # ---- สำเร็จ -> บันทึก state ----
        for so, fp in changed:
            state[so] = fp
        state = {so: fp for so, fp in state.items() if so in heads or len(state) < 5000}
        json.dump(state, open(state_file, "w", encoding="utf-8"))
        log("pushed %d SO (%d item rows) OK" % (len(changed), len(all_items)))
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass

if __name__ == "__main__":
    main()
