// ============================================================================
// 007 Metals — feeder Drive proxy (deploy เป็น Web App โดยบัญชีที่เห็น AutoExport)
// ให้ so_push.py ดึงไฟล์ DBF จาก AutoExport ผ่าน HTTPS แทนการอ่าน local mount
//
// เหตุผล: macOS ผูก Google Drive FileProvider (local mount) ไว้กับ active GUI
// session เท่านั้น — launchd/cron (background daemon) อ่านไม่ได้เลยไม่ว่าจะให้
// Full Disk Access ยังไง (ลองมาหมดแล้ว: FDA python3/zsh, FDA ตัว caller app,
// restart เครื่อง, เปลี่ยน launchd เป็น cron — ไม่มีทางไหนแก้ได้)
// วิธีนี้เลี่ยงปัญหาทั้งหมดเพราะเป็น HTTPS call ธรรมดา ไม่พึ่ง FileProvider เลย
//
// หมายเหตุ "DBF >10MB โหลดผ่าน Drive API ไม่ได้" ที่เคยมีคนเจอไว้ — พิสูจน์แล้วว่า
// เป็นข้อจำกัดของ tool อื่นที่ใช้ตรวจ (cap ไว้เอง 10MB กันเปลือง token/context)
// ไม่ใช่ข้อจำกัดจริงของ Drive API — ทดสอบไฟล์ 22MB โหลดได้ครบ MD5 ตรงกับต้นฉบับ
// ============================================================================

var ALL_ON_CLOUD_ID = '1SnHuxCry43cpBTpupZjDm0d80dJXRphF'; // All_on_Cloud folder id
// Apps Script โหลดไฟล์เต็มจาก Drive ทุก request อยู่ดี (ไม่มี state ข้าม invocation)
// แบ่ง chunk เล็กมีแต่เสีย (ยิงซ้ำหลายรอบ โหลดไฟล์เดิมซ้ำ) ตั้งให้ใหญ่พอที่ไฟล์
// ปัจจุบันทั้งหมด (~22MB) จบในคำขอเดียว แต่ยังกัน chunk loop ไว้เป็น safety net
var CHUNK_BYTES = 40 * 1024 * 1024;

function doGet(e) {
  var token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (!token || e.parameter.token !== token) {
    return ContentService.createTextOutput('forbidden').setMimeType(ContentService.MimeType.TEXT);
  }

  var branch = e.parameter.branch;
  var file = e.parameter.file;
  if (!branch || !file) {
    return ContentService.createTextOutput('bad request: need branch & file').setMimeType(ContentService.MimeType.TEXT);
  }

  var blob = getFileBlob_(branch, file);
  var bytes = blob.getBytes();
  var size = bytes.length;

  if (e.parameter.action === 'meta') {
    return ContentService.createTextOutput(JSON.stringify({size: size})).setMimeType(ContentService.MimeType.TEXT);
  }

  var offset = parseInt(e.parameter.offset || '0', 10);
  var length = parseInt(e.parameter.length || String(CHUNK_BYTES), 10);
  var end = Math.min(offset + length, size);
  var slice = bytes.slice(offset, end);
  var b64 = Utilities.base64Encode(slice);

  return ContentService.createTextOutput(b64).setMimeType(ContentService.MimeType.TEXT);
}

// หาไฟล์ผ่าน path All_on_Cloud/AutoExport/{branch}/{file} — cache folder id กัน list ซ้ำ
function getFileBlob_(branch, file) {
  var cache = CacheService.getScriptCache();
  var key = 'folder_' + branch;
  var folderId = cache.get(key);
  var folder;

  if (folderId) {
    folder = DriveApp.getFolderById(folderId);
  } else {
    var root = DriveApp.getFolderById(ALL_ON_CLOUD_ID);
    var autoExport = root.getFoldersByName('AutoExport').next();
    folder = autoExport.getFoldersByName(branch).next();
    cache.put(key, folder.getId(), 3600); // cache 1 ชม.
  }

  var files = folder.getFilesByName(file);
  if (!files.hasNext()) throw new Error('file not found: ' + branch + '/' + file);
  return files.next().getBlob();
}

// รันครั้งเดียวตอน setup เพื่อตั้ง token (ใส่ค่าสุ่มยาวๆ ของตัวเอง แล้วลบทิ้งจากซอร์สได้
// หลังรัน เพราะเก็บลง Script Properties แล้ว ไม่ต้องเหลือ token ไว้ในโค้ด)
function setupToken() {
  PropertiesService.getScriptProperties().setProperty('TOKEN', 'เปลี่ยนเป็นสตริงสุ่มยาวๆของคุณเอง');
}
