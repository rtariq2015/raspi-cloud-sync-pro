"""Optional Node VM checks run actual control rendering, polling and form code."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
let clock=0,nextTimer=1,submit=null;
const elements=new Map(),timers=new Map(),responses=[],messages=[],fetches=[],calls=[];
function element(id) {if(!elements.has(id))elements.set(id,{textContent:'',disabled:false,dataset:{}});return elements.get(id);}
const context=vm.createContext({
  $:element,
  document:{querySelector:selector=>selector.includes('csrf-token')?{content:'synthetic-csrf'}:element('connect'),
    getElementById:id=>id==='google-control-form'?{addEventListener:(event,callback)=>{assert.equal(event,'submit');submit=callback;}}:element(id)},
  performance:{now:()=>clock},
  setTimeout:(callback,delay)=>{assert.equal(delay,3000);const id=nextTimer++;timers.set(id,{callback,at:clock+delay});return id;},
  clearTimeout:id=>timers.delete(id),
  api:async path=>{assert.equal(path,'/api/destinations');calls.push(path);assert.ok(responses.length,'unexpected polling');const value=responses.shift();if(value instanceof Error)throw value;return value;},
  fetch:async(path,options)=>{fetches.push({path,options});return {ok:true,status:202,json:async()=>({control:{control_state:'test_requested'}})};},
  message:(value,error=false)=>messages.push({value,error}),
  location:{href:''},
});
vm.runInContext('let googlePreparationTimer=null,googlePreparationRetryUntil=0,googleControlTimer=null,googleControlRetryUntil=0;\n'+input.functions+'\n'+input.handler,context);
function data(state,{available=true,age=0,prepared=true,admitted=false}={}) {
 return {providers:[{provider:'google_drive',client_ready:true,authorization:'authorization_saved_untested',
  preparation_requests_available:false,preparation:{preparation_state:prepared?'prepared_untested':'unavailable'},
  control_requests_available:available,control:{control_state:state,requested_action:state.split('_')[0],
   age_seconds:age,historical:['test_verified','enable_recorded','disable_recorded'].includes(state),
   admission_authorized:admitted,current_worker_state_checked:false,account_verified:false}}]};
}
function show(state,options){context.destinationSetup(data(state,options));}
function deadline(){return vm.runInContext('googleControlRetryUntil',context);}
async function tick(){assert.equal(timers.size,1);const[id,task]=timers.entries().next().value;timers.delete(id);clock=task.at;await task.callback();assert.ok(timers.size<=1);}
async function drain(limit=10){let count=0;while(timers.size){assert.ok(count++<limit,'polling did not stop');await tick();}}
(async()=>{await eval('(async()=>{'+input.scenario+'})()');})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
"""


@unittest.skipUnless(NODE, 'Node is optional for dashboard control UI checks')
class DashboardGoogleControlUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'src/raspi_cloud_sync_pro/static/dashboard.js').read_text()
        begin = source.index('function scheduleGoogleControlRefresh()')
        end = source.index("\ndocument.getElementById('google-prepare-form')", begin)
        cls.functions = source[begin:end]
        begin = source.index("document.getElementById('google-control-form')")
        end = source.index("\ndocument.querySelectorAll('.oauth-connect')", begin)
        cls.handler = source[begin:end]

    def scenario(self, code):
        result = subprocess.run([NODE, '-e', HARNESS], input=json.dumps({
            'functions': self.functions, 'handler': self.handler, 'scenario': code}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_pending_transient_unavailable_and_verified_are_distinct_and_polling_stops(self):
        self.scenario("""
          show('test_requested');
          assert.match(element('google-control-status').textContent,/Test queued/);
          assert.doesNotMatch(element('google-control-status').textContent,/verified/i);
          assert.equal(element('google-enable-button').disabled,true);
          assert.equal(element('google-disable-button').disabled,false);
          const until=deadline();responses.push(data('unavailable'),data('test_verified'));
          await tick();assert.equal(deadline(),until);
          await tick();assert.equal(timers.size,0);assert.equal(deadline(),0);
          assert.match(element('google-control-status').textContent,/Test passed — not enabled/);
          assert.equal(element('google-test-button').disabled,true);
          assert.equal(element('google-enable-button').disabled,false);
          assert.match(element('google-control-detail').textContent,/has not checked whether the backup service is running/);
          for(let i=0;i<3;i++)show('test_verified');
          assert.equal(timers.size,0);assert.equal(calls.length,2);
        """)

    def test_permission_is_historical_and_disable_remains_available_without_preparation(self):
        self.scenario("""
          show('enable_recorded',{admitted:true});
          assert.match(element('google-control-status').textContent,/Permission saved — service status unconfirmed/);
          assert.match(element('google-control-detail').textContent,/Starting another Test pauses this permission until you choose Enable again/);
          assert.match(element('google-control-detail').textContent,/which Google account is connected/);
          assert.equal(element('google-disable-button').disabled,false);
          show('not_requested',{prepared:false});
          assert.equal(element('google-test-button').disabled,true);
          assert.equal(element('google-enable-button').disabled,true);
          assert.equal(element('google-disable-button').disabled,false);
          show('disable_requested',{prepared:false});
          assert.match(element('google-control-detail').textContent,/A transfer already in progress may finish/);
          assert.equal(element('google-disable-button').disabled,true);
        """)

    def test_disabled_capability_and_terminal_states_do_not_invent_requests_or_poll_forever(self):
        self.scenario("""
          show('test_requested',{available:false});
          assert.equal(timers.size,0);assert.equal(deadline(),0);
          for(const id of ['google-test-button','google-enable-button','google-disable-button'])assert.equal(element(id).disabled,true);
          assert.match(element('google-control-detail').textContent,/Automatic backup setup is still being completed on this device/);
          show('unavailable');assert.equal(timers.size,0);
          for(const state of ['test_failed','test_expired','enable_failed','disable_recorded','expired','stale_binding','invalid']) {
            show('test_requested');assert.equal(timers.size,1);
            show(state);assert.equal(timers.size,0);assert.equal(deadline(),0);
          }
        """)

    def test_control_polling_unavailable_and_network_failures_keep_original_bounded_deadline(self):
        self.scenario("""
          show('enable_requested',{age:591});
          const until=deadline();assert.equal(until,9000);
          responses.push(data('unavailable'),new Error('temporary failure'),data('unavailable'));
          await drain();assert.equal(clock,until);assert.equal(timers.size,0);
          assert.equal(deadline(),until);assert.equal(calls.length,3);
          context.scheduleGoogleControlRefresh();assert.equal(timers.size,0);
        """)

    def test_actual_form_handler_posts_only_fixed_action_with_csrf_and_no_private_fields(self):
        self.scenario("""
          show('not_requested');
          responses.push(data('test_requested'));
          let prevented=false;
          await submit({preventDefault:()=>{prevented=true;},submitter:{value:'test',disabled:false}});
          assert.equal(prevented,true);assert.equal(fetches.length,1);
          assert.equal(fetches[0].path,'/api/destinations/google_drive/control');
          assert.equal(fetches[0].options.method,'POST');
          assert.equal(fetches[0].options.credentials,'same-origin');
          assert.equal(fetches[0].options.headers['X-CSRF-Token'],'synthetic-csrf');
          assert.deepEqual(JSON.parse(fetches[0].options.body),{action:'test'});
          assert.match(messages[0].value,/Test queued.*Wait for a pass before choosing Enable/);
          await submit({preventDefault:()=>{},submitter:{value:'../enable',disabled:false}});
          await submit({preventDefault:()=>{},submitter:{value:'enable',disabled:true}});
          assert.equal(fetches.length,1);
        """)


if __name__ == '__main__':
    unittest.main()
