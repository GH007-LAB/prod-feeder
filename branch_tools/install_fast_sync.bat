@echo off
chcp 65001 >nul
rem ============================================================
rem 007 Metals - ติดตั้ง task "007DBFSyncFast" (ปิดรอบ 007)
rem   อัปโหลดไฟล์ Express ขึ้น Drive ทุก 2 นาที เฉพาะ 16:00-17:00
rem   ใช้ action เดียวกับ task เดิม 007DBFSync (อ่านจากเครื่องนี้เอง ไม่เดา path)
rem   ไม่แก้/ไม่ลบ task เดิม · รันซ้ำได้ (เขียนทับตัวเอง)
rem   วิธีใช้: คลิกขวาไฟล์นี้ > Run as administrator
rem ============================================================
setlocal EnableDelayedExpansion
title 007DBFSyncFast installer
echo.
echo [1/4] ตรวจสิทธิ์ผู้ดูแล ...
net session >nul 2>&1
if errorlevel 1 (
  echo   X ต้องเปิดแบบ "Run as administrator" - คลิกขวาไฟล์นี้แล้วเลือก Run as administrator
  pause & exit /b 1
)

echo [2/4] อ่าน task เดิม 007DBFSync ...
schtasks /query /tn 007DBFSync >nul 2>&1
if errorlevel 1 (
  echo   X ไม่พบ task 007DBFSync ในเครื่องนี้ - เครื่องนี้อาจไม่ใช่เครื่อง Express ของสาขา
  pause & exit /b 1
)
set "ACTION="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "(Get-ScheduledTask -TaskName '007DBFSync').Actions[0].Execute"`) do set "ACTION=%%A"
set "RUNAS="
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "(Get-ScheduledTask -TaskName '007DBFSync').Principal.UserId"`) do set "RUNAS=%%U"
if "!ACTION!"=="" (
  echo   X อ่าน action ของ 007DBFSync ไม่ได้ - ถ่ายรูปหน้าจอนี้ส่ง CTO
  schtasks /query /tn 007DBFSync /v /fo list
  pause & exit /b 1
)
echo   task เดิมรันไฟล์: !ACTION!
echo   ผู้รัน: !RUNAS!
if not exist "!ACTION!" (
  echo   X ไฟล์ !ACTION! ไม่มีอยู่จริง - ถ่ายรูปหน้าจอนี้ส่ง CTO
  pause & exit /b 1
)

echo [3/4] สร้าง task 007DBFSyncFast (ทุก 2 นาที 16:00-17:00 ทุกวัน) ...
schtasks /create /tn "007DBFSyncFast" /tr "\"!ACTION!\"" /sc daily /st 16:00 /ri 2 /du 01:00 /f >nul
if errorlevel 1 (
  echo   X สร้าง task ไม่สำเร็จ - ลองใหม่ด้วยผู้รันเดิม ...
  schtasks /create /tn "007DBFSyncFast" /tr "\"!ACTION!\"" /sc daily /st 16:00 /ri 2 /du 01:00 /ru "!RUNAS!" /f
  if errorlevel 1 ( echo   X ยังไม่สำเร็จ - ถ่ายรูปหน้าจอนี้ส่ง CTO & pause & exit /b 1 )
)
echo   สร้างแล้ว

echo [4/4] ทดสอบรัน 1 ครั้ง ...
schtasks /run /tn "007DBFSyncFast" >nul
timeout /t 8 /nobreak >nul
for %%F in ("!ACTION!") do set "SCRIPTDIR=%%~dpF"
for /d %%B in ("!SCRIPTDIR!..\BK" "!SCRIPTDIR!..\SKN" "!SCRIPTDIR!..\PPS") do (
  if exist "%%~B\last_sync.txt" (
    echo   ผลล่าสุดใน %%~nxB: & type "%%~B\last_sync.txt"
  )
)
echo.
echo ========== เสร็จแล้ว ==========
schtasks /query /tn 007DBFSyncFast /fo list | findstr /i "TaskName Status Next"
echo.
echo ถ้าบรรทัด "ผลล่าสุด" ด้านบนเป็นเวลาเมื่อสักครู่ = ใช้ได้
echo ถ้าไม่ใช่ ให้ถ่ายรูปหน้าจอนี้ส่ง CTO
echo (ยกเลิก task นี้ภายหลัง: schtasks /delete /tn 007DBFSyncFast /f)
pause
