const $ = id => document.getElementById(id);
const state = { profile: 'mp4-compatible', outputDir: '', media: null, running: false, timer: null };
let bound = false;
let initialized = false;

function apiReady() { return window.pywebview && window.pywebview.api; }
function fmtBytes(value) {
  if (!value || value < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB']; let n = value; let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
function fmtDuration(seconds) {
  if (!Number.isFinite(Number(seconds))) return '';
  const value = Math.max(0, Math.floor(Number(seconds)));
  const h = Math.floor(value / 3600), m = Math.floor(value % 3600 / 60), s = value % 60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
}
function shortPath(path='') {
  if (path.length < 42) return path;
  return `…${path.slice(-39)}`;
}
function toast(message, kind='ok') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`; node.textContent = message;
  $('toastHost').appendChild(node);
  setTimeout(() => node.remove(), 3800);
}
function setStatus(text, kind='ready') {
  $('statusText').textContent = text;
  $('statusDot').className = kind === 'busy' ? 'busy' : kind === 'error' ? 'error' : '';
}
function selectedQuality() {
  const value = $('qualitySelect').value;
  return value ? Number(value) : null;
}
function syncDownloadCopy() {
  const names = { best: 'Chất lượng gốc', 'mp4-compatible': 'MP4', audio: 'Âm thanh gốc', 'audio-mp3': 'MP3' };
  const isAudio = state.profile.startsWith('audio');
  const quality = isAudio ? 'âm thanh tốt nhất' : ($('qualitySelect').selectedOptions[0]?.textContent || 'tốt nhất');
  $('downloadSub').textContent = `${names[state.profile]} · ${quality}`;
  $('qualitySelect').disabled = isAudio;
}
function setRunning(running) {
  state.running = running;
  $('downloadBtn').disabled = running;
  $('inspectBtn').disabled = running;
  $('urlInput').disabled = running;
  $('profiles').classList.toggle('locked', running);
  $('progressBox').classList.toggle('hidden', !running);
  if (!running && state.timer) { clearInterval(state.timer); state.timer = null; }
}
function showMedia(media) {
  state.media = media;
  $('mediaCard').classList.remove('hidden');
  $('mediaTitle').textContent = media.title || 'Không có tiêu đề';
  $('source').textContent = (media.extractor || 'MEDIA').toUpperCase();
  $('mediaMeta').textContent = [media.uploader, media.is_playlist ? `${media.entry_count || '?'} mục` : fmtDuration(media.duration)].filter(Boolean).join(' · ');
  $('duration').textContent = media.is_playlist ? 'PLAYLIST' : fmtDuration(media.duration);
  $('duration').classList.toggle('hidden', !$('duration').textContent);
  if (media.thumbnail_url) {
    $('thumbnail').src = media.thumbnail_url;
    $('thumbnail').onerror = () => { $('thumbnail').removeAttribute('src'); };
  }
}
async function inspectUrl(silent=false) {
  const url = $('urlInput').value.trim();
  if (!url) { if (!silent) toast('Hãy dán link video trước.', 'error'); return false; }
  setStatus('Đang đọc link…', 'busy');
  $('inspectBtn').textContent = 'Đang kiểm tra…';
  try {
    const response = await window.pywebview.api.inspect_url(url);
    if (!response.ok) throw new Error(response.error?.message || 'Không đọc được link.');
    showMedia(response.media);
    setStatus('Đã nhận diện video');
    if (!silent) toast('Link hợp lệ. Có thể tải rồi.');
    return true;
  } catch (error) {
    state.media = null; $('mediaCard').classList.add('hidden');
    setStatus('Link chưa hợp lệ', 'error');
    toast(error.message || String(error), 'error');
    return false;
  } finally { $('inspectBtn').textContent = 'Kiểm tra link'; }
}
async function startDownload() {
  if (state.running) return;
  if (!state.outputDir) { toast('Hãy chọn thư mục lưu.', 'error'); return; }
  if (!state.media) {
    const ok = await inspectUrl(true);
    if (!ok) return;
  }
  const payload = {
    url: $('urlInput').value.trim(), output_dir: state.outputDir,
    profile: state.profile, quality_limit: selectedQuality(),
    conflict_policy: 'reuse', write_metadata: false, retries: 3, timeout_seconds: 20,
    allow_playlist: false,
  };
  $('resultCard').classList.add('hidden');
  $('progressBar').style.width = '1%'; $('progressPct').textContent = '1%';
  setRunning(true); setStatus('Đang tải…', 'busy');
  const started = await window.pywebview.api.start_download(payload);
  if (!started.ok) { setRunning(false); setStatus('Không thể bắt đầu', 'error'); toast(started.error, 'error'); return; }
  state.timer = setInterval(pollJob, 350);
}
async function pollJob() {
  try {
    const job = await window.pywebview.api.job_status();
    const pct = Number.isFinite(Number(job.percent)) ? Math.max(0, Math.min(100, Number(job.percent))) : 0;
    $('progressBar').style.width = `${pct}%`; $('progressPct').textContent = pct ? `${Math.round(pct)}%` : '…';
    $('progressText').textContent = job.message || 'Đang tải…';
    const parts = [];
    if (job.downloaded_bytes) parts.push(fmtBytes(job.downloaded_bytes));
    if (job.total_bytes) parts.push(`/ ${fmtBytes(job.total_bytes)}`);
    if (job.speed) parts.push(`· ${fmtBytes(job.speed)}/s`);
    if (job.eta) parts.push(`· còn ${Math.ceil(job.eta)}s`);
    $('progressStats').textContent = parts.join(' ') || ({inspect:'Đang đọc metadata', merge:'Đang ghép luồng', postprocess:'Đang xử lý định dạng', verify:'Đang kiểm tra file'}[job.stage] || 'Đang xử lý');
    if (!job.running) finishJob(job);
  } catch (error) { setRunning(false); setStatus('Mất kết nối giao diện', 'error'); toast(String(error), 'error'); }
}
function finishJob(job) {
  setRunning(false);
  const result = job.result || {};
  if (result.status === 'success') {
    const artifact = result.artifacts?.[0];
    setStatus('Tải xong');
    $('resultCard').classList.remove('hidden');
    $('resultTitle').textContent = 'Đã tải và kiểm tra file';
    $('resultPath').textContent = artifact?.path || state.outputDir;
    const detail = [artifact?.codecs?.join(' + '), result.selected_format && `luồng ${result.selected_format}`].filter(Boolean).join(' · ');
    $('resultTitle').textContent = detail ? `Đã tải · ${detail}` : 'Đã tải và kiểm tra file';
    toast('Tải hoàn tất.');
    if (result.warnings?.length) toast(result.warnings[0], 'error');
  } else if (result.status === 'cancelled') {
    setStatus('Đã hủy'); toast('Đã hủy lượt tải.');
  } else {
    setStatus('Tải thất bại', 'error');
    toast(result.error?.message || job.message || 'Tải thất bại.', 'error');
  }
}
function fmtDate(iso) {
  const date = iso ? new Date(iso) : null;
  return date && !isNaN(date) ? date.toLocaleDateString('vi-VN') : '';
}
function showCookie(cookie, premium) {
  const dot = $('cookieDot'); const box = $('cookieState');
  let text; let kind;
  if (premium) {
    kind = premium.premium ? 'ok' : 'error';
    text = premium.message || '';
  } else if (!cookie?.configured) {
    kind = 'error'; text = 'Chưa có cookie. Âm thanh YouTube tối đa ~130k.';
  } else if (!cookie.logged_in) {
    kind = 'error'; text = `Cookie ${cookie.file_name} chưa đăng nhập hoặc đã hết hạn.`;
  } else {
    kind = 'warn';
    text = `Đã có cookie (${cookie.cookie_count} mục, cập nhật ${fmtDate(cookie.updated_at)}, hạn ${fmtDate(cookie.expires_at) || '?'}). Bấm "Kiểm tra Premium" để thử thật.`;
  }
  dot.className = kind;
  box.className = `cookie-state ${kind === 'warn' ? '' : kind}`;
  box.textContent = text;
}
async function refreshCookie() {
  const response = await window.pywebview.api.cookie_status();
  if (response.ok) showCookie(response.cookie);
  return response;
}
function setCookieBusy(busy, text) {
  $('cookieSave').disabled = busy; $('cookieCheck').disabled = busy; $('cookieInput').disabled = busy;
  if (busy) { $('cookieState').className = 'cookie-state'; $('cookieState').textContent = text; }
}
async function checkPremium() {
  setCookieBusy(true, 'Đang hỏi YouTube Music…');
  try {
    const result = await window.pywebview.api.check_premium();
    if (!result.ok) throw new Error(result.error?.message || 'Không kiểm tra được Premium.');
    showCookie(null, result);
  } catch (error) {
    showCookie(null, { premium: false, message: error.message || String(error) });
  } finally { setCookieBusy(false); }
}
async function saveCookies() {
  const text = $('cookieInput').value;
  if (!text.trim()) { toast('Hãy dán cookie vào ô trước.', 'error'); $('cookieInput').focus(); return; }
  setCookieBusy(true, 'Đang lưu cookie…');
  let response;
  try { response = await window.pywebview.api.save_cookies(text); }
  catch (error) { response = { ok: false, error: { message: String(error) } }; }
  finally { setCookieBusy(false); }
  if (!response.ok) {
    showCookie(null, { premium: false, message: response.error?.message || 'Không lưu được cookie.' });
    return;
  }
  $('cookieInput').value = '';
  toast(`Đã lưu ${response.cookie.cookie_count} cookie YouTube.`);
  await checkPremium();
}
function openCookieModal() {
  $('cookieModal').classList.remove('hidden');
  $('cookieInput').focus();
  refreshCookie();
}
function closeCookieModal() {
  $('cookieInput').value = '';
  $('cookieModal').classList.add('hidden');
}
async function chooseOutput() {
  const path = await window.pywebview.api.choose_output();
  if (path) { state.outputDir = path; $('outputPath').textContent = shortPath(path); $('outputPath').title = path; }
}
async function openOutput() {
  const result = await window.pywebview.api.open_output();
  if (!result.ok) toast(result.error || 'Không mở được thư mục.', 'error');
}
function bind() {
  if (bound) return;
  bound = true;
  $('profiles').addEventListener('click', event => {
    const button = event.target.closest('.profile'); if (!button || state.running) return;
    document.querySelectorAll('.profile').forEach(item => item.classList.remove('active'));
    button.classList.add('active'); state.profile = button.dataset.profile; syncDownloadCopy();
  });
  $('qualitySelect').addEventListener('change', syncDownloadCopy);
  $('inspectBtn').addEventListener('click', () => inspectUrl());
  $('downloadBtn').addEventListener('click', startDownload);
  $('outputBtn').addEventListener('click', chooseOutput);
  $('openOutputTop').addEventListener('click', openOutput);
  $('openResult').addEventListener('click', openOutput);
  $('cancelBtn').addEventListener('click', () => window.pywebview.api.cancel_download());
  $('cookieBtn').addEventListener('click', openCookieModal);
  $('cookieClose').addEventListener('click', closeCookieModal);
  $('cookieModal').addEventListener('click', event => { if (event.target === $('cookieModal')) closeCookieModal(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('cookieModal').classList.contains('hidden')) closeCookieModal(); });
  $('cookieSave').addEventListener('click', saveCookies);
  $('cookieCheck').addEventListener('click', checkPremium);
  $('pasteBtn').addEventListener('click', async () => {
    try { $('urlInput').value = await navigator.clipboard.readText(); $('urlInput').focus(); }
    catch { toast('Nhấn Ctrl+V để dán link.', 'error'); $('urlInput').focus(); }
  });
  $('urlInput').addEventListener('input', () => { state.media = null; $('mediaCard').classList.add('hidden'); });
  $('urlInput').addEventListener('keydown', event => { if (event.key === 'Enter') inspectUrl(); });
}
async function init() {
  if (initialized || !apiReady()) return;
  initialized = true;
  syncDownloadCopy();
  try {
    const initial = await window.pywebview.api.initial_state();
    state.outputDir = initial.output_dir || '';
    state.profile = initial.profile || 'mp4-compatible';
    document.querySelectorAll('.profile').forEach(item => item.classList.toggle('active', item.dataset.profile === state.profile));
    $('qualitySelect').value = initial.quality_limit == null ? '' : String(initial.quality_limit);
    $('outputPath').textContent = shortPath(state.outputDir); $('outputPath').title = state.outputDir;
    syncDownloadCopy(); setStatus('Sẵn sàng');
    refreshCookie().catch(() => {});
  } catch (error) { setStatus('Không nối được backend', 'error'); toast(String(error), 'error'); }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, {once:true});
else bind();
window.addEventListener('pywebviewready', init, {once:true});
setTimeout(init, 800);
