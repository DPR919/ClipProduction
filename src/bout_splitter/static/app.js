const app = document.querySelector('#app');
const toast = document.querySelector('#toast');
let job = null;
let view = 'generate';
let selected = 0;
let signedIn = false;
let signedInSite = '';
let pollTimer = null;
let toastTimer = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function seconds(value) {
  const n = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}
function notify(message, error = false) {
  toast.textContent = message;
  toast.style.background = error ? '#963e33' : '#253838';
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
}
async function api(path, payload) {
  const response = await fetch(path, {
    method: payload === undefined ? 'GET' : 'POST',
    headers: payload === undefined ? {} : {'Content-Type':'application/json','X-Local-Request':'review-ui'},
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function setView(next) {
  if (next !== 'generate' && !job) return;
  if (next === 'review' && job.state !== 'ready') return;
  view = next;
  render();
}
function setJob(next) {
  job = next;
  if (selected >= (job?.clips?.length || 0)) selected = 0;
  render();
  schedulePoll();
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!job || (job.state !== 'processing' && job.upload_state !== 'running')) return;
  pollTimer = setTimeout(async () => {
    try {
      const data = await api(`/api/jobs/${job.id}`);
      const justReady = job.state === 'processing' && data.job.state === 'ready';
      job = data.job;
      if (justReady) view = 'review';
      render();
    } catch (error) { notify(error.message, true); }
    schedulePoll();
  }, 1100);
}
function render() {
  document.querySelectorAll('.tabs button').forEach(button => {
    button.classList.toggle('active', button.dataset.view === view);
    button.disabled = button.dataset.view === 'review' && job?.state !== 'ready' || button.dataset.view === 'upload' && !job;
  });
  const clips = job?.clips || [];
  const reviewed = clips.filter(c => c.decision !== 'pending').length;
  document.querySelector('#review-count').textContent = clips.length ? `${reviewed}/${clips.length}` : '';
  document.querySelector('#top-status').textContent = job ? `${job.source_name} · ${job.phase}` : 'No bout selected';
  if (view === 'generate') renderGenerate();
  else if (view === 'review') renderReview();
  else renderUpload();
}
function renderGenerate() {
  app.innerHTML = `
    <div class="page-heading"><div><h1>Generate clips</h1><p>Select a full bout recording.</p></div></div>
    ${job ? `<section class="section compact"><div class="status-line">${job.state === 'processing' ? '<span class="busy"></span>' : ''}<strong>${escapeHtml(job.phase)}</strong><span class="muted">${escapeHtml(job.source_name)}</span></div>${job.error ? `<p class="error">${escapeHtml(job.error)}</p>` : ''}${job.state === 'ready' ? `<div class="actions"><button class="btn primary" id="go-review">Review ${job.clips.length} clips</button></div>` : ''}</section>` : ''}
    <section class="section"><h2>Source recording</h2><div class="field"><label for="source">Video file</label><input id="source" type="file" accept=".mp4,.mov,.mkv,.webm,video/*"></div>
      <div id="upload-progress" hidden><div class="progress"><span id="upload-bar" style="width:0%"></span></div><small id="upload-pct">Copying source video…</small></div>
      <div class="actions"><button class="btn primary" id="generate-btn" ${job?.state === 'processing' ? 'disabled' : ''}>Generate clips</button><span class="muted">Local processing. Your source file is not uploaded to Whatsthecall.</span></div></section>
    <section class="section"><h2>Detection settings</h2><div class="form-grid four">
      ${field('fps','Sample rate (fps)','number','5','min="1" max="20" step="1"')}
      ${field('minPixels','Light threshold (pixels)','number','500','min="1" max="100000" step="1"')}
      ${field('minGap','Merge gap (sec)','number','5','min="0" max="20" step="0.1"')}
      ${field('lookback','Motion lookback (sec)','number','8','min="1" max="30" step="0.1"')}
      ${field('startAt','Start at (sec)','number','0','min="0" step="0.1"')}
      ${field('endAt','End at (sec)','number','','min="0" step="0.1" placeholder="Full recording"')}
      ${field('roi','Light area (x1,y1,x2,y2)','text','0,0.78,1,1')}
      ${field('motionRoi','Motion area (x1,y1,x2,y2)','text','0,0,1,0.78')}
    </div></section>`;
  document.querySelector('#generate-btn').addEventListener('click', generate);
  document.querySelector('#go-review')?.addEventListener('click', () => setView('review'));
}
function field(id, label, type, value, extra = '') {
  return `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${escapeHtml(value)}" ${extra}></div>`;
}
function generate() {
  const file = document.querySelector('#source').files[0];
  if (!file) return notify('Choose a full bout video first.', true);
  const names = ['fps','minPixels','minGap','lookback','startAt','endAt','roi','motionRoi'];
  const settings = Object.fromEntries(names.map(name => [name, document.getElementById(name).value]));
  const url = `/api/jobs?name=${encodeURIComponent(file.name)}&settings=${encodeURIComponent(JSON.stringify(settings))}`;
  const request = new XMLHttpRequest();
  request.open('POST', url);
  request.setRequestHeader('X-Local-Request', 'review-ui');
  document.querySelector('#generate-btn').disabled = true;
  document.querySelector('#upload-progress').hidden = false;
  request.upload.onprogress = event => {
    if (event.lengthComputable) {
      const pct = Math.round(100 * event.loaded / event.total);
      document.querySelector('#upload-bar').style.width = `${pct}%`;
      document.querySelector('#upload-pct').textContent = `Copying source video: ${pct}%`;
    }
  };
  request.onload = () => {
    try {
      const data = JSON.parse(request.responseText);
      if (request.status >= 400) throw new Error(data.error || 'Could not start generation.');
      setJob(data.job);
    } catch (error) {
      document.querySelector('#generate-btn').disabled = false;
      notify(error.message, true);
    }
  };
  request.onerror = () => { document.querySelector('#generate-btn').disabled = false; notify('Source video could not be copied.', true); };
  request.send(file);
}
function renderReview() {
  const clips = job.clips;
  const clip = clips[selected];
  const reviewed = clips.filter(c => c.decision !== 'pending').length;
  app.innerHTML = `<div class="page-heading"><div><h1>Review clips</h1><p>${reviewed} of ${clips.length} reviewed · ${escapeHtml(job.source_name)}</p></div><button class="btn" id="go-upload">Upload setup</button></div>
    ${!clips.length ? `<section class="section"><p>No scoring-light events were detected in this recording.</p><button class="btn" id="back-generate">Back to generation</button></section>` : `<div class="review-layout">
    <aside class="clip-list"><div class="clip-list-title">Clips</div><div class="clip-scroll">${clips.map((c, i) => `<button class="clip-item ${i === selected ? 'active' : ''}" data-index="${i}"><span><span class="clip-name">${String(c.index).padStart(3,'0')}</span><span class="clip-time">${seconds(c.clip_start)}–${seconds(c.clip_end)}</span></span><span class="pill ${c.decision}">${c.decision === 'keep' ? 'Usable' : c.decision === 'discard' ? 'Discard' : 'Pending'}</span></button>`).join('')}</div></aside>
    <section class="video-panel"><div class="video-header"><strong>Clip ${String(clip.index).padStart(3,'0')}</strong><span class="pill ${clip.decision}">${clip.decision === 'keep' ? 'Usable' : clip.decision === 'discard' ? 'Discard' : 'Pending'}</span></div><div class="video-frame"><video controls preload="metadata" src="/media/${job.id}/${clip.index}"></video></div><div class="video-meta"><span>${seconds(clip.clip_start)}–${seconds(clip.clip_end)} in source</span><span>${(clip.clip_end - clip.clip_start).toFixed(1)} sec</span><span>Start: ${escapeHtml(clip.start_method)}</span></div><div class="video-actions"><button class="btn" id="prev" ${selected === 0 ? 'disabled' : ''}>Previous</button><span class="muted">${selected + 1} / ${clips.length}</span><button class="btn" id="next" ${selected === clips.length - 1 ? 'disabled' : ''}>Next</button></div></section>
    <aside class="details-panel"><h2>Clip details</h2><div class="field"><label for="clip-title">Title</label><input id="clip-title" maxlength="300" value="${escapeHtml(clip.title)}"></div><div class="field"><label for="clip-score">Score at touch</label><input id="clip-score" maxlength="100" placeholder="e.g. 8–7" value="${escapeHtml(clip.scoreAtTouch)}"></div><div class="field"><label for="clip-notes">Notes</label><textarea id="clip-notes" maxlength="5000">${escapeHtml(clip.notes)}</textarea></div>${clip.review_reasons?.length ? `<div class="notice">${clip.review_reasons.map(escapeHtml).join(', ')}</div>` : ''}<div class="actions"><button class="btn primary" id="keep" ${clip.upload_status === 'uploaded' ? 'disabled' : ''}>Mark usable</button><button class="btn danger" id="discard" ${clip.upload_status === 'uploaded' ? 'disabled' : ''}>Discard</button></div><button class="btn text" id="save-details" ${clip.upload_status === 'uploaded' ? 'disabled' : ''}>Save details</button>${clip.upload_status === 'uploaded' ? '<p class="success">Uploaded clips are locked.</p>' : ''}</aside></div>`}`;
  document.querySelector('#go-upload').addEventListener('click', () => setView('upload'));
  document.querySelector('#back-generate')?.addEventListener('click', () => setView('generate'));
  document.querySelectorAll('.clip-item').forEach(button => button.addEventListener('click', () => { selected = Number(button.dataset.index); render(); }));
  document.querySelector('#prev')?.addEventListener('click', () => { selected--; render(); });
  document.querySelector('#next')?.addEventListener('click', () => { selected++; render(); });
  document.querySelector('#keep')?.addEventListener('click', () => saveReview('keep', true));
  document.querySelector('#discard')?.addEventListener('click', () => saveReview('discard', true));
  document.querySelector('#save-details')?.addEventListener('click', () => saveReview(clip?.decision, false));
}
async function saveReview(decision, advance) {
  const clip = job.clips[selected];
  const payload = {index:clip.index, decision, title:document.querySelector('#clip-title').value,
    scoreAtTouch:document.querySelector('#clip-score').value, notes:document.querySelector('#clip-notes').value};
  try {
    const data = await api(`/api/jobs/${job.id}/review`, payload);
    job = data.job;
    if (advance) {
      const nextPending = job.clips.findIndex((c,i) => i > selected && c.decision === 'pending');
      if (nextPending >= 0) selected = nextPending;
      else if (job.clips.some(c => c.decision === 'pending')) selected = job.clips.findIndex(c => c.decision === 'pending');
      else if (selected < job.clips.length - 1) selected++;
      else if (job.clips.every(c => c.decision !== 'pending')) { view = 'upload'; notify('Review complete. Add match details to upload.'); }
    } else notify('Clip details saved.');
    render();
  } catch (error) { notify(error.message, true); }
}
function renderUpload() {
  const clips = job.clips || [];
  const kept = clips.filter(c => c.decision === 'keep');
  const discarded = clips.filter(c => c.decision === 'discard');
  const pending = clips.filter(c => c.decision === 'pending');
  const uploaded = kept.filter(c => c.upload_status === 'uploaded');
  const s = job.shared;
  app.innerHTML = `<div class="page-heading"><div><h1>Upload approved clips</h1><p>${escapeHtml(job.source_name)}</p></div>${job.state === 'ready' ? '<button class="btn" id="back-review">Review clips</button>' : ''}</div>
    <div class="summary-grid"><div class="summary-stat"><strong>${clips.length}</strong><span>Generated</span></div><div class="summary-stat"><strong>${kept.length}</strong><span>Usable</span></div><div class="summary-stat"><strong>${discarded.length}</strong><span>Discarded</span></div><div class="summary-stat"><strong>${uploaded.length}</strong><span>Uploaded</span></div></div>
    ${pending.length ? `<div class="notice">${pending.length} clip${pending.length === 1 ? '' : 's'} still need review. Upload is available after all clips are marked.</div>` : ''}
    ${job.upload_error ? `<div class="notice error">${escapeHtml(job.upload_error)}</div>` : ''}
    <div class="upload-grid"><div><section class="section"><h2>Match details</h2><div class="form-grid two">
      ${field('eventName','Event name','text',s.eventName,'maxlength="300"')}
      ${field('weapon','Weapon','text',s.weapon,'maxlength="100"')}
      ${field('leftFencer','Left fencer','text',s.leftFencer,'maxlength="200"')}
      ${field('rightFencer','Right fencer','text',s.rightFencer,'maxlength="200"')}
      <div class="span-2">${field('sourceUrl','Source video URL','url',s.sourceUrl,'placeholder="https://…" maxlength="2048"')}</div>
      </div><div class="actions"><button class="btn" id="save-match" ${job.upload_state === 'running' ? 'disabled' : ''}>Save match details</button></div></section>
      <section class="section"><h2>Whatsthecall account</h2><div class="form-grid two">${field('site','Site URL','url',signedInSite || job.upload_site || 'http://localhost:3000','placeholder="https://your-site.example"')}<div></div>${field('email','Email','email','','autocomplete="username"')}<div class="field"><label for="password">Password</label><input id="password" type="password" autocomplete="current-password"></div></div><div class="actions"><button class="btn" id="sign-in">Sign in</button><span class="${signedIn ? 'success' : 'muted'}">${signedIn ? `Signed in to ${escapeHtml(signedInSite)}` : 'Not signed in'}</span></div></section></div>
      <section class="section"><h2>Upload queue</h2>${kept.length ? `<ol class="upload-list">${kept.map(c => `<li class="upload-row"><span><strong>${String(c.index).padStart(3,'0')}</strong> ${escapeHtml(c.title)}${c.upload_error ? `<br><small class="error">${escapeHtml(c.upload_error)}</small>` : ''}</span><span class="pill ${c.upload_status}">${escapeHtml(c.upload_status)}</span></li>`).join('')}</ol>` : '<p class="muted">No usable clips selected.</p>'}<div class="divider"></div><button class="btn primary" id="upload-clips" ${pending.length || !kept.length || !signedIn || job.state !== 'ready' || job.upload_state === 'running' || uploaded.length === kept.length ? 'disabled' : ''}>${job.upload_state === 'running' ? 'Uploading…' : uploaded.length ? 'Retry remaining clips' : 'Upload usable clips'}</button><p class="muted" style="margin:11px 0 0">Only usable clips are sent to S3 and registered on Whatsthecall.</p></section></div>`;
  document.querySelector('#back-review')?.addEventListener('click', () => setView('review'));
  document.querySelector('#save-match').addEventListener('click', saveShared);
  document.querySelector('#sign-in').addEventListener('click', signIn);
  document.querySelector('#upload-clips').addEventListener('click', uploadClips);
}
function sharedValues() {
  return Object.fromEntries(['eventName','leftFencer','rightFencer','weapon','sourceUrl'].map(id => [id,document.getElementById(id).value]));
}
async function saveShared() {
  try {
    const data = await api(`/api/jobs/${job.id}/shared`, sharedValues());
    job = data.job;
    notify('Match details saved.');
  } catch (error) { notify(error.message, true); }
}
async function signIn() {
  const payload = {site:document.querySelector('#site').value, email:document.querySelector('#email').value,
    password:document.querySelector('#password').value};
  const button = document.querySelector('#sign-in');
  button.disabled = true;
  try {
    const data = await api('/api/login', payload);
    signedIn = true;
    signedInSite = data.site;
    render();
    notify('Signed in to Whatsthecall.');
  } catch (error) { button.disabled = false; notify(error.message, true); }
}
async function uploadClips() {
  try {
    const shared = sharedValues();
    const saved = await api(`/api/jobs/${job.id}/shared`, shared);
    job = saved.job;
    const data = await api(`/api/jobs/${job.id}/upload`, {});
    setJob(data.job);
  } catch (error) { notify(error.message, true); }
}
document.querySelectorAll('.tabs button').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
api('/api/jobs/latest').then(data => {
  if (data.job) {
    job = data.job;
    view = job.state === 'ready' ? (job.upload_state === 'running' || job.upload_state === 'partial' || job.upload_state === 'done' ? 'upload' : 'review') : 'generate';
  }
  render(); schedulePoll();
}).catch(error => { render(); notify(error.message, true); });
