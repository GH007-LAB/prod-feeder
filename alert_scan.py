# -*- coding: utf-8 -*-
"""สแกน feeder.log หาความผิดปกติ → เขียน ops_alert แล้วเรียก dispatcher บน hr-app ให้ส่ง LINE

รันท้ายทุกรอบของ run_all.sh:
  1. อ่าน log เฉพาะส่วนที่งอกใหม่ (จำ offset ไว้ที่ ~/007so_push/.alert_scan_pos)
  2. บรรทัดที่มี VERIFY-FAIL / SMOKE-FAIL / STORAGE-BACKUP-FAIL / ERROR
     → insert ตาราง ops_alert (ref = hash บรรทัด — ซ้ำแล้วข้าม)
  3. เรียก ALERTS_URL (hr-app /api/cron/alerts, Bearer = service key)
     → ฝั่งนั้นเช็คข้อมูลค้าง/ยอดเงินไม่ตรงเพิ่ม แล้วส่ง LINE แอดมิน

env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ALERTS_URL
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
LOG_PATH = os.path.join(HOME, "007so_push", "feeder.log")
POS_PATH = os.path.join(HOME, "007so_push", ".alert_scan_pos")

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ALERTS_URL = os.environ.get("ALERTS_URL", "")

# บรรทัดที่ถือว่าผิดปกติ (คำที่สคริปต์ทุกตัวใช้พิมพ์ตอนพัง)
PATTERN = re.compile(r"(VERIFY-FAIL|SMOKE-FAIL|STORAGE-BACKUP-FAIL|ERROR)")

# จำกัดการเตือน "ต่อเรื่อง" (CTO สั่ง 14 ก.ย. 69): เรื่องเดิมเตือนไม่เกิน 3 ครั้ง
# ครั้งที่ 3 แนบ "รอการแก้ไข" แล้วเงียบเฉพาะเรื่องนั้น (เรื่องอื่นเตือนตามปกติ)
# เรื่อง = หัวข้อ error (เช่น TIKTOK-QUICK / LINEUP-MAIL / VERIFY-FAIL BK)
# นับหนึ่งใหม่เมื่อเรื่องนั้นเงียบต่อเนื่องเกิน 6 ชม. (= ถือเป็นเหตุการณ์ใหม่)
# เรื่องที่ถูกเงียบยังถูกบันทึกลง ops_alert ครบ (notified_at ใส่ไว้เลย) — ตามดูย้อนหลังได้
TOPIC_STATE = os.path.join(HOME, "007so_push", ".alert_topic_counts.json")
TOPIC_LIMIT = 3
TOPIC_RESET_SEC = 6 * 3600


def topic_of(line, kind):
    """สกัด "เรื่อง" จากบรรทัด log — ตัดตัวเลข (วันเวลา) ออกให้เหลือหัวข้อคงที่"""
    if kind == "ERROR":
        m = re.search(r"ERROR:?\s+([A-Za-zก-๛][A-Za-zก-๛0-9_-]*)", line)
        t = m.group(1) if m else line.strip()[:30]
    else:
        t = kind
        # รองรับทั้ง "VERIFY-FAIL BK" และ "VERIFY-FAIL: BK ..."
        m = re.search(re.escape(kind) + r"[:\s]+([A-Z]{2,4})\b", line)
        if m:
            t += " " + m.group(1)
    t = re.sub(r"\d+", "", t).strip("-_ ")
    return (t or kind)[:40]


def recovered_in(line, topic):
    """บรรทัด (ที่ไม่ใช่ error) นี้เป็นหลักฐานว่า "เรื่อง" นั้นกลับมาทำงานปกติไหม
    - VERIFY-FAIL <BR>  ฟื้นเมื่อเจอ "VERIFY: <BR> ผ่านครบ"
    - X-FAIL            ฟื้นเมื่อเจอ "X-OK"
    - เรื่องจาก ERROR:   ฟื้นเมื่อเจอหัวข้อเดียวกันในบรรทัดปกติ (สคริปต์พิมพ์ตอนสำเร็จ)
    เรื่องที่สคริปต์ไม่พิมพ์ตอนสำเร็จ = ไม่มีทางประกาศฟื้น (เงียบดีกว่าประกาศมั่ว)"""
    if topic.startswith("VERIFY-FAIL"):
        br = topic[len("VERIFY-FAIL"):].strip()
        return "VERIFY:" in line and "ผ่านครบ" in line and (not br or (" %s " % br) in line + " ")
    if topic.endswith("-FAIL"):
        return (topic[:-5] + "-OK") in line
    return topic in line


def load_topic_state():
    try:
        return json.load(open(TOPIC_STATE))
    except Exception:
        return {}


def save_topic_state(state):
    try:
        json.dump(state, open(TOPIC_STATE, "w"), ensure_ascii=False)
    except Exception:
        pass


def log(msg):
    print("%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def read_new_lines():
    if not os.path.exists(LOG_PATH):
        return []
    size = os.path.getsize(LOG_PATH)
    pos = 0
    if os.path.exists(POS_PATH):
        try:
            pos = int(open(POS_PATH).read().strip() or 0)
        except Exception:
            pos = 0
    if pos > size:  # log ถูกตัด/สร้างใหม่
        pos = 0
    with open(LOG_PATH, "rb") as f:
        f.seek(pos)
        chunk = f.read()
        new_pos = f.tell()
    open(POS_PATH, "w").write(str(new_pos))
    return chunk.decode("utf-8", errors="replace").splitlines()


def push_alerts(rows):
    req = urllib.request.Request(
        URL + "/rest/v1/ops_alert?on_conflict=ref",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            "apikey": KEY,
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        },
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    if not URL or not KEY:
        log("ALERTS: ข้าม — env ไม่ครบ")
        return 0

    all_lines = read_new_lines()
    if all_lines:
        state = load_topic_state()
        now_ts = time.time()
        rows = []
        muted = 0
        errs = 0
        # ไล่ตามลำดับเวลา: บรรทัด error = นับ/เตือน · บรรทัดปกติ = เช็คว่าเรื่องที่
        # กำลัง error อยู่กลับมาสำเร็จหรือยัง (CTO สั่ง 14 ก.ย. 69: ฟื้นแล้วต้องแจ้ง)
        for l in all_lines:
            m = PATTERN.search(l)
            if m:
                if errs >= 50:  # กันเหตุพังถล่ม log — รอบละไม่เกิน 50 เรื่อง
                    continue
                errs += 1
                kind = m.group(1)
                ref = "log-" + hashlib.sha1(l.encode()).hexdigest()[:16]
                msg = l.strip()[:300]

                # นับต่อเรื่อง: เกิน 3 ครั้ง = บันทึกอย่างเดียว ไม่ยิง LINE
                topic = topic_of(l, kind)
                st = state.get(topic)
                if st and now_ts - st.get("last", 0) > TOPIC_RESET_SEC:
                    st = None  # เงียบมานานพอ = เหตุการณ์ใหม่ นับหนึ่งใหม่
                n = (st.get("n", 0) if st else 0) + 1
                state[topic] = {"n": n, "last": now_ts}

                row = {"ref": ref, "kind": kind, "message": msg}
                if n == TOPIC_LIMIT:
                    row["message"] = (msg[:210] +
                                      " ⛔ เตือนครบ 3 ครั้ง — รอการแก้ไข "
                                      "(เรื่องนี้จะเงียบจนกว่าจะหายเกิน 6 ชม.)")
                elif n > TOPIC_LIMIT:
                    # ใส่ notified_at ไว้เลย -> dispatcher ไม่ส่ง LINE แต่ประวัติครบ
                    row["notified_at"] = datetime.now(timezone.utc).isoformat()
                    muted += 1
                rows.append(row)
            else:
                # บรรทัดปกติ: เรื่องไหนที่ค้าง error อยู่แล้วมีหลักฐานสำเร็จ -> ประกาศฟื้น
                for topic in list(state.keys()):
                    st = state[topic]
                    if st.get("n", 0) < 1 or not recovered_in(l, topic):
                        continue
                    if now_ts - st.get("last", 0) <= TOPIC_RESET_SEC:
                        ref = "recover-" + hashlib.sha1(
                            (topic + l).encode()).hexdigest()[:16]
                        rows.append({
                            "ref": ref, "kind": "RECOVERED",
                            "message": "✅ %s กลับมาทำงานปกติแล้ว (หลังแจ้งเตือน %d ครั้ง)"
                                       % (topic, min(st.get("n", 0), TOPIC_LIMIT)),
                        })
                    del state[topic]  # เคลียร์ตัวนับ — พังใหม่ = นับหนึ่งใหม่
        save_topic_state(state)
        if rows:
            try:
                push_alerts(rows)
                log("ALERTS: บันทึก %d เรื่อง%s" % (len(rows),
                    " (เงียบ %d เรื่องที่เตือนครบ 3 แล้ว)" % muted if muted else ""))
            except Exception as e:
                log("ALERTS: เขียน ops_alert ไม่สำเร็จ: %s" % e)

    # เรียก dispatcher ให้เช็คเพิ่ม (ข้อมูลค้าง/ยอดเงิน) + ส่ง LINE ถ้ามีเรื่องค้าง
    if ALERTS_URL:
        try:
            req = urllib.request.Request(
                ALERTS_URL, headers={"Authorization": "Bearer " + KEY}
            )
            body = urllib.request.urlopen(req, timeout=30).read().decode()
            sent = json.loads(body).get("sent", 0)
            if sent:
                log("ALERTS: ส่ง LINE แล้ว %s เรื่อง" % sent)
        except Exception as e:
            log("ALERTS: เรียก dispatcher ไม่สำเร็จ: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
