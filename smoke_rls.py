#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_rls.py — ตรวจ "สิทธิ์การเห็นข้อมูล" ด้วยตัวตนจริง 2 ระดับ ทุกชั่วโมง
================================================================
ทำไมต้องมี: บั๊กสิทธิ์ 2 ครั้ง (hr_kpi เห็นข้ามคน / policy employees เปิดกว้าง) หลุดรอด
เพราะทดสอบด้วยบัญชีแอดมินอย่างเดียว — ตัวนี้ยิง PostgREST ตรงแบบผู้ไม่หวังดีจริง:

  ระดับ 1 "anon"        = คนนอกถือ anon key เปล่า ๆ (key อยู่ใน JS ทุกหน้า)
  ระดับ 2 "auth-unlinked" = ล็อกอินสำเร็จแต่ไม่ผูกทะเบียนพนักงาน (แบบบัญชีหลงเข้ามา)

ทุกข้อ assert ว่า "ต้องไม่เห็น/ต้องทำไม่ได้" — ข้อไหนหลุด = พิมพ์ SMOKE-FAIL ลง log
(grep 'SMOKE-FAIL' ~/007so_push/feeder.log) · ผ่านหมด = SMOKE-OK 1 บรรทัด

env ที่ใช้ (จาก feeder.env): SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY,
SMOKE_EMAIL, SMOKE_PASSWORD, SMOKE_EVERY_MIN (คุมจังหวะเหมือน verify)
บัญชีบอทถูกสร้างอัตโนมัติครั้งแรก (email_confirm ผ่าน admin API) — ไม่อยู่ใน allowlist ใด
จึงเข้าแอพจริงไม่ได้อยู่แล้ว มีไว้ขอ token มายิงทดสอบเท่านั้น
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
SERVICE = os.environ.get("SUPABASE_SERVICE_KEY", "")
EMAIL = os.environ.get("SMOKE_EMAIL", "")
PASSWORD = os.environ.get("SMOKE_PASSWORD", "")
EVERY_MIN = int(os.environ.get("SMOKE_EVERY_MIN") or 60)
STAMP = os.path.expanduser("~/007so_push/.smoke_last_run")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def throttle_ok():
    if EVERY_MIN <= 0:
        return True
    try:
        if time.time() - os.path.getmtime(STAMP) < EVERY_MIN * 60 - 30:
            return False
    except OSError:
        pass
    return True


def req(method, path, token, body=None, key=None):
    """คืน (status, parsed_json_or_text) — ไม่ raise เพื่อให้ assert สถานะได้ตรง ๆ"""
    headers = {"apikey": key or ANON, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as res:
            raw = res.read().decode()
            try:
                return res.status, json.loads(raw) if raw else None
            except ValueError:
                return res.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else None
        except ValueError:
            return e.code, raw


def ensure_bot():
    """สร้างบัญชีบอทครั้งแรก (idempotent) แล้วคืน access_token ของมัน"""
    st, body = req("POST", "/auth/v1/token?grant_type=password", None,
                   {"email": EMAIL, "password": PASSWORD})
    if st == 200 and isinstance(body, dict) and body.get("access_token"):
        return body["access_token"]
    # ล็อกอินไม่ได้ -> สร้างใหม่ด้วย service role แล้วลองอีกครั้ง
    req("POST", "/auth/v1/admin/users", SERVICE,
        {"email": EMAIL, "password": PASSWORD, "email_confirm": True}, key=SERVICE)
    st, body = req("POST", "/auth/v1/token?grant_type=password", None,
                   {"email": EMAIL, "password": PASSWORD})
    if st == 200 and isinstance(body, dict) and body.get("access_token"):
        return body["access_token"]
    raise RuntimeError(f"ขอ token บอทไม่ได้ (HTTP {st})")


FAILS = []


def expect_rows(label, st, body, want_max=0):
    """คาดว่า 'ไม่เห็นข้อมูล' — ยอมรับ [] หรือ 401/403/404; เห็นแถว = FAIL"""
    if isinstance(body, list) and len(body) > want_max:
        FAILS.append(f"{label}: เห็น {len(body)} แถว (ต้องเห็น {want_max})")
    elif st == 200 and not isinstance(body, list):
        FAILS.append(f"{label}: ตอบ 200 รูปแบบแปลก: {str(body)[:80]}")


def expect_denied(label, st, body):
    """คาดว่า 'ทำไม่ได้' — 2xx = FAIL"""
    if 200 <= st < 300:
        FAILS.append(f"{label}: ทำสำเร็จ (HTTP {st}) ทั้งที่ต้องถูกปฏิเสธ")


def main():
    if not (URL and ANON and SERVICE and EMAIL and PASSWORD):
        log("SMOKE: ข้าม — env ไม่ครบ (ต้องมี SUPABASE_ANON_KEY/SMOKE_EMAIL/SMOKE_PASSWORD)")
        return
    if not throttle_ok():
        return
    open(STAMP, "w").close()

    # ---------- ระดับ 1: anon (ไม่ล็อกอิน) ----------
    for t, label in [
        ("employees?select=nickname&limit=5", "anon อ่านทะเบียนพนักงาน"),
        ("hr_payslip?select=id&limit=1", "anon อ่านสลิปเงินเดือน"),
        ("hr_timelog?select=id&limit=1", "anon อ่านการลงเวลา"),
        ("hr_leave?select=id&limit=1", "anon อ่านใบลา"),
        ("sopo_branch_month?select=branch&limit=1", "anon อ่านยอดขาย SOPO"),
        ("payouts?select=payout_id&limit=1", "anon อ่านเงินเข้าร้านเมทัลชีท"),
    ]:
        st, body = req("GET", "/rest/v1/" + t, None)
        expect_rows(label, st, body)
    st, body = req("POST", "/rest/v1/sopo_branch_now", None, {"branch": "XX"})
    expect_denied("anon เขียนตาราง SOPO", st, body)
    st, body = req("POST", "/rest/v1/hr_leave", None,
                   {"employee_id": 1, "leave_type": "vacation", "date_from": "2099-01-01",
                    "date_to": "2099-01-01", "days": 1, "status": "approved"})
    expect_denied("anon สร้างใบลา", st, body)

    # ---------- ระดับ 2: ล็อกอินแล้วแต่ไม่ผูกทะเบียนพนักงาน ----------
    tok = ensure_bot()
    for t, label in [
        ("employees?select=nickname,email,line_id&limit=5", "auth-unlinked อ่านทะเบียนพนักงาน"),
        ("hr_payslip?select=id,gross&limit=1", "auth-unlinked อ่านสลิปเงินเดือน"),
        ("hr_leave?select=id,reason&limit=1", "auth-unlinked อ่านใบลา"),
        ("hr_kpi?select=employee_id,score&limit=1", "auth-unlinked อ่าน KPI"),
        ("hr_discipline?select=id&limit=1", "auth-unlinked อ่านประวัติวินัย"),
        ("sopo_branch_month?select=branch&limit=1", "auth-unlinked อ่านยอดขาย SOPO"),
        ("hr_staff_ext?select=phone&limit=1", "auth-unlinked อ่านเบอร์โทร"),
    ]:
        st, body = req("GET", "/rest/v1/" + t, tok)
        expect_rows(label, st, body)
    # คอลัมน์พิกัด GPS ต้องถูกปิดระดับคอลัมน์ (H4) — คาดว่า error ไม่ใช่ []
    st, body = req("GET", "/rest/v1/hr_timelog?select=lat,lng&limit=1", tok)
    expect_denied("auth-unlinked อ่านพิกัด GPS (คอลัมน์ต้องถูกปิด)", st, body)
    # ปลอมใบลา approved (C2) — ต้องถูกปฏิเสธ
    st, body = req("POST", "/rest/v1/hr_leave", tok,
                   {"employee_id": 1, "leave_type": "vacation", "date_from": "2099-01-01",
                    "date_to": "2099-01-01", "days": 1, "status": "approved"})
    expect_denied("auth-unlinked ปลอมใบลา approved", st, body)
    if 200 <= st < 300:  # กันเหตุ: ถ้าหลุดจริง ลบทิ้งทันทีด้วย service role
        req("DELETE", "/rest/v1/hr_leave?date_from=eq.2099-01-01", SERVICE, key=SERVICE)

    if FAILS:
        for f in FAILS:
            log("SMOKE-FAIL: " + f)
        sys.exit(1)
    log(f"SMOKE-OK: สิทธิ์ผ่านครบ (anon 8 ข้อ + auth-unlinked 9 ข้อ)")


if __name__ == "__main__":
    main()
