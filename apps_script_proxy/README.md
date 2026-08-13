# feeder Drive proxy (Apps Script)

`Code.gs` ในโฟลเดอร์นี้คือ source ของ Web App ที่ deploy ไว้บน script.google.com
ให้ `so_push.py` เรียกดึงไฟล์ DBF จาก `All_on_Cloud/AutoExport` ผ่าน HTTPS
แทนการอ่าน local Google Drive mount ตรงๆ

## ทำไมต้องมีตัวนี้

launchd และ cron (background daemon) อ่าน Google Drive FileProvider mount
ไม่ได้เลย ไม่ว่าจะให้ Full Disk Access ยังไง — macOS ผูก FileProvider ไว้กับ
active GUI session เท่านั้น ลองมาหมดแล้ว (FDA python3/zsh, FDA ตัว caller app,
restart เครื่องทั้งเครื่อง, เปลี่ยน launchd เป็น cron) ไม่มีทางไหนแก้ได้เลย —
มีแค่ตอนรันผ่าน session ที่ล็อกอินอยู่ (เช่น Terminal) เท่านั้นที่อ่านได้ปกติ

วิธีนี้เลี่ยงปัญหาทั้งหมดเพราะเป็น plain HTTPS request ไม่พึ่ง FileProvider เลย

## Deploy ครั้งแรก (หรือ deploy ใหม่บนเครื่อง/บัญชีอื่น)

ต้องเป็นบัญชี Google ที่**เห็นโฟลเดอร์ `All_on_Cloud` อยู่แล้ว** (เช่น cto@007metals.com)

1. เปิด https://script.google.com → **New project**
2. ลบโค้ด default ทิ้ง → paste เนื้อหาจาก `Code.gs`
3. แก้บรรทัดใน `setupToken()` ให้ใส่สตริงสุ่มยาวๆของตัวเอง
4. เลือกฟังก์ชัน `setupToken` จาก dropdown บน toolbar → กด **Run** → อนุมัติ authorization ที่เด้งขึ้น (ต้องกดเองใน browser ของ user เจ้าของบัญชี — automation ทำแทนไม่ได้)
5. รันเสร็จแล้ว **ลบ token ที่เขียนไว้ในซอร์สโค้ดออก** (เก็บอยู่ใน Script Properties แล้ว ไม่ต้องเหลือใน source — commit กลับมาที่ repo แบบไม่มี token จริง)
6. **Deploy → New deployment** → เลือก type **Web app**:
   - Execute as: **Me** (บัญชีเจ้าของ)
   - Who has access: **Anyone** (ป้องกันด้วย token ในโค้ดแทน ไม่ใช่ OAuth)
7. กด **Deploy** → อนุมัติ authorization รอบที่ 2 (คนละ scope จากตอน setupToken)
8. copy **Web app URL** (ลงท้าย `/exec`) + token จากข้อ 3

## ตั้งค่าใน `feeder.env`

```
PROXY_URL=<Web app URL จากข้อ 8>
PROXY_TOKEN=<token จากข้อ 3>
```

`run_all.sh` จะสลับไปใช้ proxy โดยอัตโนมัติถ้าเห็น `PROXY_URL` ตั้งไว้ — ถ้าไม่ตั้ง
จะ fallback กลับไปอ่าน local mount (`DRIVE_ROOT`) แบบเดิม

## ทดสอบว่า deploy ถูกต้อง

```bash
curl "https://script.google.com/macros/s/XXXXX/exec?token=YOUR_TOKEN&branch=SKN&file=OESO.DBF&action=meta"
# ควรได้ {"size": <เลขไบต์>}
```

## API

- `?token=...&branch=SKN&file=OESO.DBF&action=meta` → `{"size": N}`
- `?token=...&branch=SKN&file=OESO.DBF` → base64 ของไฟล์ (offset=0, length=ค่า default `CHUNK_BYTES`)
- `?token=...&branch=SKN&file=OESO.DBF&offset=N&length=M` → base64 ของช่วง byte [N, N+M)
- `?token=...&fileId=<Drive file id>[&action=meta|&offset=N&length=M]` → เหมือนข้างบนแต่ดึงไฟล์ตรงด้วย
  Drive file id แทน branch+file — ใช้กับไฟล์ที่ไม่ได้อยู่ใต้ `AutoExport/{branch}` เช่น
  `report_scores.csv` ของ Finny (ต้องแชร์ไฟล์นั้นให้บัญชีที่รัน Apps Script นี้เห็นด้วย)

## Gotcha

- Apps Script โหลดไฟล์เต็มจาก Drive เข้า memory ทุก request (ไม่มี state ข้าม
  invocation) — **แบ่ง chunk เล็กมีแต่เสีย** (ยิงซ้ำหลายรอบ โหลดไฟล์เดิมซ้ำทุกครั้ง
  ช้าลงมาก) `CHUNK_BYTES` ตั้งไว้ใหญ่พอให้ไฟล์ปัจจุบันจบในคำขอเดียวเสมอ
- รอบแรกหลัง Apps Script ไม่ถูกเรียกมาสักพัก (cold start) จะช้ากว่าปกติ (เคยเห็น
  ~1-5 นาทีสำหรับไฟล์ใหญ่สุด ~22MB) รอบถัดไปที่ "อุ่น" แล้วจะเร็วกว่ามาก (~1-3 นาที)
- ห้ามรัน `so_push.py` ซ้อนกันสำหรับ branch เดียวกัน (เช่น รันมือพร้อม ๆ กับที่
  launchd กำลังรันอยู่) จะไปแย่ง lock/state file เดียวกัน — เช็ค `ps aux | grep
  so_push.py` ก่อนรันมือเสมอ
