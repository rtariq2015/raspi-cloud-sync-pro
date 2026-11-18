'use strict';
// Callback codes/state never enter this document or browser storage. The
// callback already redirected to this clean URL; Strict session cookies resume
// on these same-origin requests after the provider's cross-site navigation.
(async () => {
  const result=document.getElementById('oauth-result');
  try {
    const context=await fetch('/api/oauth/context',{credentials:'same-origin',cache:'no-store'});
    if(!context.ok) throw new Error('Return to the dashboard and sign in with the same owner session that started this connection, then try Connect again.');
    const data=await context.json();
    const response=await fetch('/api/oauth/complete',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-CSRF-Token':data.csrf}});
    const completion=await response.json();
    if(!response.ok) throw new Error(completion.error==='oauth_authorization_declined'?'Authorization was declined. Your backups remain disabled.':'The connection could not be completed. Return to the dashboard and start Connect again.');
    result.textContent='Authorization saved privately, but not tested. Folder selection, verification and upload enablement are still required.';
  } catch(error) {result.textContent=error.message||'The connection could not be completed.';}
})();
