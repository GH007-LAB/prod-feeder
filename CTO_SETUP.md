# ✅ CTO Mac mini — Checklist ย้ายครั้งเดียว (ระบบผลิต production)

ทำตามลำดับ ครั้งเดียวจบ · ส่วนที่ต้องทำ "ที่เครื่องสาขา" แยกไว้ท้ายสุด

---

## 0) เตรียมเครื่อง CTO (เช็คว่ามี)
```bash
python3 --version   # ต้องมี (มากับ macOS)
git --version       # ถ้าไม่มี จะเด้งให้ติดตั้ง Xcode CLT — กด Install
```

## 1) Google Drive Desktop (บัญชี cto@007metals.com)
- [ ] เปิดแอป Google Drive → ล็อกอิน **cto@007metals.com**
- [ ] เว็บ drive.google.com (cto@) → **Shared with me** → โฟลเดอร์ `All_on_Cloud` → คลิกขวา → **Add shortcut to Drive** → My Drive
- [ ] แอป Google Drive (เมนูบาร์) → Settings → โฟลเดอร์ `All_on_Cloud/AutoExport` → **Available offline** (สำคัญ! ไม่งั้นอ่าน DBF ช้า)
- [ ] ยืนยันเห็นไฟล์:
```bash
ls ~/Library/CloudStorage/GoogleDrive-cto@007metals.com/"My Drive"/All_on_Cloud/AutoExport/SKN
# ต้องเห็น OESO.DBF OESOIT.DBF ARMAS.DBF STMAS.DBF last_sync.txt
```

## 2) ติดตั้ง feeder (SO + รายการ + คอยล์ → Supabase กลาง ทุก 10 นาที)
- [ ] clone:
```bash
git clone https://github.com/GH007-LAB/prod-feeder.git ~/prod-feeder && cd ~/prod-feeder
```
- [ ] `cp feeder.env.example feeder.env` แล้วแก้ `PROXY_URL`/`PROXY_TOKEN` (หรือ `DRIVE_ROOT=` เป็น path จากข้อ 1 ถ้าใช้ local mount แทน — ลงท้าย `.../All_on_Cloud/AutoExport`) — `feeder.env` ไม่ commit
- [ ] ทดสอบ 1 รอบ + ดู log:
```bash
./run_all.sh && tail -20 ~/007so_push/feeder.log
# ควรเห็น: "SKN: N SO in window, M changed ... pushed ... OK" และ "STOCK: pushed ... rows OK"
```
- [ ] ติดตั้งรันอัตโนมัติทุก 10 นาที:
```bash
./install.sh
```

## 3) แจ้ง Claude (เครื่องนี้/เครื่องหลัก) ให้ verify
บอกว่า "feeder เครื่อง CTO รันแล้ว ช่วยเช็ก so_live/coil_stock ในกลางว่ามี synced_at ใหม่" → Claude เฝ้าดูให้

---

## 🖥️ ที่เครื่องสาขา 3 เครื่อง (SKN/BK/PPS) — ทำหลัง feeder ข้อ 2 ทำงานแล้ว
- [ ] เปิด **Task Scheduler** → ปิด/Disable task **`007SoPush`** (เดิม push ไป Supabase เก่า dbbhg — ไม่ใช้แล้ว กัน push ซ้ำซ้อน)
- [ ] ⚠️ **ห้ามปิด `007DBFSync`** — ตัวนี้คือคนที่ copy DBF จาก Express ขึ้น Drive (feeder ข้อ 2 พึ่งมัน) ต้องรันต่อทุก 15 นาที

### ปิดรอบ 007: task เร่ง `007DBFSyncFast` (ช่วงตัดรอบ 16:00–17:00)
เหตุ: `007DBFSync` อัปโหลดทุก 15 นาที → ตอนกดตัดรอบ (≤16:45) บิลที่ออกใน 15 นาทีล่าสุดยังไม่ขึ้น Drive
จะหลุดไปเป็น "ยกมา" ของรอบถัดไป · task นี้รัน **action เดียวกับ 007DBFSync** ทุก 2 นาทีเฉพาะชั่วโมงนั้น
(ไม่แก้/ไม่ปิดตัวเดิม) · ต้นฉบับ task เดิม: `AutoExport/scripts/install_{BK,SKN,PPS}.bat` + `sync_{BR}.bat`
(robocopy `*.DBF *.FPT` จาก `Z:\<br>2569` → `AutoExport\<BR>` แล้วเขียน `last_sync.txt`)

**ทางลัด (แนะนำ):** ดับเบิลคลิกแบบ Run as administrator ที่ `AutoExport/scripts/install_fast_sync.bat` (คู่มือสำหรับสาขา: `branch_tools/คู่มือตั้ง_007DBFSyncFast.md`) — สคริปต์อ่าน action/ผู้รันจาก task เดิมให้เอง · ขั้นตอนมือด้านล่างใช้เมื่อสคริปต์ล้ม

ทำที่เครื่องสาขาทีละเครื่อง — เปิด **Command Prompt แบบ Run as administrator**:
1. ดู action/ผู้รันของตัวเดิมก่อน (ห้ามเดา path — แต่ละสาขาไม่เหมือนกัน):
   ```
   schtasks /query /tn 007DBFSync /v /fo list
   ```
   จดค่า **Task To Run** (เช่น `"E:\007skn\All_on_Cloud\AutoExport\scripts\sync_SKN.bat"`)
   และ **Run As User**
   - ค่าที่ install_*.bat ตั้งไว้ (อ้างอิง — ใช้ค่าจากเครื่องจริงเป็นหลัก):
     SKN `E:\007skn\All_on_Cloud\AutoExport\scripts\sync_SKN.bat` ·
     BK `E:\007sbk\All_on_Cloud\AutoExport\scripts\sync_BK.bat` ·
     PPS `%MIRROR%\All_on_Cloud\AutoExport\scripts\sync_PPS.bat` (MIRROR ตั้งต่อเครื่อง — ต้องดูจาก query)
2. สร้าง task ใหม่ (แทน `<TASK_TO_RUN>` ด้วยค่าที่จด — คงเครื่องหมายคำพูดแบบในตัวอย่าง):
   ```
   schtasks /create /tn "007DBFSyncFast" /tr "\"<TASK_TO_RUN>\"" /sc daily /st 16:00 /ri 2 /du 01:00 /f
   ```
   ตัวอย่าง SKN:
   ```
   schtasks /create /tn "007DBFSyncFast" /tr "\"E:\007skn\All_on_Cloud\AutoExport\scripts\sync_SKN.bat\"" /sc daily /st 16:00 /ri 2 /du 01:00 /f
   ```
   - ถ้าตัวเดิม Run As User ไม่ใช่ผู้ใช้ที่ล็อกอินอยู่ ให้เติม `/ru "<Run As User>"` ให้ตรงกัน
     (คนละผู้ใช้ = อาจมองไม่เห็นไดรฟ์ `Z:` ที่ map ไว้ → sync_error.log ขึ้น "source not found")
   - วันอาทิตย์/วันหยุดรันด้วยก็ไม่เสียหาย (robocopy ข้ามไฟล์ที่ไม่เปลี่ยน)
3. ตรวจ:
   ```
   schtasks /query /tn 007DBFSyncFast /v /fo list
   schtasks /run /tn 007DBFSyncFast
   ```
   แล้วดู `AutoExport\<BR>\last_sync.txt` เวลาต้องขยับ · ช่วง 16:00–17:00 ควรขยับทุก 2 นาที
   ฝั่ง Mac ดู `p_dbf_mtime`/`cash_feed_status` หรือ `stat -f %Sm ~/ไดรฟ์ของฉัน*/All_on_Cloud/AutoExport/<BR>/last_sync.txt`
4. ยกเลิก: `schtasks /delete /tn 007DBFSyncFast /f` (ตัวเดิม 007DBFSync ไม่ได้รับผลกระทบ)

ข้อควรรู้: นาที :00/:15/:30/:45 สอง task อาจรันชนกัน (robocopy ไฟล์เดียวกันพร้อมกัน — คนเขียนทีหลังชนะ
ไฟล์ไม่พัง) · feeder ฝั่ง Mac มี guard ไฟล์ขาดท้าย (ไม่ตั้งธงหาย/ไม่แทนที่ IV ค้าง) อยู่แล้ว ·
Drive Desktop ที่สาขาต้องอัปโหลดทัน: ARTRN ~7–10MB/ครั้งเฉพาะตอนมีบิลใหม่

---

## หมายเหตุ
- ความสด SO/คอยล์ = ~15 นาที (ตามรอบ 007DBFSync) · งานด่วนใช้ปุ่ม ⚡ ในแอพ (พิมพ์เลข SO เข้าตรง ไม่รอ)
- ข้อมูล "ใบสั่งผลิต/บอร์ด/เครื่องจักร" = พนักงานกดในแอพ → เข้ากลาง realtime อยู่แล้ว (ไม่เกี่ยวกับ feeder นี้)
- ล็อกอิน = กลางที่ app.007metals.com (ทุกแอพ) เสร็จแล้ว ไม่ต้องทำอะไรเพิ่ม
- ถ้าจะย้าย routine อื่นมาเครื่อง CTO ทีหลัง (SOPO dashboard, สรุปยอด, morning brief) = คนละงาน ค่อยทำเพิ่มได้
