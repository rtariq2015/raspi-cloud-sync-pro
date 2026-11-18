'use strict';
let files = [];
let googlePreparationTimer = null;
let googlePreparationRetryUntil = 0;
let googleControlTimer = null;
let googleControlRetryUntil = 0;
let backupRefreshTimer = null;
let backupRefreshFlight = null;
let advancedReady = false;
let advancedRefreshFlight = null;
const $ = id => document.getElementById(id);
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if(className) el.className = className; return el; };
const bytes = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : n < 1073741824 ? `${(n/1048576).toFixed(1)} MB` : `${(n/1073741824).toFixed(1)} GB`;
function message(value, error=false) { $('message').textContent=value; $('message').className=error?'notice error':'notice'; $('message').hidden=false; }
async function api(path, options={}) { const response=await fetch(path, {credentials:'same-origin', ...options}); if(response.status===401) {location.href='/login'; throw new Error('Sign in again.');} if(!response.ok) throw new Error(response.status===413?'The server rejected the upload request. Maximum file size: 100 MiB.':response.status===403?'Your account cannot perform this action.':'The request could not be completed. Please try again.'); return response.json(); }
function view(name) { if(!$(name)?.classList.contains('view')) return; document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name); document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===name)); $('view-label').textContent=({overview:'Overview',files:'Files',backups:'Backups',network:'Wi-Fi & access',history:'History',advanced:'Advanced Tools'})[name]; }
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>view(el.dataset.view)));
function fileRows() { const term=$('file-search').value.toLowerCase(); const selected=files.filter(file=>file.name.toLowerCase().includes(term)); $('file-rows').replaceChildren(); $('files-empty').hidden=selected.length>0; for(const file of selected) {const row=document.createElement('tr'); row.append(text('td',file.name),text('td',bytes(file.size))); const state=document.createElement('td'); state.append(text('span',file.cloud_verified?'Verified':'Staged locally','tag '+(file.cloud_verified?'verified':'staged'))); row.append(state); const actions=text('td',''); actions.className='row-actions'; const query=new URLSearchParams({batch:file.batch,name:file.name}); const preview=text('button','Preview'); preview.addEventListener('click',async()=>{try{const data=await api('/api/preview?'+query); $('preview-name').textContent=file.name; $('preview-text').textContent=data.supported?data.text+(data.truncated?'\n\n[Preview limited to 64 KiB]':''):'This file has no text preview. Use Download to open it on your device.'; $('preview-dialog').showModal();}catch(error){message(error.message,true);}}); const download=text('a','Download'); download.href='/api/download?'+query; actions.append(preview,download); row.append(actions); $('file-rows').append(row);} }
function workerReport(report) {
  const labels={not_connected:'No worker report connected.',unavailable:'Worker report unavailable.',invalid:'Worker report could not be validated.',stale:'Last worker report is older than five minutes.',error:'The last worker attempt reported an error.',observed:'Worker report received.'};
  $('worker-state').textContent=labels[report?.state]||labels.unavailable;
  if(report?.state==='observed'||report?.state==='stale') {
    const age=report.age_seconds<60?report.age_seconds+' seconds':Math.floor(report.age_seconds/60)+' minutes';
    const queue=report.pending_jobs===null?'Queue unknown':report.pending_jobs+' jobs pending';
    const policy=report.network_policy_allows_upload?'Last observed network policy allowed work':'Last observed network policy paused work';
    $('worker-detail').textContent=queue+' · '+report.destination_count+' destinations · Reported '+age+' ago. '+policy+'. '+(report.reconciliation_pending?'More completed batches await reconciliation. ':'')+'This is a last report, not a live heartbeat or proof of cloud-account setup.';
  } else $('worker-detail').textContent='Reports do not start a worker or authorize cloud accounts. A parked scheduler may have no recent report. File verification is shown separately in history.';
}
function backupTimestamp(value) {
  if(typeof value!=='number'||!Number.isFinite(value)||value<0) return 'Unknown';
  const date=new Date(value*1000);
  return Number.isFinite(date.getTime())?date.toLocaleString():'Unknown';
}
function backupReport(report) {
  const observed=['current','stale'].includes(report?.state);
  const available=observed&&report?.history?.state==='available';
  const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
  const counts=report?.history?.counts||{};
  const verified=available?count(counts.verified):null;
  const pendingStates=['queued','retry','uploading','uploaded','verifying'];
  const pending=available&&pendingStates.every(state=>count(counts[state])!==null)?pendingStates.reduce((sum,state)=>sum+counts[state],0):null;
  for(const [id,value] of [['verified-count',verified],['backup-verified',verified],['backup-pending',pending],['backup-retrying',available?count(counts.retry):null],['backup-failed',available?count(counts.failed):null]]) $(id).textContent=value===null?'—':String(value);
  const reportLabels={current:'Read-only backup report received.',stale:'The backup report is stale. Current service health is unknown.',not_connected:'The read-only backup status bridge is not connected.',unavailable:'Backup status is unavailable. Current service health is unknown.',invalid:'The backup report could not be validated. Current service health is unknown.'};
  $('backup-report-state').textContent=reportLabels[report?.state]||reportLabels.unavailable;
  $('backup-updated').textContent=observed?'Report observed: '+backupTimestamp(report.observed_at)+' · '+(Number.isSafeInteger(report.age_seconds)?report.age_seconds:'Unknown')+' seconds ago.':'No usable observation is available.';
  const serviceLabels={running:'Running',stopped:'Stopped',failed:'Failed',unknown:'Unknown'};
  const service=serviceLabels[report?.service?.state]||serviceLabels.unknown;
  $('backup-service').textContent=observed?(report.state==='stale'?'Last observed: '+service+' (stale)':'Observed: '+service):'Unknown';
  const google=report?.google_drive;
  $('backup-google').textContent=observed&&google?.state==='historically_verified'?'Google Drive backup verified previously':observed&&google?.state==='recorded'?'Google Drive destination recorded':'Google Drive status unknown';
  $('backup-google-detail').textContent=observed&&google?.state==='historically_verified'?'Last verified backup: '+backupTimestamp(google.last_verified_at)+'. Current Google access has not been tested.':'Current Google access has not been tested. Dashboard sign-in setup is separate from the existing USB backup worker.';
  const destinations=observed&&Array.isArray(report.destinations)?report.destinations:[];
  $('backup-destinations').textContent=destinations.length?destinations.map(item=>item.label).join(' · ')+' · Recorded backup destinations; private identifiers hidden.':'Destination information unavailable.';
  $('backup-progress').replaceChildren();
  const jobs=available&&Array.isArray(report.jobs)?report.jobs:[];
  const stateLabels={queued:'Queued',retry:'Waiting to retry',uploading:'Uploading',uploaded:'Uploaded — awaiting verification',verifying:'Verifying downloaded contents',verified:'Verified',failed:'Failed'};
  const active=jobs.filter(job=>['uploading','uploaded','verifying'].includes(job.state));
  if(!active.length) $('backup-progress').append(text('p',available?'No transfer-stage job is recorded in the recent history.':'Transfer state unavailable.','muted'));
  for(const job of active) $('backup-progress').append(text('p',job.label+' · '+(stateLabels[job.state]||'Unknown'),'muted'));
  $('backup-progress').append(text('p','Progress shows recorded job stages. The worker does not provide reliable live byte counts or percentages.','fine'));
  $('backup-recent').replaceChildren();
  if(!jobs.length) $('backup-recent').append(text('p',available?'No backup jobs recorded yet.':'Backup history unavailable.','empty'));
  for(const job of jobs.slice(0,5)) {
    const line=text('div','','file-line'),details=text('div',job.label);
    details.append(text('small',job.destination+' · '+backupTimestamp(job.updated_at)));
    if(job.error_message) details.append(text('small',job.error_message));
    line.append(details,text('span',stateLabels[job.state]||'Unknown'));
    $('backup-recent').append(line);
  }
  $('history-rows').replaceChildren();
  $('history-empty').hidden=jobs.length>0;
  $('history-empty').textContent=available?'No backup history yet.':'Backup history unavailable.';
  for(const job of jobs) {
    const row=document.createElement('tr');
    let detail=job.error_message||'—';
    if(job.state==='retry') detail+=' · Next retry: '+backupTimestamp(job.next_attempt);
    row.append(text('td',job.label),text('td',job.destination),text('td',stateLabels[job.state]||'Unknown'),text('td',job.attempts),text('td',bytes(job.size)),text('td',backupTimestamp(job.updated_at)),text('td',detail));
    $('history-rows').append(row);
  }
  workerReport(observed?report.worker:null);
}
function scheduleBackupRefresh() {
  if(backupRefreshTimer!==null) {clearTimeout(backupRefreshTimer);backupRefreshTimer=null;}
  if(document.visibilityState==='hidden') return;
  backupRefreshTimer=setTimeout(()=>{backupRefreshTimer=null;refreshBackups();},15000);
}
function refreshBackups() {
  if(backupRefreshFlight!==null) return backupRefreshFlight;
  if(backupRefreshTimer!==null) {clearTimeout(backupRefreshTimer);backupRefreshTimer=null;}
  backupRefreshFlight=(async()=>{
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),10000);
    try {backupReport(await api('/api/backups',{signal:controller.signal}));}
    catch(error) {backupReport({state:'unavailable'});}
    finally {clearTimeout(timeout);backupRefreshFlight=null;scheduleBackupRefresh();}
  })();
  return backupRefreshFlight;
}
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='hidden') {
    if(backupRefreshTimer!==null) {clearTimeout(backupRefreshTimer);backupRefreshTimer=null;}
  } else refreshBackups();
});
function advancedReport(report) {
  if(!$('rclone-tools')) return;
  advancedReady=report?.state==='ready'&&report.open_url==='http://127.0.0.1:5573/'&&report.client_tunnel_verified===false;
  $('rclone-tunnels').disabled=!advancedReady;
  if(!advancedReady) $('rclone-tunnels').checked=false;
  $('rclone-open').disabled=!advancedReady||!$('rclone-tunnels').checked;
  $('rclone-admin-state').textContent=advancedReady?'Pi loopback WebGUI reported ready.':report?.state==='disabled'?'Advanced rclone access is disabled.':'Advanced rclone access is unavailable.';
  $('rclone-admin-detail').textContent=advancedReady?'This dashboard has not checked either SSH tunnel on your computer. Open only after both tunnels are running. Your browser will still require the private rclone sign-in.':'Separate local administrator setup and approval are required. This dashboard does not start rclone or expose its administrative interface.';
}
function refreshAdvanced() {
  if(!$('rclone-tools')) return Promise.resolve();
  if(advancedRefreshFlight!==null) return advancedRefreshFlight;
  $('rclone-open').disabled=true;
  advancedRefreshFlight=(async()=>{
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
    try {advancedReport(await api('/api/advanced/rclone',{signal:controller.signal}));}
    catch(error) {advancedReport({state:'unavailable'});}
    finally {clearTimeout(timeout);advancedRefreshFlight=null;}
  })();
  return advancedRefreshFlight;
}
document.getElementById('rclone-check')?.addEventListener('click',refreshAdvanced);
document.getElementById('rclone-tunnels')?.addEventListener('change',()=>{
  $('rclone-open').disabled=!advancedReady||!$('rclone-tunnels').checked;
});
document.getElementById('rclone-open')?.addEventListener('click',()=>{
  if(!advancedReady||!$('rclone-tunnels').checked||$('rclone-open').disabled) return;
  window.open('http://127.0.0.1:5573/','_blank','noopener,noreferrer');
});
function scheduleGoogleControlRefresh() {
  if(googleControlTimer!==null) {clearTimeout(googleControlTimer);googleControlTimer=null;}
  if(performance.now()>=googleControlRetryUntil) return;
  googleControlTimer=setTimeout(async()=>{
    googleControlTimer=null;
    try {destinationSetup(await api('/api/destinations'));}
    catch(error){message(error.message,true);scheduleGoogleControlRefresh();}
  },3000);
}
function googleControlReport(provider) {
  if(googleControlTimer!==null) {clearTimeout(googleControlTimer);googleControlTimer=null;}
  const report=provider.control||{},state=report.control_state||'unavailable',available=provider.control_requests_available===true;
  const labels={not_requested:'No Test requested',test_requested:'Test queued',test_verified:'Test passed — not enabled',test_expired:'Test expired — run Test again',test_failed:'Test failed',enable_requested:'Enable queued',enable_recorded:'Permission saved — service status unconfirmed',enable_failed:'Permission was not saved',disable_requested:'New backups paused',disable_recorded:'New backups paused',disable_failed:'New backups paused — check service',expired:'Request expired',stale_binding:'Setup or sign-in changed',invalid:'Backup status could not be checked',unavailable:'Backup status unavailable'};
  let detail=state==='test_requested'?'Your Test request is queued. Wait for the result before choosing Enable.':
    state==='test_verified'?'The Test reported a matching downloaded copy. New backups still need a separate Enable request.':
    state==='enable_requested'?'Your Enable request is queued. Wait for the result; this does not confirm a running backup service.':
    state==='enable_recorded'?'Permission for new backups is saved. Starting another Test pauses this permission until you choose Enable again.':
    ['disable_requested','disable_recorded','disable_failed'].includes(state)?'New backups are paused. A transfer already in progress may finish.':
    state==='test_expired'?'The previous Test is too old to enable backups. Run Test again.':
    state==='test_failed'?'The Test did not pass. Check setup, then try Test again.':
    state==='expired'?'No result was saved before this request expired. Refresh and try again.':
    state==='stale_binding'?'Your sign-in or Google setup changed. Check preparation and run Test again.':
    ['invalid','unavailable'].includes(state)?'Backup status could not be checked. Refresh to try again.':
    'Prepare Google Drive, then run Test before enabling new backups.';
  if(!available) detail='Automatic backup setup is still being completed on this device.';
  detail+=' This panel has not checked whether the backup service is running or which Google account is connected.';
  if(report.historical&&Number.isFinite(report.age_seconds)) detail+=' Last result: '+Math.floor(report.age_seconds)+' seconds ago.';
  if($('google-control-status')) $('google-control-status').textContent=labels[state]||labels.unavailable;
  if($('google-control-detail')) $('google-control-detail').textContent=detail;
  const pending=['test_requested','enable_requested','disable_requested'].includes(state);
  const test=$('google-test-button'),enable=$('google-enable-button'),disable=$('google-disable-button');
  if(test) test.disabled=!available||pending||['test_verified','invalid','unavailable'].includes(state)||provider.preparation?.preparation_state!=='prepared_untested';
  if(enable) enable.disabled=!available||state!=='test_verified';
  if(disable) disable.disabled=!available||['disable_requested','disable_recorded'].includes(state);
  if(pending&&available) {
    if(!googleControlRetryUntil) googleControlRetryUntil=performance.now()+Math.max(0,600-(Number.isFinite(report.age_seconds)?Math.max(0,report.age_seconds):0))*1000;
  } else if(state!=='unavailable'||!available) googleControlRetryUntil=0;
  if(available&&performance.now()<googleControlRetryUntil) scheduleGoogleControlRefresh();
}
function scheduleGooglePreparationRefresh() {
  if(googlePreparationTimer!==null) {clearTimeout(googlePreparationTimer);googlePreparationTimer=null;}
  if(performance.now()>=googlePreparationRetryUntil) return;
  googlePreparationTimer=setTimeout(async()=>{
    googlePreparationTimer=null;
    try {destinationSetup(await api('/api/destinations'));}
    catch(error){message(error.message,true);scheduleGooglePreparationRefresh();}
  },3000);
}
function destinationSetup(data) {
  if(googlePreparationTimer!==null) {clearTimeout(googlePreparationTimer);googlePreparationTimer=null;}
  let preparationPending=false;
  for(const provider of data.providers||[]) {
    const label=$('oauth-'+provider.provider+'-status');
    if(label) label.textContent=provider.authorization==='authorization_saved_untested'?'Authorization saved · Not tested':provider.client_ready?'Ready for provider sign-in':'Private app setup required';
    const button=document.querySelector('.oauth-connect[data-provider="'+provider.provider+'"]');
    if(button) {button.disabled=provider.authorization==='authorization_saved_untested'; button.dataset.ready=provider.client_ready?'yes':'no';}
    if(provider.provider==='google_drive') {
      if(provider.control) googleControlReport(provider);
      const report=provider.preparation||{},state=report.preparation_state||'unavailable';
      const labels={not_requested:'Configuration not prepared',requested:'Preparation requested',expired:'Preparation request expired',prepared_untested:'Configuration prepared · Test required',stale_source:'Authorization changed · Prepare again',stale_receipt:'Previous preparation needs review',invalid:'Preparation status could not be validated',unavailable:'Preparation status unavailable'};
      $('google-preparation-status').textContent=labels[state]||labels.unavailable;
      let detail=state==='requested'?'Waiting for the device setup service. Automatic backups remain off.':state==='prepared_untested'?'This is the last preparation report. The current worker configuration has not been checked; a cloud test and explicit enablement are still required.':state==='expired'?'No completed preparation was reported before this request expired. You can request preparation again.':'Preparation does not create a cloud folder, test access or enable automatic backups.';
      if(!provider.preparation_requests_available) detail='The device setup service is not connected yet. Automatic backups remain off.';
      if(report.historical&&Number.isFinite(report.age_seconds)) detail+=' Reported '+Math.floor(report.age_seconds)+' seconds ago.';
      $('google-preparation-detail').textContent=detail;
      const prepare=$('google-prepare-button'),folder=$('google-folder-name');
      const disabled=!provider.preparation_requests_available||provider.authorization!=='authorization_saved_untested'||['requested','invalid','unavailable'].includes(state);
      if(prepare) prepare.disabled=disabled;
      if(folder) folder.disabled=disabled;
      if(state==='requested'&&provider.preparation_requests_available) {
        if(!googlePreparationRetryUntil) googlePreparationRetryUntil=performance.now()+Math.max(0,600-(Number.isFinite(report.age_seconds)?Math.max(0,report.age_seconds):0))*1000;
        preparationPending=performance.now()<googlePreparationRetryUntil;
      } else if(state==='unavailable'&&provider.preparation_requests_available) {
        preparationPending=performance.now()<googlePreparationRetryUntil;
      } else googlePreparationRetryUntil=0;
    }
  }
  if(preparationPending) scheduleGooglePreparationRefresh();
}
document.getElementById('google-prepare-form')?.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=$('google-prepare-button'),folder=$('google-folder-name');
  if(button.disabled||folder.disabled) return;
  const name=folder.value.trim();button.disabled=true;folder.disabled=true;
  try {
    const response=await fetch('/api/destinations/google_drive/prepare',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify({folder_name:name})});
    if(response.status===401) {location.href='/login';return;}
    const data=await response.json();
    if(!response.ok) {
      const errors={google_setup_service_unavailable:'The device setup service is not ready yet.',google_folder_name_invalid:'Use 1–64 letters or numbers, with spaces, periods, underscores or hyphens inside the name.',google_setup_source_unavailable:'Finish Google sign-in before preparing a backup destination.'};
      throw new Error(response.status===403?'Your sign-in expired or this account cannot prepare backups. Refresh and sign in again.':errors[data.error]||'Preparation could not be requested. Refresh to check its status before trying again.');
    }
    if(data.preparation?.preparation_state==='requested') googlePreparationRetryUntil=performance.now()+600000;
    message(data.preparation?.preparation_state==='prepared_untested'?'Configuration was already prepared. A cloud test is still required; automatic backups remain off.':'Preparation requested. Automatic backups remain off.');
  }catch(error){message(error.message,true);}
  finally{try{destinationSetup(await api('/api/destinations'));}catch(error){message(error.message,true);scheduleGooglePreparationRefresh();}}
});
document.getElementById('google-control-form')?.addEventListener('submit',async event=>{
  event.preventDefault();
  const action=event.submitter?.value;
  if(!['test','enable','disable'].includes(action)||event.submitter.disabled) return;
  for(const id of ['google-test-button','google-enable-button','google-disable-button']) if($(id)) $(id).disabled=true;
  try {
    const response=await fetch('/api/destinations/google_drive/control',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify({action})});
    if(response.status===401) {location.href='/login';return;}
    const data=await response.json();
    if(!response.ok) {
      const errors={google_control_service_unavailable:'Automatic backup setup is still being completed on this device.',google_control_preparation_required:'Prepare Google Drive before running Test.',google_control_test_required:'Run Test successfully before choosing Enable.',google_control_source_changed:'Your Google setup changed. Prepare it again, then run Test.',google_control_owner_changed:'Your sign-in changed. Refresh and sign in again.'};
      throw new Error(response.status===403?'Your sign-in expired or this account cannot change backup permission.':errors[data.error]||'The control request could not be recorded. Refresh to check its status.');
    }
    if(['test_requested','enable_requested','disable_requested'].includes(data.control?.control_state)) googleControlRetryUntil=performance.now()+600000;
    const state=data.control?.control_state;
    message(state==='test_verified'?'A Test result is already saved. This Test does not enable backups.':state==='enable_recorded'?'Permission is already saved. Backup service status is unconfirmed.':action==='test'?'Test queued. Wait for a pass before choosing Enable.':action==='enable'?'Enable queued. Wait for the result; this does not confirm a running backup service.':'New backups paused. A transfer already in progress may finish.');
  }catch(error){message(error.message,true);}
  finally{try{destinationSetup(await api('/api/destinations'));}catch(error){message(error.message,true);scheduleGoogleControlRefresh();}}
});
document.querySelectorAll('.oauth-connect').forEach(button=>button.addEventListener('click',async()=>{
  if(button.dataset.ready!=='yes') {message('This provider needs a registered application and exact HTTPS callback configuration installed privately on the Pi before sign-in can begin.',true);return;}
  button.disabled=true;
  try {
    const response=await fetch('/api/destinations/'+encodeURIComponent(button.dataset.provider)+'/connect',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}});
    const data=await response.json();
    if(!response.ok) throw new Error('Provider setup is not ready, or this connection expired. Refresh and try again.');
    const url=new URL(data.authorization_url);
    const approved=button.dataset.provider==='google_drive'?url.origin==='https://accounts.google.com'&&url.pathname==='/o/oauth2/v2/auth':url.origin==='https://www.dropbox.com'&&url.pathname==='/oauth2/authorize';
    if(!approved) throw new Error('The provider authorization address was refused.');
    window.location.assign(url.href);
  } catch(error) {message(error.message||'The connection could not be started.',true);button.disabled=false;}
}));
async function refresh() { const button=$('refresh'); button.disabled=true; try {const [state,listing,destinations]=await Promise.all([api('/api/status'),api('/api/files'),api('/api/destinations'),refreshBackups(),refreshAdvanced()]); destinationSetup(destinations); files=listing.files; $('file-count').textContent=files.length; const wifi=state.network.interfaces?.wlan0; $('wifi-status').textContent=wifi?.state_code===100?'Connected':'Not connected'; $('wifi-address').textContent=wifi?.ipv4_addresses?.join(', ')||'No Wi-Fi address'; $('free-space').textContent=bytes(state.storage.free); $('recent-files').replaceChildren(); $('recent-files').className=''; if(!files.length) $('recent-files').append(text('p','Your first upload will appear here.','empty')); for(const file of files.slice(-4).reverse()) {const line=text('div','','file-line'); const details=text('div',file.name); details.append(text('small',bytes(file.size)+' · Staged locally')); line.append(details,text('span','↧')); $('recent-files').append(line);} fileRows(); $('network-details').replaceChildren(); for(const [name,data] of Object.entries(state.network.interfaces||{})){ $('network-details').append(text('dt',name==='usb0'?'USB Ethernet':name==='wlan0'?'Wi-Fi':name),text('dd',(data.state_code===100?'Connected':'Unavailable')+' · '+(data.ipv4_addresses?.join(', ')||'No IPv4 address')));} const route=state.network.preferred_ipv4_route; $('network-details').append(text('dt','Preferred IPv4 route'),text('dd',route?route.interface+' via '+route.gateway:'Unknown'),text('dt','Cached Internet state'),text('dd',state.network.cached_global_connectivity||'Unknown')); }catch(error){message(error.message,true);}finally{button.disabled=false;} }
$('refresh').addEventListener('click',refresh); $('file-search').addEventListener('input',fileRows); $('close-preview').addEventListener('click',()=>$('preview-dialog').close());
$('upload-file')?.addEventListener('change',async event=>{const input=event.target,file=input.files[0]; if(!file) return; if(file.size>100*1024*1024){message('Choose a file of 100 MiB or smaller.',true);return;} input.disabled=true; message('Uploading '+file.name+'… Keep this page open.'); const form=new FormData(); form.append('file',file); try {await api('/api/files',{method:'POST',headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:form});message('File received and staged on your Pi. Cloud backup is separate.');await refresh();}catch(error){message(error.message,true);}finally{input.disabled=false;input.value='';}});
let installPrompt; window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;$('install-app').hidden=false;}); $('install-app').addEventListener('click',async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;$('install-app').hidden=true;}});
if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
refresh();
