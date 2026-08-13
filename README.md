# Production feeder — Mac mini อ่าน DBF จาก Google Drive → push Supabase กลาง

`so_push.py` (ตัวจริงจากระบบผลิต) อ่าน Express DBF ที่ task **007DBFSync** sync ขึ้น Google Drive
ทุก ~15 นาที (`All_on_Cloud/AutoExport/{SKN,BK,PPS}/`) แล้ว push **so_live / so_item_live / coil_stock**
เข้าโปรเจกต์ Supabase กลาง (`syvfdbvmwaeyokytckwb`) — ตั้ง launchd รันเองทุก 10 นาที

> รันบนเครื่องไหนก็ได้ที่ (1) มี Python 3 (2) มีโฟลเดอร์ AutoExport ของ Google Drive mount อยู่ในเครื่อง
> (3) โหลด launchd — แนะนำ **Mac mini ของ CTO** (เครื่องกลางที่รัน routines)

## ติดตั้งบนเครื่อง CTO (ทำครั้งเดียว)

**1) Google Drive Desktop** — ล็อกอิน `cto@007metals.com` แล้ว:
   - เว็บ Drive → *Shared with me* → โฟลเดอร์ `All_on_Cloud` → คลิกขวา → **Add shortcut to Drive**
   - แอป Google Drive → ตั้ง `All_on_Cloud/AutoExport` เป็น **Available offline** (สำคัญ! ไม่งั้นอ่านไฟล์ช้า)

**2) clone + ตั้งค่า**
```bash
git clone https://github.com/GH007-LAB/prod-feeder.git ~/prod-feeder
cd ~/prod-feeder
cp feeder.env.example feeder.env   # feeder.env ไม่ commit (มี PROXY_TOKEN ต่อเครื่อง) — .gitignore ไว้แล้ว
# หา path จริงของ AutoExport บนเครื่องนี้:
ls ~/Library/CloudStorage/GoogleDrive-cto@007metals.com/"My Drive"/All_on_Cloud/AutoExport
# แก้ PROXY_URL/PROXY_TOKEN (หรือ DRIVE_ROOT ถ้าย้อนกลับไป local mount) ใน feeder.env ให้ตรงเครื่อง
nano feeder.env
```

**3) ทดสอบ 1 รอบ** (dry-run ไม่ push):
```bash
source feeder.env
python3 so_push.py <(printf "BRANCH=SKN\nSRC=$DRIVE_ROOT/SKN\nSUPABASE_URL=$SUPABASE_URL\nSUPABASE_KEY=$SUPABASE_KEY\n") --dry
```
ควรขึ้น `SKN: N SO in window, ...` = อ่าน DBF ได้

**4) ติดตั้ง launchd** (รันเองทุก 10 นาที):
```bash
./install.sh
```

## Monitor / จัดการ
- log: `tail -f ~/007so_push/feeder.log`
- รันมือ: `./run_all.sh`
- ปิด: `./uninstall.sh`
- ล้าง state (บังคับ push ใหม่หมด): `rm ~/007so_push/state_*.json ~/007so_push/stock_*.json`

## ไฟล์
| ไฟล์ | หน้าที่ |
|---|---|
| `so_push.py` | feeder จริง (SO+item+coil ในตัว, pure Python) |
| `feeder.env.example` | เทมเพลต — `cp` เป็น `feeder.env` แล้วแก้ต่อเครื่อง (ไม่ commit เพราะมี `PROXY_TOKEN`) |
| `run_all.sh` | รันทั้ง 3 สาขา + log |
| `install.sh` / `uninstall.sh` | ติดตั้ง/ปิด launchd |

## หมายเหตุ
- ความสด ~15 นาที (ตามรอบ 007DBFSync) · งานด่วน = ปุ่ม ⚡ ในแอพ (พิมพ์เลข SO เข้าตรง)
- `SUPABASE_KEY` = anon key กลาง (public โดยดีไซน์ — ปลอดภัย, so_push ใช้ anon เท่านั้น ห้าม service_role)
- ใช้ทางนี้แล้ว → ปิด task **007SoPush** บนเครื่องสาขา (กัน push ซ้ำ) แต่ **คง 007DBFSync ไว้** (คนป้อน DBF ขึ้น Drive)
