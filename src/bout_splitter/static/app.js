const app = document.querySelector('#app');
const toast = document.querySelector('#toast');
let job = null;
let view = 'generate';
let selected = 0;
let signedIn = false;
let signedInSite = '';
let pollTimer = null;
let toastTimer = null;
let sourceObjectUrl = null;
let trimEditor = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function seconds(value) {
  const n = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}
function preciseSeconds(value) {
  const tenths = Math.max(0, Math.round((Number(value) || 0) * 10));
  return `${Math.floor(tenths / 600)}:${String(Math.floor(tenths / 10) % 60).padStart(2, '0')}.${tenths % 10}`;
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
async function setView(next) {
  if (next !== 'generate' && !job) return;
  if (next === 'review' && job.state !== 'ready') return;
  if (view === 'review' && next !== view) {
    try { await persistTrim(); }
    catch (error) { return notify(error.message, true); }
  }
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
      const stateChanged = job.state !== data.job.state || job.upload_state !== data.job.upload_state;
      job = data.job;
      if (justReady) view = 'review';
      if (stateChanged || view !== 'generate') render();
      else {
        document.querySelector('#top-status').textContent = `${job.source_name} · ${job.phase}`;
        const phase = document.querySelector('.status-line strong');
        if (phase) phase.textContent = job.phase;
      }
    } catch (error) { notify(error.message, true); }
    schedulePoll();
  }, 1100);
}
function render() {
  if (view !== 'review') trimEditor = null;
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
      <div class="title-prefix-field">${field('titlePrefix','Clip title prefix','text',job?.title_prefix ?? 'Phrase','maxlength="100" required')}</div>
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
  augmentGenerate();
}
function augmentGenerate() {
  const previous = job?.settings || {};
  const value = (name, fallback) => previous[name] ?? fallback;
  const area = previous.roi ? Object.values(previous.roi).join(',') : '0,0.75,1,0.9';
  const redArea = previous.red_roi ? Object.values(previous.red_roi).join(',') : '0.08,0.74,0.47,0.9';
  const greenArea = previous.green_roi ? Object.values(previous.green_roi).join(',') : '0.53,0.74,0.92,0.9';
  const motion = previous.motion_roi ? Object.values(previous.motion_roi).join(',') : '0,0,1,0.78';
  for (const [id,v] of Object.entries({
    fps:value('fps',5), minPixels:value('min_pixels',500), minGap:value('min_gap',1.5),
    lookback:value('lookback',8), startAt:value('start_at',0), endAt:value('end_at','') || '',
    roi:area, motionRoi:motion,
  })) document.getElementById(id).value = v;
  document.querySelector('label[for="minGap"]').textContent = 'Minimum touch gap (sec)';
  document.querySelector('label[for="minPixels"]').textContent = 'Minimum light pixels';
  document.querySelector('label[for="startAt"]').textContent = 'Bout start (sec)';
  document.querySelector('label[for="endAt"]').textContent = 'Bout end (sec)';
  document.querySelector('#roi').parentElement.insertAdjacentHTML('beforebegin',
    field('referenceAt','Known touch (sec or mm:ss)','text',value('reference_at','') || '','placeholder="e.g. 7:00"'));
  document.querySelector('#roi').parentElement.insertAdjacentHTML('beforebegin',
    field('redRoi','Red light area','text',redArea));
  document.querySelector('#roi').parentElement.insertAdjacentHTML('beforebegin',
    field('greenRoi','Green light area','text',greenArea));
  document.querySelector('#roi').parentElement.hidden = true;
  const source = document.querySelector('#source');
  document.querySelector('.title-prefix-field').insertAdjacentHTML('afterend', `
    <div id="source-stage" class="source-stage" ${job ? '' : 'hidden'}>
      <video id="source-preview" controls preload="metadata" ${job ? `src="/source/${job.id}"` : ''}></video>
      <div id="roi-overlay" class="roi-overlay"><div id="red-box" class="roi-box red-box"></div><div id="green-box" class="roi-box green-box"></div></div>
    </div>
    <div class="actions source-tools">
      <button class="btn" id="select-red" ${job ? '' : 'disabled'}>Select red area</button>
      <button class="btn" id="select-green" ${job ? '' : 'disabled'}>Select green area</button>
      <button class="btn" id="use-start" ${job ? '' : 'disabled'}>Set start</button>
      <button class="btn" id="use-end" ${job ? '' : 'disabled'}>Set end</button>
      <button class="btn" id="use-reference" ${job ? '' : 'disabled'}>Mark known touch</button>
    </div>`);
  if (job) document.querySelector('#generate-btn').textContent = 'Re-analyze saved recording';
  if (job?.settings?.calibration) {
    const result = job.settings.calibration;
    document.querySelector('#source-stage').insertAdjacentHTML('afterend',
      `<p class="muted">Known touch matched at ${seconds(result.detected_at)}; threshold ${result.min_pixels} pixels.</p>`);
  }
  const stage = document.querySelector('#source-stage');
  const video = document.querySelector('#source-preview');
  const overlay = document.querySelector('#roi-overlay');
  const buttons = {red:document.querySelector('#select-red'),green:document.querySelector('#select-green')};
  const boxes = {red:document.querySelector('#red-box'),green:document.querySelector('#green-box')};
  function drawBox(box, values) {
    if (values.length !== 4 || values.some(v => !Number.isFinite(v))) return;
    const [x1,y1,x2,y2] = values;
    Object.assign(box.style, {left:`${x1*100}%`,top:`${y1*100}%`,width:`${(x2-x1)*100}%`,height:`${(y2-y1)*100}%`});
  }
  function showAreas() {
    for (const [color,id] of [['red','redRoi'],['green','greenRoi']]) drawBox(boxes[color], document.getElementById(id).value.split(',').map(Number));
  }
  showAreas();
  for (const id of ['redRoi','greenRoi']) document.getElementById(id).addEventListener('input', showAreas);
  video.addEventListener('loadedmetadata', () => {
    if (video.videoWidth && video.videoHeight) stage.style.aspectRatio = `${video.videoWidth}/${video.videoHeight}`;
  });
  source.addEventListener('change', () => {
    if (sourceObjectUrl) URL.revokeObjectURL(sourceObjectUrl);
    const file = source.files[0];
    if (!file) return;
    sourceObjectUrl = URL.createObjectURL(file);
    video.src = sourceObjectUrl;
    stage.hidden = false;
    document.querySelectorAll('.source-tools button').forEach(item => { item.disabled = false; });
    document.querySelector('#generate-btn').textContent = 'Generate clips';
    for (const [id,v] of Object.entries({minPixels:'500',minGap:'1.5',startAt:'0',endAt:'',referenceAt:'',roi:'0,0.75,1,0.9',redRoi:'0.08,0.74,0.47,0.9',greenRoi:'0.53,0.74,0.92,0.9'})) document.getElementById(id).value = v;
    showAreas();
  });
  for (const [buttonId,fieldId] of [['use-start','startAt'],['use-end','endAt'],['use-reference','referenceAt']]) {
    document.getElementById(buttonId).addEventListener('click', () => { document.getElementById(fieldId).value = video.currentTime.toFixed(1); });
  }
  let mode = null;
  for (const color of ['red','green']) buttons[color].addEventListener('click', () => {
    mode = mode === color ? null : color;
    overlay.classList.toggle('selecting', mode !== null);
    for (const other of ['red','green']) buttons[other].classList.toggle('primary', mode === other);
  });
  let origin = null;
  function point(event) {
    const rect = overlay.getBoundingClientRect();
    return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
  }
  overlay.addEventListener('pointerdown', event => { if (!mode) return; origin = point(event); overlay.setPointerCapture(event.pointerId); });
  overlay.addEventListener('pointermove', event => {
    if (!origin) return;
    const [x,y] = point(event);
    const [x1,x2] = [origin[0],x].sort((a,b) => a-b);
    const [y1,y2] = [origin[1],y].sort((a,b) => a-b);
    drawBox(boxes[mode], [x1,y1,x2,y2]);
  });
  overlay.addEventListener('pointerup', event => {
    if (!origin) return;
    const [x,y] = point(event);
    const [x1,x2] = [origin[0],x].sort((a,b) => a-b);
    const [y1,y2] = [origin[1],y].sort((a,b) => a-b);
    origin = null;
    overlay.classList.remove('selecting');
    buttons[mode].classList.remove('primary');
    const selectedMode = mode;
    mode = null;
    if (x2-x1 < 0.02 || y2-y1 < 0.02) return notify('Select a larger light area.', true);
    document.getElementById(selectedMode === 'red' ? 'redRoi' : 'greenRoi').value = [x1,y1,x2,y2].map(v => v.toFixed(3)).join(',');
    showAreas();
  });
}
function field(id, label, type, value, extra = '') {
  return `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${escapeHtml(value)}" ${extra}></div>`;
}
async function generate() {
  const file = document.querySelector('#source').files[0];
  if (!file && !job) return notify('Choose a full bout video first.', true);
  const titlePrefix = document.querySelector('#titlePrefix').value.trim();
  if (!titlePrefix) return notify('Enter a clip title prefix.', true);
  const names = ['fps','minPixels','minGap','lookback','startAt','endAt','referenceAt','roi','redRoi','greenRoi','motionRoi'];
  const settings = Object.fromEntries(names.map(name => [name, document.getElementById(name).value]));
  if (!file) {
    const button = document.querySelector('#generate-btn');
    button.disabled = true;
    try {
      const data = await api(`/api/jobs/${job.id}/reanalyze`, {...settings, titlePrefix});
      view = 'generate';
      setJob(data.job);
    } catch (error) { button.disabled = false; notify(error.message, true); }
    return;
  }
  const url = `/api/jobs?name=${encodeURIComponent(file.name)}&titlePrefix=${encodeURIComponent(titlePrefix)}&settings=${encodeURIComponent(JSON.stringify(settings))}`;
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
  const trimLocked = clip && (clip.s3_key || clip.upload_status === 'uploaded' || job.upload_state === 'running');
  app.innerHTML = `<div class="page-heading"><div><h1>Review clips</h1><p>${reviewed} of ${clips.length} reviewed · ${escapeHtml(job.source_name)}</p></div><button class="btn" id="go-upload">Upload setup</button></div>
    ${!clips.length ? `<section class="section"><p>No scoring-light events were detected in this recording.</p><button class="btn" id="back-generate">Back to generation</button></section>` : `<div class="review-layout">
    <aside class="clip-list"><div class="clip-list-title">Clips</div><div class="clip-scroll">${clips.map((c, i) => `<button class="clip-item ${i === selected ? 'active' : ''}" data-index="${i}"><span><span class="clip-name">${String(c.index).padStart(3,'0')}</span><span class="clip-time">${clipSourceRange(c)}</span></span><span class="pill ${c.decision}">${c.decision === 'keep' ? 'Usable' : c.decision === 'discard' ? 'Discard' : 'Pending'}</span></button>`).join('')}</div></aside>
    <section class="video-panel"><div class="video-header"><strong>Clip ${String(clip.index).padStart(3,'0')}</strong><span class="pill ${clip.decision}">${clip.decision === 'keep' ? 'Usable' : clip.decision === 'discard' ? 'Discard' : 'Pending'}</span></div><div class="video-frame"><video id="review-video" controls preload="metadata" src="/media/${job.id}/${clip.index}"></video></div>
      <div class="trim-editor"><div class="trim-heading"><strong>Crop clip</strong><span id="trim-status" class="muted" role="status"></span></div><div class="trim-times"><span>Begin <strong id="trim-begin-time"></strong></span><span>End <strong id="trim-end-time"></strong></span></div>
        <div class="trim-track"><div id="trim-selection" class="trim-selection"></div><div id="trim-playhead" class="trim-playhead"></div><input id="trim-begin" class="trim-range trim-range-begin" type="range" aria-label="Begin" min="0" step="0.01" ${trimLocked ? 'disabled' : ''}><input id="trim-end" class="trim-range trim-range-end" type="range" aria-label="End" min="0" step="0.01" ${trimLocked ? 'disabled' : ''}></div>
        <div class="trim-actions"><button class="btn" id="play-trim">Play selection</button><button class="btn" id="set-trim-begin" ${trimLocked ? 'disabled' : ''}>Set Begin</button><button class="btn" id="set-trim-end" ${trimLocked ? 'disabled' : ''}>Set End</button><button class="btn text" id="reset-trim" ${trimLocked ? 'disabled' : ''}>Reset</button></div></div>
      <div class="video-meta"><span id="trim-source-time"></span><span id="trim-duration"></span><span>Start: ${escapeHtml(clip.start_method)}</span></div><div class="video-actions"><button class="btn" id="prev" ${selected === 0 ? 'disabled' : ''}>Previous</button><span class="muted">${selected + 1} / ${clips.length}</span><button class="btn" id="next" ${selected === clips.length - 1 ? 'disabled' : ''}>Next</button></div></section>
    <aside class="details-panel"><h2>Clip details</h2><div class="field"><label for="clip-title">Title</label><input id="clip-title" maxlength="300" value="${escapeHtml(clip.title)}"></div><div class="field"><label for="clip-score">Score at touch</label><input id="clip-score" maxlength="100" placeholder="e.g. 8–7" value="${escapeHtml(clip.scoreAtTouch)}"></div><div class="field"><label for="clip-notes">Notes</label><textarea id="clip-notes" maxlength="5000">${escapeHtml(clip.notes)}</textarea></div>${clip.review_reasons?.length ? `<div class="notice">${clip.review_reasons.map(escapeHtml).join(', ')}</div>` : ''}<div class="actions"><button class="btn primary" id="keep" ${clip.upload_status === 'uploaded' ? 'disabled' : ''}>Mark usable</button><button class="btn danger" id="discard" ${clip.upload_status === 'uploaded' ? 'disabled' : ''}>Discard</button></div><button class="btn text" id="save-details" ${clip.upload_status === 'uploaded' ? 'disabled' : ''}>Save details</button>${clip.upload_status === 'uploaded' ? '<p class="success">Uploaded clips are locked.</p>' : ''}</aside></div>`}`;
  document.querySelector('#go-upload').addEventListener('click', () => setView('upload'));
  document.querySelector('#back-generate')?.addEventListener('click', () => setView('generate'));
  document.querySelectorAll('.clip-item').forEach(button => button.addEventListener('click', () => selectClip(Number(button.dataset.index))));
  document.querySelector('#prev')?.addEventListener('click', () => selectClip(selected - 1));
  document.querySelector('#next')?.addEventListener('click', () => selectClip(selected + 1));
  document.querySelector('#keep')?.addEventListener('click', () => saveReview('keep', true));
  document.querySelector('#discard')?.addEventListener('click', () => saveReview('discard', true));
  document.querySelector('#save-details')?.addEventListener('click', () => saveReview(clip?.decision, false));
  if (clip) setupTrim(clip, Boolean(trimLocked));
}
async function selectClip(index) {
  if (index === selected) return;
  try { await persistTrim(); }
  catch (error) { return notify(error.message, true); }
  selected = index;
  render();
}
function clipSourceRange(clip) {
  return `${seconds(clip.clip_start + (clip.trim_begin ?? 0))}–${seconds(clip.clip_start + (clip.trim_end ?? clip.clip_end - clip.clip_start))}`;
}
function setupTrim(clip, locked) {
  const video = document.querySelector('#review-video');
  const beginInput = document.querySelector('#trim-begin');
  const endInput = document.querySelector('#trim-end');
  const editor = {clipIndex:clip.index, duration:clip.clip_end - clip.clip_start,
    begin:clip.trim_begin ?? 0, end:clip.trim_end ?? clip.clip_end - clip.clip_start,
    savedBegin:clip.trim_begin ?? 0, savedEnd:clip.trim_end ?? clip.clip_end - clip.clip_start,
    locked, saving:null};
  trimEditor = editor;
  const paint = () => {
    if (trimEditor !== editor) return;
    const duration = editor.duration || 1;
    beginInput.max = duration;
    endInput.max = duration;
    beginInput.value = editor.begin;
    endInput.value = editor.end;
    document.querySelector('#trim-begin-time').textContent = preciseSeconds(editor.begin);
    document.querySelector('#trim-end-time').textContent = preciseSeconds(editor.end);
    document.querySelector('#trim-source-time').textContent = `${preciseSeconds(clip.clip_start + editor.begin)}–${preciseSeconds(clip.clip_start + editor.end)} in source`;
    document.querySelector('#trim-duration').textContent = `${(editor.end - editor.begin).toFixed(1)} sec selected`;
    const left = 100 * editor.begin / duration;
    const right = 100 * editor.end / duration;
    Object.assign(document.querySelector('#trim-selection').style, {left:`${left}%`,width:`${right-left}%`});
    document.querySelector('#trim-playhead').style.left = `${100 * Math.min(video.currentTime || 0, duration) / duration}%`;
    document.querySelector('#trim-status').textContent = editor.locked ? 'Locked after upload' :
      editor.saving ? 'Saving crop…' : editor.begin !== editor.savedBegin || editor.end !== editor.savedEnd ? 'Unsaved crop' : 'Saved';
    for (const control of [beginInput, endInput, document.querySelector('#set-trim-begin'),
      document.querySelector('#set-trim-end'), document.querySelector('#reset-trim')]) control.disabled = editor.locked || Boolean(editor.saving);
  };
  editor.paint = paint;
  video.addEventListener('loadedmetadata', () => {
    if (!Number.isFinite(video.duration) || video.duration <= 0) return;
    editor.duration = video.duration;
    if (!clip.trim_file) editor.end = editor.savedEnd = video.duration;
    else editor.end = Math.min(editor.end, video.duration);
    paint();
  });
  video.addEventListener('timeupdate', () => {
    if (video.currentTime >= editor.end && !video.paused) video.pause();
    paint();
  });
  video.addEventListener('play', () => {
    if (video.currentTime < editor.begin || video.currentTime >= editor.end) video.currentTime = editor.begin;
  });
  for (const [input, edge] of [[beginInput,'begin'],[endInput,'end']]) {
    input.addEventListener('input', () => {
      const value = Number(input.value);
      editor[edge] = edge === 'begin' ? Math.min(value, editor.end - 0.1) : Math.max(value, editor.begin + 0.1);
      editor[edge] = Math.max(0, Math.min(editor.duration, editor[edge]));
      video.pause();
      video.currentTime = editor[edge];
      paint();
    });
    input.addEventListener('change', () => persistTrim().catch(error => notify(error.message, true)));
  }
  document.querySelector('#play-trim').addEventListener('click', () => {
    video.currentTime = editor.begin;
    video.play().catch(error => notify(error.message, true));
  });
  for (const [id,edge] of [['set-trim-begin','begin'],['set-trim-end','end']]) {
    document.getElementById(id).addEventListener('click', () => {
      editor[edge] = edge === 'begin' ? Math.min(video.currentTime, editor.end - 0.1) :
        Math.min(editor.duration, Math.max(video.currentTime, editor.begin + 0.1));
      paint();
      persistTrim().catch(error => notify(error.message, true));
    });
  }
  document.querySelector('#reset-trim').addEventListener('click', () => {
    editor.begin = 0;
    editor.end = editor.duration;
    paint();
    persistTrim().catch(error => notify(error.message, true));
  });
  paint();
}
async function persistTrim() {
  const editor = trimEditor;
  if (!editor || editor.locked) return;
  if (editor.saving) await editor.saving;
  if (editor.begin === editor.savedBegin && editor.end === editor.savedEnd) return;
  const begin = Number(editor.begin.toFixed(3));
  const end = Number(editor.end.toFixed(3));
  editor.saving = api(`/api/jobs/${job.id}/trim`, {index:editor.clipIndex, begin, end});
  editor.paint();
  try {
    const data = await editor.saving;
    job = data.job;
    editor.savedBegin = editor.begin;
    editor.savedEnd = editor.end;
    const listTime = document.querySelector(`.clip-item[data-index="${selected}"] .clip-time`);
    if (listTime) listTime.textContent = clipSourceRange(job.clips[selected]);
  } finally {
    editor.saving = null;
    editor.paint();
  }
}
async function saveReview(decision, advance) {
  const clip = job.clips[selected];
  const payload = {index:clip.index, decision, title:document.querySelector('#clip-title').value,
    scoreAtTouch:document.querySelector('#clip-score').value, notes:document.querySelector('#clip-notes').value};
  try {
    await persistTrim();
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
