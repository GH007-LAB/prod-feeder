# -*- coding: utf-8 -*-
"""
007 Metals - Finny sync (Phase 5 ของ SOPO rebuild)
ดึง report_scores.csv (Google Drive, คนละที่จาก AutoExport) ผ่าน Apps Script
proxy (fileId mode) -> upsert เข้า finny_daily ไม่ใช่ข้อมูลจาก ERP/DBF เหมือน
so_push.py/sopo_month.py — เป็นคะแนนตรวจความแม่นยำผู้คีย์บัญชีรายวัน จากคนละระบบ

รันครั้งเดียวต่อรอบ (ไม่ต้องต่อ branch เหมือน so_push.py — ไฟล์เดียวใช้ร่วมทุกสาขา)
usage: python finny_sync.py <config_file> [--dry]
"""
import sys, os, csv, io, json, base64, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S  # reuse PROXY / _proxy_get / sb_request / load_config / chunks / log

FINNY_FILE_ID = "14M32nx8Taky-DWTdlMpJAh6-QQJZNhtk"  # report_scores.csv (Google Drive)
R70_CUTOVER = datetime.date(2026, 7, 13)  # >= วันนี้ สเกลเต็ม 4 แต้ม (ก่อนหน้าเต็ม 3) — จาก build_finny.py


def fetch_finny_csv():
    base = {"token": S.PROXY["token"], "fileId": FINNY_FILE_ID}
    meta = json.loads(S._proxy_get(dict(base, action="meta")))
    size = meta["size"]
    parts = []
    offset = 0
    while offset < size:
        b64 = S._proxy_get(dict(base, offset=offset, length=S.PROXY_CHUNK))
        parts.append(base64.b64decode(b64))
        offset += S.PROXY_CHUNK
    return b"".join(parts)


def be_to_iso(s):
    """'2569-07-04' (พ.ศ.) -> '2026-07-04' (ค.ศ.)"""
    y, m, d = s.strip().split("-")
    return datetime.date(int(y) - 543, int(m), int(d)).isoformat()


def parse_num(s):
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    dry = ("--dry" in sys.argv) or not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_KEY")
    if cfg.get("PROXY_URL"):
        S.PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=cfg["BRANCH"])
    if not S.PROXY:
        S.log("FINNY: no PROXY_URL configured -> skip (ต้องใช้ Drive proxy, ไม่รองรับ local mount)")
        return

    try:
        raw = fetch_finny_csv()
    except Exception as e:
        S.log("FINNY: fetch error %s" % e)
        return

    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = {}
    header_skipped = False
    for r in reader:
        if not r or not r[0].strip():
            continue
        if not header_skipped:
            header_skipped = True
            continue
        if len(r) < 11:
            continue
        try:
            date_iso = be_to_iso(r[0])
        except Exception:
            continue
        branch = r[1].strip()
        if branch not in ("BK", "PPS", "SKN"):
            continue
        preparer = r[2].strip()
        points = parse_num(r[3]) or 0.0
        points_max = 4.0 if datetime.date.fromisoformat(date_iso) >= R70_CUTOVER else 3.0
        score = round(min(points / points_max * 100, 100)) if points_max else 0
        note = r[11].strip() if len(r) > 11 else ""
        rows[(branch, date_iso)] = {
            "branch": branch,
            "date": date_iso,
            "preparer_name": preparer,
            "points": points,
            "points_max": points_max,
            "score": score,
            "pct_numeric": parse_num(r[4]),
            "pct_column": parse_num(r[5]),
            "pct_bank_evidence": parse_num(r[6]),
            "pct_billing": parse_num(r[7]),
            "pct_ontime": parse_num(r[8]),
            "pct_no_fix": parse_num(r[9]),
            "level": r[10].strip(),
            "note": note[:500],
        }

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    batch = [dict(v, synced_at=now_iso) for v in rows.values()]
    S.log("FINNY: %d rows parsed%s" % (len(batch), " (DRY)" if dry else ""))
    if dry or not batch:
        return

    for chunk in S.chunks(batch, 200):
        S.sb_request(cfg, "POST", "/rest/v1/finny_daily?on_conflict=branch,date",
                     chunk, prefer="resolution=merge-duplicates,return=minimal")
    S.log("FINNY: pushed OK")


if __name__ == "__main__":
    main()
