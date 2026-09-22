'use strict';
const $ = id => document.getElementById(id);
const form = $('settings-form');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration = seconds => {const m = Math.floor(seconds / 60); return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2,'0')}m` : m === 0 && seconds > 0 ? '<1m' : `${m}m`;};
const clock = seconds => {const s = Math.max(0, Math.floor(seconds)); return s >= 3600 ? `${Math.floor(s/3600)}:${String(Math.floor(s/60)%60).padStart(2,'0')}:${String(s%60).padStart(2,'0')}` : `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
const dateLabel = (date, opts={month:'short',day:'numeric'}) => new Date(`${date}T12:00:00Z`).toLocaleDateString(undefined,{timeZone:'UTC',...opts});
const stamp = (seconds, zone, opts={month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}) => new Date(seconds*1000).toLocaleString(undefined,{timeZone:zone,...opts});
function markup(id, html) { if ($(id).innerHTML !== html) $(id).innerHTML = html; }
let selectedWeek=null, selectedReport=null, token='', data=null, dirty=false, editVersion=0, initialized=false;
let refreshing=false, online=false, emailBusy=false, emailCooldown=0, timerState=null, timerSyncedAt=0, timerBusy=false;

async function post(path, payload) {
  const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}
function query() {return selectedReport ? '?report='+selectedReport : selectedWeek ? '?week='+selectedWeek : '';}
async function refresh() {
  if (refreshing) return;
  refreshing=true;
  const selection=query();
  try {
    const response=await fetch('/api/dashboard'+selection);
    if (!response.ok) throw new Error('Dashboard unavailable');
    const result=await response.json();
    if (selection!==query()) return;
    data=result; token=data.token; online=true;
    $('connection-error').hidden=true;
    renderDashboard();
  } catch {
    online=false;
    $('connection-error').hidden=false;
    $('connection-error').textContent='Tracker disconnected. Your saved history is safe. Start RobloxStats to resume; this page reconnects automatically.';
    $('status').textContent='Tracker unavailable';
    $('status').classList.remove('active');
  } finally {refreshing=false; updateControls();}
}
function renderDashboard() {
  $('status').textContent=data.status;
  $('status').classList.toggle('active',Boolean(data.active));
  $('today-value').textContent=duration(data.today.seconds);
  $('week-value').textContent=data.current_total;
  $('average-value').textContent=data.stats.average;
  $('today-detail').textContent=data.active ? 'A game session is being recorded' : 'Connected in-game time';
  $('today-date').textContent=dateLabel(data.today.date,{weekday:'short',month:'short',day:'numeric'});
  const dayIndex=data.current_stats.days.findIndex(d=>d.date===data.today.date);
  const previous=data.previous_stats.days.slice(0,dayIndex+1).reduce((n,d)=>n+d.seconds,0);
  const diff=data.current_stats.total-previous;
  $('week-comparison').textContent=`${diff>=0?'↑':'↓'} ${duration(Math.abs(diff))} vs. same days last week`;
  $('live-title').textContent=data.active ? `Playing ${data.active.place ? 'place '+data.active.place : 'a Roblox experience'}` : 'No game session right now';
  $('live-description').textContent=data.active ? 'Connected game time is being saved automatically.' : 'The home screen doesn’t count. Tracking starts when you join a game.';
  if (!data.active && /paused|error|unavailable/i.test(data.status)) $('live-description').textContent=data.status;
  $('live-duration').textContent=data.active ? clock(data.active.last_seen-data.active.start) : '—';
  renderChart(); renderSessions(); renderReports(); renderTimer(data.timer);
  if (!dirty) {
    for (const [key,value] of Object.entries(data.settings)) {
      const input=form.elements.namedItem(key); if (!input) continue;
      if (input.type==='checkbox') input.checked=value;
      else if (document.activeElement!==input) input.value=value;
    }
    initialized=true;
  }
  const provider=data.email_provider==='resend'?'Resend':'SMTP';
  $('email-provider-label').textContent=provider+' email delivery';
  $('email-connection-status').textContent=data.email_configured?'Configured':'Setup needed';
  $('email-connection-status').classList.toggle('is-ready',data.email_configured);
  $('email-connection-description').textContent=data.email_configured?'Credentials are available. Accepted emails appear in your activity log.':'Connect your email provider to send recaps and test messages.';
  $('email-sender').textContent=data.email_sender||'No sender configured';
  $('sender-guidance').textContent=data.email_sender?.includes('@resend.dev')?'This test sender can only email your Resend account address.':'This address is used for your reports and samples.';
  $('smtp-note').textContent=data.email_provider==='resend'?'Keep the tracker running at your scheduled delivery time.':'Manual email buttons use Resend. Scheduled reports can also use SMTP.';
  emailCooldown=performance.now()+(data.email_cooldown||0)*1000;
  updateSchedulePreview();
  if ($('report-preview').open) renderPreview();
}
function renderChart() {
  const s=data.stats;
  $('week-picker').value=s.week;
  $('week-description').textContent=`${dateLabel(s.days[0].date)} – ${dateLabel(s.days[6].date,{month:'short',day:'numeric',year:'numeric'})}${selectedReport?' · saved report':''}`;
  $('chart-zone').textContent=s.timezone;
  $('best-day').textContent=s.most_played;
  $('selected-total').textContent=s.formatted;
  $('active-days').textContent=s.days.filter(d=>d.seconds>0).length;
  const max=Math.max(1800,...s.days.map(d=>d.seconds));
  markup('chart',s.days.map(d=>`<div class="bar-group ${d.date===data.today.date?'is-today':''}" aria-label="${d.day}: ${esc(duration(d.seconds))}"><span class="bar-value">${esc(duration(d.seconds))}</span><svg width="64%" height="${Math.max(3,d.seconds/max*165)}" viewBox="0 0 70 165" preserveAspectRatio="none" aria-hidden="true"><rect width="70" height="165" rx="5" fill="${d.date===data.today.date?'#66894e':'#cdddb7'}"/></svg><span class="bar-label">${d.day.slice(0,3)}${d.date===data.today.date?' ·':''}</span></div>`).join(''));
}
function renderSessions() {
  if (!data) return;
  const search=$('session-search').value.trim().toLowerCase(), filter=$('session-filter').value, zone=data.settings.timezone;
  const rows=data.sessions.filter(r=> (filter==='all'||(filter==='estimated'?r.estimated:!r.estimated)) && `${r.place||'Roblox experience'} ${r.reason||'in progress'} ${stamp(r.start,zone)}`.toLowerCase().includes(search));
  $('session-count').textContent=`${rows.length} OF ${data.sessions.length} RECENT`;
  markup('session-rows',rows.length?rows.map(r=>`<tr><td><div class="session-place"><span class="game-icon" aria-hidden="true">◈</span><div>${r.place?'Place '+esc(r.place):'Roblox experience'}<small>${esc(r.reason?.replaceAll('_',' ')||'Playing now')}${r.estimated?' · estimated':''}</small></div></div></td><td>${esc(stamp(r.start,zone))}<small>→ ${r.end===null?'In progress':esc(stamp(r.end,zone))}</small></td><td title="${r.duration.toFixed(3)} seconds">${r.estimated?'≈ ':''}${esc(duration(r.duration))}</td></tr>`).join(''):`<tr><td colspan="3"><div class="empty"><span class="empty-icon">◷</span>${data.sessions.length?'No sessions match your filters.':'Your next adventure starts here.<br>Join a game to record your first session.'}</div></td></tr>`);
}
function renderReports() {
  const next=data.next_report;
  $('next-report-date').textContent=next?stamp(Date.parse(next.at)/1000,data.settings.timezone,{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Email delivery is off';
  $('next-report-period').textContent=next?`${next.overdue?'Pending delivery · ':''}Covers ${dateLabel(next.week)}–${dateLabel(next.through)} · ${data.settings.timezone}`:'Completed reports are still saved locally.';
  markup('report-list',data.reports.length?data.reports.map(r=>`<button class="report-item" data-week="${r.week}"><strong>${dateLabel(r.week,{month:'short',day:'numeric',year:'numeric'})} · ${esc(r.payload.formatted)}</strong><small>${esc(r.status==='ready'?'Saved · awaiting delivery':r.status==='sent'?'Sent to provider':r.status)}</small>${r.error?`<small>${esc(r.error)}</small>`:''}</button>`).join(''):'<div class="empty">Your first completed week will appear here.<br>You can preview this week at any time.</div>');
  $('delivery-count').textContent=data.email_activity.length;
  markup('delivery-list',data.email_activity.length?data.email_activity.map(r=>`<div class="delivery-row"><strong>${r.kind==='weekly'?'Week-so-far report':'Sample email'}</strong><span class="delivery-status ${esc(r.status)}">${esc(r.status)}</span><small>${esc(r.recipient)} · ${esc(stamp(r.created_at,data.settings.timezone,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}</small>${r.error?`<small>${esc(r.error)}</small>`:''}${r.provider_id?`<small>Provider ID: ${esc(r.provider_id)}</small>`:''}</div>`).join(''):'<div class="empty">New manual email attempts appear here.<br>“Accepted” means the provider received it, not confirmed inbox delivery.</div>');
}
$('report-list').addEventListener('click',e=>{const button=e.target.closest('[data-week]'); if(!button)return;selectedReport=button.dataset.week;selectedWeek=button.dataset.week;refresh();$('activity').scrollIntoView({behavior:'smooth'});});
$('session-search').addEventListener('input',renderSessions);
$('session-filter').addEventListener('change',renderSessions);
$('week-picker').addEventListener('change',e=>{selectedReport=null;selectedWeek=e.target.value||null;refresh();});
for (const [id,delta] of [['prev-week',-7],['next-week',7]]) $(id).addEventListener('click',()=>{const d=new Date($('week-picker').value+'T12:00:00Z');if(Number.isNaN(+d))return;d.setUTCDate(d.getUTCDate()+delta);selectedReport=null;selectedWeek=d.toISOString().slice(0,10);refresh();});
$('current-week').addEventListener('click',()=>{selectedWeek=null;selectedReport=null;refresh();});
function updateSchedulePreview() {
  const f=form.elements;
  $('schedule-preview').textContent=f.emails_enabled.checked?`Every ${f.report_day.selectedOptions[0].textContent} at ${f.report_time.value||'09:00'} · ${f.timezone.value||'your time zone'}. Sends a completed Monday–Sunday week.`:'Email delivery is off. Weekly reports remain available on this device.';
  f.email.required=f.emails_enabled.checked;
  $('settings-state').textContent=dirty?'UNSAVED CHANGES':'PREFERENCES';
  $('settings-state').classList.toggle('dirty',dirty);
}
form.addEventListener('input',()=>{dirty=true;editVersion++;updateSchedulePreview();});
form.addEventListener('change',()=>{dirty=true;editVersion++;updateSchedulePreview();});
form.addEventListener('submit',async event=>{
  event.preventDefault();
  const values=Object.fromEntries(new FormData(form)); values.emails_enabled=form.elements.emails_enabled.checked;values.report_day=Number(values.report_day);
  const submittedVersion=editVersion;
  $('save-preferences').disabled=true; $('save-status').textContent='Saving preferences…';
  try {await post('/api/settings',values);dirty=editVersion!==submittedVersion;$('save-status').textContent=dirty?'Saved. Your newer edits are still unsaved.':'Saved. The next report date reflects your updated schedule.';updateSchedulePreview();await refresh();}
  catch(error){$('save-status').textContent=error.message;}
  finally{$('save-preferences').disabled=!online;}
});
function renderTimer(state) {
  timerState=state;timerSyncedAt=performance.now();
  const active=['running','expired','exiting'].includes(state.status);
  $('timer-start').hidden=active;$('timer-minutes').disabled=active;
  document.querySelectorAll('[data-minutes]').forEach(b=>{b.disabled=active;b.setAttribute('aria-pressed',String(Number(b.dataset.minutes)===Number($('timer-minutes').value)));});
  $('timer-cancel').hidden=state.status!=='running'||state.snoozed;
  $('timer-snooze').hidden=state.snoozed;
  $('timer-badge').textContent=state.status==='running'?(state.snoozed?'FINAL 2 MIN':'RUNNING'):state.status==='expired'?'TIME IS UP':'READY';
  $('timer-help').textContent=state.snoozed&&state.status==='running'?'Final two minutes. Roblox will close automatically when this countdown ends.':'At time’s up, snooze once for 2 minutes or quit Roblox. After snooze, Roblox closes automatically.';
  $('timer-dialog-copy').textContent=state.snoozed?'Your snooze is over. Exit Roblox to finish your break timer.':'Your Roblox timer is done. Snooze once for two more minutes, or exit Roblox now.';
  $('timer-dialog-error').textContent=state.error;
  if(state.status==='expired'&&!$('timer-dialog').open)$('timer-dialog').showModal();
  if(state.status!=='expired'&&$('timer-dialog').open)$('timer-dialog').close();
  updateCountdown();
}
function updateCountdown() {
  if(!timerState)return;
  const running=timerState.status==='running';
  const left=Math.max(0,Math.ceil(timerState.remaining-(performance.now()-timerSyncedAt)/1000));
  $('timer-countdown').textContent=running?clock(left):timerState.status==='expired'?'Time is up':timerState.status==='exiting'?'Closing…':clock(Number($('timer-minutes').value||30)*60);
  $('timer-face-label').textContent=running?(timerState.snoozed?'FINAL SNOOZE':'UNTIL YOUR BREAK'):timerState.status==='finished'?'LAST TIMER COMPLETED':'YOUR NEXT PAUSE';
  const total=timerState.duration||Math.max(timerState.remaining,1);
  $('timer-ring').setAttribute('stroke-dashoffset',String(running?477.52*(1-Math.min(1,left/total)):0));
}
async function timerAction(action) {
  if(timerBusy||!online)return;timerBusy=true;updateControls();$('timer-message').textContent='';
  try{renderTimer(await post('/api/timer',{action,id:timerState?.id,minutes:Number($('timer-minutes').value)}));}
  catch(error){$('timer-message').textContent=error.message;$('timer-dialog-error').textContent=error.message;}
  finally{timerBusy=false;updateControls();}
}
$('timer-form').addEventListener('submit',e=>{e.preventDefault();timerAction('start');});
$('timer-cancel').addEventListener('click',()=>timerAction('cancel'));
$('timer-snooze').addEventListener('click',()=>timerAction('snooze'));
$('timer-exit').addEventListener('click',()=>timerAction('exit'));
$('timer-dialog').addEventListener('cancel',e=>e.preventDefault());
for(const button of document.querySelectorAll('[data-minutes]'))button.addEventListener('click',()=>{$('timer-minutes').value=button.dataset.minutes;renderTimer(timerState);});
$('timer-minutes').addEventListener('input',()=>{if(timerState)renderTimer(timerState);});
function updateControls() {
  const left=Math.max(0,Math.ceil((emailCooldown-performance.now())/1000));
  const disabled=!online||emailBusy||left>0;
  $('send-sample-email').disabled=disabled;$('send-weekly-email').disabled=disabled;$('preview-send').disabled=disabled;
  $('send-sample-email').textContent=emailBusy?'Sending…':left?`Try again in ${left}s`:'Send sample email';
  $('send-weekly-email').textContent=emailBusy?'Sending…':left?`Try again in ${left}s`:'Send weekly report now ↗';
  $('preview-send').textContent=emailBusy?'Sending…':left?`Available in ${left}s`:'Send this week’s report';
  for(const id of ['timer-start','timer-cancel','timer-snooze','timer-exit'])$(id).disabled=!online||timerBusy;
  $('preview-report').disabled=!online;
}
async function sendManualEmail(weekly=false) {
  if(emailBusy||!online||performance.now()<emailCooldown)return;
  const recipient=form.elements.email;
  if(!recipient.value||!recipient.checkValidity()){$('sample-email-status').textContent='Enter a valid recipient email address first.';$('preview-status').textContent='Enter a recipient in Preferences first.';recipient.focus();return;}
  emailBusy=true;updateControls();
  const label=weekly?'this week’s report':'a sample';
  $('sample-email-status').textContent=`Sending ${label} to ${recipient.value}…`;
  $('preview-status').textContent=$('sample-email-status').textContent;
  try{const result=await post(weekly?'/api/email/weekly':'/api/email/sample',{email:recipient.value});emailCooldown=performance.now()+30000;$('sample-email-status').textContent=result.message;$('preview-status').textContent=result.message;}
  catch(error){$('sample-email-status').textContent=error.message;$('preview-status').textContent=error.message;}
  finally{emailBusy=false;await refresh();updateControls();}
}
$('send-sample-email').addEventListener('click',()=>sendManualEmail());
$('send-weekly-email').addEventListener('click',()=>sendManualEmail(true));
function renderPreview() {
  if(!data)return;const report=data.current_stats;
  $('preview-period').textContent=`${dateLabel(report.days[0].date)}–${dateLabel(report.days[6].date)} · ${report.timezone}`;
  $('preview-recipient').textContent='To: '+(form.elements.email.value||'Choose a recipient in Preferences');
  markup('preview-days',report.days.map(d=>`<tr><td>${d.day}</td><td>${esc(duration(d.seconds))}</td></tr>`).join(''));
  $('preview-total').textContent=report.formatted;
}
$('preview-report').addEventListener('click',()=>{renderPreview();$('preview-status').textContent='';$('report-preview').showModal();});
$('close-preview').addEventListener('click',()=>$('report-preview').close());
$('preview-send').addEventListener('click',()=>sendManualEmail(true));
function updateNav(){const hash=location.hash||'#overview';for(const link of document.querySelectorAll('nav a')){link.classList.toggle('selected',link.getAttribute('href')===hash);if(link.getAttribute('href')===hash)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}}
window.addEventListener('hashchange',updateNav);updateNav();
setInterval(()=>{updateCountdown();updateControls();},250);
setInterval(()=>{if(!document.hidden)refresh();},2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
refresh();
