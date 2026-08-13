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

---

## หมายเหตุ
- ความสด SO/คอยล์ = ~15 นาที (ตามรอบ 007DBFSync) · งานด่วนใช้ปุ่ม ⚡ ในแอพ (พิมพ์เลข SO เข้าตรง ไม่รอ)
- ข้อมูล "ใบสั่งผลิต/บอร์ด/เครื่องจักร" = พนักงานกดในแอพ → เข้ากลาง realtime อยู่แล้ว (ไม่เกี่ยวกับ feeder นี้)
- ล็อกอิน = กลางที่ app.007metals.com (ทุกแอพ) เสร็จแล้ว ไม่ต้องทำอะไรเพิ่ม
- ถ้าจะย้าย routine อื่นมาเครื่อง CTO ทีหลัง (SOPO dashboard, สรุปยอด, morning brief) = คนละงาน ค่อยทำเพิ่มได้
