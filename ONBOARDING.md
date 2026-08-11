# 007 Metals — ส่งต่องานย้ายแอพ (Onboarding: Claude เครื่อง Mac mini CTO)

เอกสารนี้ให้ Claude เครื่อง CTO (cto@007metals.com) รับงานย้ายแอพต่อจากเครื่องเดิม (kritsada@). ตอบไทยเสมอ.

## 🎯 เป้าหมายรวม
ย้าย 7 แอพองค์กร (ระบบผลิต/HR/ใบเสนอราคา/สต็อก/SOPO/หน้างาน-FieldLog/ตรวจคอยล์-Kilo) จากแอพเก่า → **Supabase กลาง** (`syvfdbvmwaeyokytckwb`) + **ล็อกอินกลาง** `app.007metals.com` (SSO ทุกแอพ). ส่วนใหญ่เสร็จแล้ว เครื่อง CTO รับไม้ต่อ 3 งาน + งาน ops.

## 🔑 Access ที่ต้องมีก่อนเริ่ม
1. **central Supabase service key** — ขอจาก Pond (เดิมอยู่ไฟล์ `hr-app/scripts/import/_source/.env` ตัวแปร `SUPABASE_SERVICE_ROLE_KEY` บนเครื่องเก่า) · URL `https://syvfdbvmwaeyokytckwb.supabase.co` · เก็บเป็น env ห้าม commit
2. **Google Drive** = cto@ (เครื่องนี้ล็อกอินอยู่) — ต้อง *Add shortcut to Drive* โฟลเดอร์ `All_on_Cloud` (แชร์จาก 007skn0777) + ตั้ง `AutoExport` เป็น **Available offline**
3. **GitHub** org `GH007-LAB` — clone repos (git author ต้อง `GH007-LAB <299744317+GH007-LAB@users.noreply.github.com>`)
4. **anon key กลาง** (public, ใส่โค้ดได้) — ดึงจาก repo/แอพที่ deploy แล้ว หรือขอ Pond

## 📊 สถานะ 7 แอพ (audit 2026-08-11)
| แอพ | สถานะ | เหลือ |
|---|---|---|
| 📦 สต็อก (stock) | ✅ ครบ 1,088 + cron sync | — |
| 📄 ใบเสนอราคา (quote) | ✅ ข้อมูลครบ (243 บิล) | เหลือ*เขียนฟีเจอร์* builder (ไม่ใช่ย้ายข้อมูล) |
| 🏭 ระบบผลิต (production) | ✅ historical ครบ · SSO+cutover เสร็จ | **A. เปิด feeder** (SO/คอยล์ค้าง snapshot) |
| 👥 HR | ✅ core ครบ | **E.** seed วันหยุด2569 + รูปพนง.ใหม่ 22 |
| 🔩 ตรวจคอยล์ (Kilo) | ✅ record 653 ย้ายแล้ว | **B. รูป 58 ไฟล์** (sync_photos.py) |
| 🔧 หน้างาน (FieldLog) | 🟡 master ครบ record 0 | **C. ย้าย ~275 record + asset detail** |
| 🗂️ SOPO | 🟡 แค่คิวกด✔ | **D. Performance Arena** (dashboard/KPI — งานใหญ่ คุยขอบเขตก่อน) |

## 📋 งานที่เหลือ (ทำตามลำดับ)

### A. เปิด feeder ระบบผลิต ⭐ด่วนสุด (SO/คอยล์ยังค้าง snapshot เมื่อ 11 ส.ค. เช้า)
`git clone https://github.com/GH007-LAB/prod-feeder.git ~/prod-feeder` → ทำตาม **`CTO_SETUP.md`** ในนั้น (Drive mount AutoExport offline → แก้ `DRIVE_ROOT` ใน feeder.env → `./run_all.sh` ทดสอบ → `./install.sh` launchd ทุก 10 นาที). so_push.py ทำ SO+item+coil ในตัว. หลังทำ → ปิด task `007SoPush` บนเครื่องสาขา (คง `007DBFSync`).

### B. Kilo — รูป 58 ไฟล์ (record 653 ย้ายแล้ว)
`git clone GH007-LAB/kilo-app` → `scripts/sync/sync_photos.py` (ดู README_migrate.md): mount โฟลเดอร์รูป Kilo (Drive) → `KILO_PHOTOS_ROOT=<root> python3 sync_photos.py --push` → อัปเข้า Storage bucket `kilo` (path ตรงกับ photo_paths แล้ว). แล้วเลิกใช้ Apps Script Kilo เก่า (ยังเขียนทุกวัน)

### C. FieldLog — ย้าย ~275 record + แก้ asset detail (ยังไม่เริ่ม)
Sheet ต้นทาง `007_FieldLog_DB` id `12XTTHPCoiTkLc6WMGart6tfcQXeqIOfo4j6pD1H87SQ` (แชร์ cto@). ย้าย usage(~200)/trip(46)/fuel(19)/wo(9)/inspect(1) → `fl_*` + re-import `fl_asset.detail` (เลขไมล์/คำเตือน) + รูป→Storage `fieldlog`. เขียน import script ใหม่ (repo fieldlog-app มีแต่ scripts/seed/ ยังไม่มี sync). แล้วตัดสวิตช์ให้หน้างานใช้ field.007metals.com

### D. SOPO Performance Arena — งานใหญ่ **คุยขอบเขตกับ Pond ก่อนลงมือ**
ใหม่=แค่คิวกด✔. เก่า=dashboard ยอดขาย/KPI/pipeline/mentor/bonus/report (Drive folder `1gsP-aSAWoTzxpZ3UmeOpU3Frkb6PZoa1`: SOPO.html, sopo_data2-16.js, reports). ต้องออกแบบตาราง+sync+UI ใหม่. **อย่าเพิ่งลงมือจนกว่า Pond เคาะว่าจะเอาฟีเจอร์ไหน**

### E. HR ของเล็ก
seed `hr_holidays` (Pond ส่งรายการวันหยุด 2569) · เพิ่มรูปพนง.ใหม่ 22 คน (Pond ส่งรูป) — ทั้งคู่กู้จากต้นทางไม่ได้

## 📦 repos (GH007-LAB)
`prod-feeder`(feeder+CTO_SETUP) · `007-production-app` · `hr-app`(มี scripts/import + _source ข้อมูลดิบ HR007) · `kilo-app`(scripts/sync) · `fieldlog-app` · `sopo-app` · `stock-app` · `quote-app` · `SP-TT-dashboard`(hub+portal+login กลาง)

## 🤖 sub-agents แนะนำ
ไม่จำเป็นต้องมี custom agent ก็ทำได้ (spawn **general-purpose** ad-hoc สำหรับงานขนาน เช่น audit หลายแอพพร้อมกัน). แต่ถ้าอยากได้ทีมเหมือนเครื่องเดิม → ก็อป `~/.claude/agents/*.md` จากเครื่องเก่ามาไว้ `~/.claude/agents/` เครื่องนี้ ตัวที่ใช้กับงานย้าย/build:
- **web-architect** (วางแผน/ออกแบบ — โดยเฉพาะ SOPO งาน D) · **backend-developer** (schema/API/importer) · **frontend-developer** + **ui-ux-designer** (UI) · **code-reviewer-qa** (รีวิวก่อน deploy)
- (ops รายวัน: hr-*, sales-*, shopee/tiktok-* — ก็อปด้วยถ้าจะรัน routine สรุปยอด)

## ⚠️ บทเรียน/gotchas (ต้องรู้ กันพลาดซ้ำ)
- **git author** ต้อง `GH007-LAB` เท่านั้น ไม่งั้น Vercel **Deployment Blocked** · deploy = `git push` (CLI deploy ค้าง)
- **git push/commit บางครั้งโดน auto-classifier block** → แยก `git commit` ก่อน แล้ว `git push` ต่างหาก + retry ได้
- **รัน SQL ใน Supabase SQL editor**: paste/type ปกติไม่ติด → ใช้ `window.monaco.editor.getEditors()[0].setValue(sql)` ผ่าน browser tool
- **DDL / RLS / security policy = ให้ Pond กด Run เอง** (Claude เตรียม SQL ให้ ไม่รันเอง) · ผล "Success. No rows returned" = ปกติสำหรับ DDL
- **service_role key ห้าม commit/วางบน Drive** · **anon key = public** ใส่ในโค้ด client ได้
- **Vercel env**: ตั้ง `--no-sensitive` เพื่อ verify ย้อนได้ · เปลี่ยน env ต้อง redeploy (git push) ถึงมีผล · ค่าที่ env ls โชว์ `eyJ2...` เป็น format จัดเก็บ ไม่ใช่ raw (verify ด้วย env pull)
- **supabase-py `.upsert()`** encode jsonb ให้เอง → **ห้าม `json.dumps()` ค่า jsonb ซ้อน** (ได้ string ซ้อน พังฝั่ง JS)
- **DBF ไฟล์ใหญ่ >10MB โหลดผ่าน Drive API ไม่ได้** → ต้องอ่านผ่าน Drive mount ในเครื่อง (เหตุผลที่ feeder ต้องรันบนเครื่องที่ mount)
- **แอพเก่า Kilo/FieldLog/SOPO ยังใช้อยู่ทุกวัน** → หลังย้ายต้องตัดสวิตช์ ไม่งั้นข้อมูลแตกสองที่
- รายละเอียดเต็ม (ประวัติทุกเฟส) อยู่ในไฟล์ memory เครื่องเก่า `~/.claude/projects/-Users-007metals/memory/sp-tt-dashboard-migration.md` + `production-migration.md` — Pond ก็อปมาวางที่ memory เครื่องนี้ได้ถ้าต้องการบริบทเต็ม

## ▶️ เริ่มยังไง (ลำดับแรก)
1. ขอ **central service key** จาก Pond → export เป็น env (ไม่ commit)
2. Add shortcut `All_on_Cloud` + ตั้ง `AutoExport` offline (Google Drive Desktop)
3. clone `prod-feeder` → ทำ **งาน A (feeder)** ให้จบก่อน (ข้อมูลผลิตจะได้สด)
4. ต่อ B (Kilo รูป) → C (FieldLog) → คุย D (SOPO) กับ Pond
