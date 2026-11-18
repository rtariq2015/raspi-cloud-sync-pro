"""Exercise the actual preparation polling functions with Node and a minimal DOM.

No browser, timers, HTTP requests or provider connections run outside this VM.
Node is optional; Python-only environments skip these browser-logic regressions.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
let clock=0, nextTimer=1;
const timers=new Map(), elements=new Map(), responses=[], calls=[], errors=[];
function element(id) {
  if(!elements.has(id)) elements.set(id,{textContent:'',disabled:false,dataset:{}});
  return elements.get(id);
}
const context=vm.createContext({
  $:element,
  document:{querySelector:()=>element('connect')},
  performance:{now:()=>clock},
  setTimeout:(callback,delay)=>{
    assert.equal(delay,3000);
    const id=nextTimer++;
    timers.set(id,{callback,at:clock+delay});
    return id;
  },
  clearTimeout:id=>timers.delete(id),
  api:async path=>{
    assert.equal(path,'/api/destinations');
    calls.push(path);
    assert.ok(responses.length,'unexpected extra polling request');
    const response=responses.shift();
    if(response instanceof Error) throw response;
    return response;
  },
  message:(text,error)=>errors.push({text,error}),
});
vm.runInContext('let googlePreparationTimer=null; let googlePreparationRetryUntil=0;\n'+input.functions,context);
function data(state,{available=true,age=0}={}) {
  return {providers:[{provider:'google_drive',client_ready:true,
    authorization:'authorization_saved_untested',preparation_requests_available:available,
    preparation:{preparation_state:state,age_seconds:age,historical:state==='prepared_untested'}}]};
}
function show(state,options) {context.destinationSetup(data(state,options));}
function deadline() {return vm.runInContext('googlePreparationRetryUntil',context);}
async function tick() {
  assert.equal(timers.size,1,'polling must have exactly one outstanding timer');
  const [id,task]=timers.entries().next().value;
  timers.delete(id);
  clock=task.at;
  await task.callback();
  assert.ok(timers.size<=1,'polling created duplicate timers');
}
async function drain(limit=10) {
  let count=0;
  while(timers.size) {
    assert.ok(count++<limit,'polling did not stop');
    await tick();
  }
}
(async()=>{await eval('(async()=>{'+input.scenario+'})()');})().catch(error=>{
  process.stderr.write(error.stack+'\n');
  process.exitCode=1;
});
"""


@unittest.skipUnless(NODE, "Node is optional for browser polling regression tests")
class DashboardPreparationPollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "src/raspi_cloud_sync_pro/static/dashboard.js").read_text()
        start = source.index("function scheduleGooglePreparationRefresh()")
        stop = source.index("\ndocument.getElementById('google-prepare-form')", start)
        cls.functions = source[start:stop]
        if "function destinationSetup(data)" not in cls.functions:
            raise AssertionError("Actual destination setup function was not extracted")

    def run_scenario(self, scenario):
        result = subprocess.run(
            [NODE, "-e", HARNESS],
            input=json.dumps({"functions": self.functions, "scenario": scenario}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_requested_transient_unavailable_then_prepared_continues_and_stops(self):
        self.run_scenario("""
          show('requested');
          const originalDeadline=deadline();
          responses.push(data('unavailable'),data('prepared_untested'));
          await tick();
          assert.equal(deadline(),originalDeadline,'unavailable must not extend retry lifetime');
          assert.equal(timers.size,1);
          assert.match(element('google-preparation-status').textContent,/unavailable/);
          await tick();
          assert.equal(timers.size,0);
          assert.equal(deadline(),0);
          assert.equal(calls.length,2);
          assert.match(element('google-preparation-status').textContent,/Test required/);
          assert.match(element('google-preparation-detail').textContent,/current worker configuration has not been checked/);
          await drain();
          assert.equal(calls.length,2);
        """)

    def test_repeated_unavailable_reports_stop_at_original_retry_deadline(self):
        self.run_scenario("""
          show('requested',{age:591});
          const originalDeadline=deadline();
          assert.equal(originalDeadline,9000);
          responses.push(data('unavailable'),data('unavailable'),data('unavailable'));
          await drain();
          assert.equal(calls.length,3);
          assert.equal(clock,originalDeadline);
          assert.equal(timers.size,0);
          context.scheduleGooglePreparationRefresh();
          assert.equal(timers.size,0);
        """)

    def test_repeated_network_failures_stop_at_original_retry_deadline(self):
        self.run_scenario("""
          show('requested',{age:591});
          const originalDeadline=deadline();
          responses.push(new Error('temporary failure'),new Error('temporary failure'),new Error('temporary failure'));
          await drain();
          assert.equal(calls.length,3);
          assert.equal(errors.length,3);
          assert.ok(errors.every(error=>error.error===true));
          assert.equal(deadline(),originalDeadline);
          assert.equal(clock,originalDeadline);
          assert.equal(timers.size,0);
        """)

    def test_terminal_invalid_expired_changed_and_not_requested_cancel_polling(self):
        self.run_scenario("""
          for(const state of ['invalid','expired','stale_source','stale_receipt','not_requested']) {
            show('requested');
            assert.equal(timers.size,1);
            show(state);
            assert.equal(timers.size,0,state+' retained a timer');
            assert.equal(deadline(),0,state+' retained a retry deadline');
          }
          assert.equal(calls.length,0);
        """)

    def test_disabled_capability_and_idle_unavailable_never_start_a_loop(self):
        self.run_scenario("""
          show('requested',{available:false});
          assert.equal(timers.size,0);
          assert.equal(deadline(),0);
          assert.equal(element('google-prepare-button').disabled,true);
          assert.equal(element('google-folder-name').disabled,true);
          show('unavailable');
          assert.equal(timers.size,0,'idle unavailable must not invent a request');
          show('requested');
          assert.equal(timers.size,1);
          show('unavailable',{available:false});
          assert.equal(timers.size,0);
          assert.equal(deadline(),0);
          assert.equal(calls.length,0);
        """)

    def test_success_is_historical_and_repeated_updates_do_not_poll_again(self):
        self.run_scenario("""
          show('requested');
          responses.push(data('prepared_untested'));
          await tick();
          for(let i=0;i<3;i++) show('prepared_untested');
          context.scheduleGooglePreparationRefresh();
          assert.equal(timers.size,0);
          assert.equal(deadline(),0);
          assert.equal(calls.length,1);
          assert.match(element('google-preparation-detail').textContent,/cloud test and explicit enablement are still required/);
        """)

    def test_repeated_refresh_requests_keep_only_one_timer_and_one_api_call(self):
        self.run_scenario("""
          show('requested');
          const originalDeadline=deadline();
          show('requested');
          context.scheduleGooglePreparationRefresh();
          context.scheduleGooglePreparationRefresh();
          assert.equal(timers.size,1);
          assert.equal(deadline(),originalDeadline);
          responses.push(data('prepared_untested'));
          await drain();
          assert.equal(calls.length,1);
        """)


if __name__ == "__main__":
    unittest.main()
