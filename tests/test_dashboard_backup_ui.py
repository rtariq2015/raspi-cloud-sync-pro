"""Exercise real backup rendering and polling with synthetic sanitized reports."""

import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const elements=new Map(),timers=new Map(),responses=[],calls=[],listeners=new Map();
let nextTimer=1;
function element(id) {
 if(!elements.has(id)) elements.set(id,{children:[],value:'',hidden:false,
  get textContent(){return this.value+this.children.map(c=>c.textContent).join('');},
  set textContent(value){this.value=String(value);this.children=[];},
  append(...children){this.children.push(...children);},
  replaceChildren(...children){this.value='';this.children=[...children];}});
 return elements.get(id);
}
const document={visibilityState:'visible',createElement:tag=>element('created-'+elements.size),
 addEventListener:(event,callback)=>listeners.set(event,callback)};
const context=vm.createContext({document,$:element,AbortController,
 text:(tag,value,className)=>{const el=document.createElement(tag);el.textContent=value;el.className=className||'';return el;},
 bytes:value=>value+' B',workerReport:report=>{element('worker-output').textContent=report?.state||'unknown';},
 setTimeout:(callback,delay)=>{assert.ok([10000,15000].includes(delay));const id=nextTimer++;timers.set(id,{callback,delay});return id;},
 clearTimeout:id=>timers.delete(id),
 api:async(path,options)=>{assert.equal(path,'/api/backups');calls.push(path);assert.ok(responses.length,'unexpected request');const result=responses.shift();if(result instanceof Error)throw result;return Promise.race([result,new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('synthetic timeout')),{once:true}))]);}
});
vm.runInContext('let backupRefreshTimer=null,backupRefreshFlight=null;\n'+input.functions,context);
function report(overrides={}) {
 return {state:'current',observed_at:1700000000,age_seconds:4,
  service:{state:'running'},history:{state:'available',total_jobs:260,
   counts:{queued:4,retry:3,uploading:1,uploaded:1,verifying:1,verified:248,failed:2}},
  google_drive:{state:'historically_verified',verified_jobs:248,last_verified_at:1699999999,current_access:'not_tested'},
  destinations:[{label:'Google Drive'}],worker:{state:'observed'},byte_progress_available:false,
  jobs:[{id:260,label:'Backup #260',destination:'Google Drive',state:'retry',attempts:2,size:1024,
   updated_at:1700000000,next_attempt:1700000300,error_code:'rclone_timeout',error_message:'The cloud operation timed out.'}],
  ...overrides};
}
async function tick(){assert.equal(timers.size,1);const[id,task]=timers.entries().next().value;timers.delete(id);await task.callback();await flush();}
async function flush(){for(let n=0;n<8;n++)await Promise.resolve();}
(async()=>{await eval('(async()=>{'+input.scenario+'})()');})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
"""


@unittest.skipUnless(NODE, "Node is optional for dashboard backup UI checks")
class DashboardBackupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "src/raspi_cloud_sync_pro/static/dashboard.js").read_text()
        start = cls.source.index("function backupTimestamp(value)")
        end = cls.source.index("function advancedReport(report)", start)
        cls.functions = cls.source[start:end]

    def scenario(self, code):
        result = subprocess.run([NODE, "-e", HARNESS], input=json.dumps({
            "functions": self.functions, "scenario": code}), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_full_counts_use_aggregate_not_recent_jobs_and_google_is_historical(self):
        self.scenario("""
          context.backupReport(report());
          assert.equal(element('verified-count').textContent,'248');
          assert.equal(element('backup-verified').textContent,'248');
          assert.equal(element('backup-pending').textContent,'10');
          assert.equal(element('backup-retrying').textContent,'3');
          assert.equal(element('backup-failed').textContent,'2');
          assert.equal(element('backup-service').textContent,'Observed: Running');
          assert.match(element('backup-google').textContent,/verified previously/);
          assert.match(element('backup-google-detail').textContent,/Current Google access has not been tested/);
          assert.match(element('backup-updated').textContent,/4 seconds ago/);
          assert.match(element('backup-destinations').textContent,/Google Drive.*private identifiers hidden/);
          assert.equal(element('history-rows').children.length,1);
        """)

    def test_stale_stopped_and_failed_service_observations_are_distinct(self):
        self.scenario(r"""
          context.backupReport(report({state:'stale',age_seconds:90}));
          assert.match(element('backup-service').textContent,/Last observed: Running \(stale\)/);
          assert.match(element('backup-report-state').textContent,/Current service health is unknown/);
          assert.equal(element('backup-verified').textContent,'248');
          context.backupReport(report({service:{state:'stopped'}}));
          assert.equal(element('backup-service').textContent,'Observed: Stopped');
          context.backupReport(report({service:{state:'failed'}}));
          assert.equal(element('backup-service').textContent,'Observed: Failed');
          context.backupReport(report({google_drive:{state:'recorded'}}));
          assert.equal(element('backup-google').textContent,'Google Drive destination recorded');
        """)

    def test_unavailable_invalid_or_disconnected_reports_clear_previous_good_counts(self):
        self.scenario("""
          for(const state of ['unavailable','invalid','not_connected']) {
            context.backupReport(report());context.backupReport({state});
            for(const id of ['verified-count','backup-verified','backup-pending','backup-retrying','backup-failed'])assert.equal(element(id).textContent,'—');
            assert.equal(element('backup-service').textContent,'Unknown');
            assert.equal(element('history-rows').children.length,0);
            assert.equal(element('history-empty').hidden,false);
            assert.match(element('history-empty').textContent,/unavailable/);
            assert.doesNotMatch(element('backup-google').textContent,/verified previously/);
          }
          context.backupReport(report({history:{state:'unavailable'}}));
          assert.equal(element('backup-verified').textContent,'—');
          assert.equal(context.backupTimestamp(null),'Unknown');
          assert.equal(context.backupTimestamp(Infinity),'Unknown');
        """)

    def test_retry_errors_and_stage_progress_have_no_invented_percentage(self):
        self.scenario(r"""
          const r=report();r.jobs.push({...r.jobs[0],id:261,label:'Backup #261',state:'verifying',error_message:null});
          context.backupReport(r);
          const row=element('history-rows').children[0];
          assert.equal(row.children.length,7);
          assert.match(row.textContent,/Backup #260.*Waiting to retry.*timed out.*Next retry:/);
          assert.match(element('backup-progress').textContent,/Backup #261.*Verifying downloaded contents/);
          assert.match(element('backup-progress').textContent,/does not provide reliable live byte counts or percentages/);
          assert.doesNotMatch(element('backup-progress').textContent,/\d+%/);
          r.jobs[0].error_message='<script>synthetic</script>';
          context.backupReport(r);
          assert.equal(element('history-rows').children[0].children[6].textContent.startsWith('<script>synthetic</script>'),true);
          assert.equal(element('history-rows').children[0].children[6].innerHTML,undefined);
        """)

    def test_polling_is_single_flight_and_failed_fetch_replaces_healthy_state(self):
        self.scenario("""
          let complete;responses.push(new Promise(resolve=>{complete=resolve;}));
          const a=context.refreshBackups(),b=context.refreshBackups();
          assert.equal(a,b);assert.equal(calls.length,1);assert.equal(timers.size,1);
          complete(report());await a;assert.equal(timers.size,1);
          responses.push(new Error('synthetic transport failure'));
          await tick();assert.equal(calls.length,2);assert.equal(timers.size,1);
          assert.equal(element('backup-service').textContent,'Unknown');
          assert.equal(element('backup-verified').textContent,'—');
          assert.equal(element('history-rows').children.length,0);
        """)

    def test_hung_fetch_aborts_and_clears_old_running_observation(self):
        self.scenario("""
          context.backupReport(report());responses.push(new Promise(()=>{}));
          const pending=context.refreshBackups();assert.equal(timers.size,1);
          assert.equal(timers.values().next().value.delay,10000);
          await tick();await pending;
          assert.equal(element('backup-service').textContent,'Unknown');
          assert.equal(element('backup-verified').textContent,'—');
          assert.equal(timers.size,1);assert.equal(timers.values().next().value.delay,15000);
        """)

    def test_hidden_page_stops_polling_and_visible_page_resumes_once(self):
        self.scenario("""
          responses.push(report());await context.refreshBackups();assert.equal(timers.size,1);
          document.visibilityState='hidden';listeners.get('visibilitychange')();assert.equal(timers.size,0);
          context.scheduleBackupRefresh();assert.equal(timers.size,0);assert.equal(calls.length,1);
          responses.push(report({service:{state:'stopped'}}));document.visibilityState='visible';
          listeners.get('visibilitychange')();await flush();
          assert.equal(calls.length,2);assert.equal(timers.size,1);
          assert.equal(element('backup-service').textContent,'Observed: Stopped');
        """)

    def test_frontend_reads_sanitized_endpoint_and_does_not_fetch_raw_history(self):
        self.assertIn("api('/api/backups',", self.source)
        self.assertNotIn("api('/api/history')", self.source)
        self.assertNotIn("job.relative", self.source)


class BackupMarkupTests(unittest.TestCase):
    def test_backup_panel_uses_existing_design_and_mobile_layout(self):
        source = (ROOT / "src/raspi_cloud_sync_pro/templates/dashboard.html").read_text()
        css = (ROOT / "src/raspi_cloud_sync_pro/static/dashboard.css").read_text()

        class Ids(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []

            def handle_starttag(self, tag, attrs):
                self.ids.extend(value for key, value in attrs if key == "id")

        parser = Ids()
        parser.feed(source)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for identity in ("backup-report-state", "backup-service", "backup-verified", "backup-pending",
                         "backup-retrying", "backup-failed", "backup-progress", "backup-recent"):
            self.assertIn(identity, parser.ids)
        self.assertIn('aria-live="polite"', source)
        self.assertIn("Separate web-upload account setup", source)
        self.assertIn("A new Test pauses permission", source)
        self.assertIn("@media(max-width:780px){.backup-facts{grid-template-columns:1fr}", css)


if __name__ == "__main__":
    unittest.main()
