'use strict';
let files = [];
let googlePreparationTimer = null;
let googlePreparationRetryUntil = 0;
const $ = id => document.getElementById(id);
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if(className) el.className = className; return el; };
const bytes = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : n < 1073741824 ? `${(n/1048576).toFixed(1)} MB` : `${(n/1073741824).toFixed(1)} GB`;
function message(value, error=false) { $('message').textContent=value; $('message').className=error?'notice error':'notice'; $('message').hidden=false; }
async function api(path, options={}) { const response=await fetch(path, {credentials:'same-origin', ...options}); if(response.status===401) {location.href='/login'; throw new Error('Sign in again.');} if(!response.ok) throw new Error(response.status===413?'This upload is too large (100 MiB limit).':response.status===403?'Your account cannot perform this action.':'The request could not be completed. Please try again.'); return response.json(); }
function view(name) { if(!$(name)?.classList.contains('view')) return; document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name); document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===name)); $('view-label').textContent=({overview:'Overview',files:'Files',backups:'Backups',network:'Wi-Fi & access',history:'History'})[name]; }
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
async function refresh() { const button=$('refresh'); button.disabled=true; try {const [state,listing,history,destinations]=await Promise.all([api('/api/status'),api('/api/files'),api('/api/history'),api('/api/destinations')]); destinationSetup(destinations); workerReport(state.worker); files=listing.files; $('file-count').textContent=files.length; $('verified-count').textContent=history.jobs.filter(job=>job.state==='verified').length; const wifi=state.network.interfaces?.wlan0; $('wifi-status').textContent=wifi?.state_code===100?'Connected':'Not connected'; $('wifi-address').textContent=wifi?.ipv4_addresses?.join(', ')||'No Wi-Fi address'; $('free-space').textContent=bytes(state.storage.free); $('recent-files').replaceChildren(); $('recent-files').className=''; if(!files.length) $('recent-files').append(text('p','Your first upload will appear here.','empty')); for(const file of files.slice(-4).reverse()) {const line=text('div','','file-line'); const details=text('div',file.name); details.append(text('small',bytes(file.size)+' · Staged locally')); line.append(details,text('span','↧')); $('recent-files').append(line);} fileRows(); $('network-details').replaceChildren(); for(const [name,data] of Object.entries(state.network.interfaces||{})){ $('network-details').append(text('dt',name==='usb0'?'USB Ethernet':name==='wlan0'?'Wi-Fi':name),text('dd',(data.state_code===100?'Connected':'Unavailable')+' · '+(data.ipv4_addresses?.join(', ')||'No IPv4 address')));} const route=state.network.preferred_ipv4_route; $('network-details').append(text('dt','Preferred IPv4 route'),text('dd',route?route.interface+' via '+route.gateway:'Unknown'),text('dt','Cached Internet state'),text('dd',state.network.cached_global_connectivity||'Unknown')); $('history-rows').replaceChildren(); $('history-empty').hidden=history.jobs.length>0; for(const job of history.jobs){const row=document.createElement('tr'); row.append(text('td',job.relative),text('td',job.destination),text('td',job.state),text('td',job.attempts)); $('history-rows').append(row);} }catch(error){message(error.message,true);}finally{button.disabled=false;} }
$('refresh').addEventListener('click',refresh); $('file-search').addEventListener('input',fileRows); $('close-preview').addEventListener('click',()=>$('preview-dialog').close());
$('upload-file')?.addEventListener('change',async event=>{const input=event.target,file=input.files[0]; if(!file) return; if(file.size>100*1024*1024){message('Choose a file of 100 MiB or smaller.',true);return;} input.disabled=true; message('Uploading '+file.name+'… Keep this page open.'); const form=new FormData(); form.append('file',file); try {await api('/api/files',{method:'POST',headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:form});message('File received and staged on your Pi. Cloud backup is separate.');await refresh();}catch(error){message(error.message,true);}finally{input.disabled=false;input.value='';}});
let installPrompt; window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;$('install-app').hidden=false;}); $('install-app').addEventListener('click',async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;$('install-app').hidden=true;}});
if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
refresh();
