"""Owner tools never infer browser tunnels or open administration automatically."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const elements=new Map(),timers=new Map(),responses=[],calls=[],opens=[];
let owner=true,nextTimer=1;
function element(id) {
 if(!elements.has(id))elements.set(id,{textContent:'',checked:false,disabled:true,handlers:{},
  addEventListener(event,callback){this.handlers[event]=callback;}});
 return elements.get(id);
}
const context=vm.createContext({AbortController,
 $:id=>owner?element(id):null,
 document:{getElementById:id=>element(id)},
 window:{open:(...args)=>opens.push(args)},
 setTimeout:(callback,delay)=>{assert.equal(delay,10000);const id=nextTimer++;timers.set(id,callback);return id;},
 clearTimeout:id=>timers.delete(id),
 api:async(path,options)=>{assert.equal(path,'/api/advanced/rclone');calls.push(path);assert.ok(responses.length,'unexpected request');const result=responses.shift();if(result instanceof Error)throw result;return Promise.race([result,new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('timeout')),{once:true}))]);}
});
vm.runInContext('let advancedReady=false,advancedRefreshFlight=null;\n'+input.functions,context);
const ready={state:'ready',open_url:'http://127.0.0.1:5573/',client_tunnel_verified:false};
async function flush(){for(let n=0;n<8;n++)await Promise.resolve();}
(async()=>{await eval('(async()=>{'+input.scenario+'})()');})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
"""


@unittest.skipUnless(NODE, "Node is optional for dashboard advanced UI checks")
class DashboardAdvancedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "src/raspi_cloud_sync_pro/static/dashboard.js").read_text()
        start = source.index("function advancedReport(report)")
        end = source.index("function scheduleGoogleControlRefresh()", start)
        cls.functions = source[start:end]

    def scenario(self, code):
        result = subprocess.run([NODE, "-e", HARNESS], input=json.dumps({
            "functions": self.functions, "scenario": code}), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_tunnel_instructions_bind_both_client_listeners_only_to_loopback(self):
        template = (ROOT / "src/raspi_cloud_sync_pro/templates/dashboard.html").read_text()
        self.assertIn("ssh -N -o ExitOnForwardFailure=yes "
                      "-L 127.0.0.1:5573:127.0.0.1:5573 "
                      "-L 127.0.0.1:5572:127.0.0.1:5572 pi@PI_HOST", template)

    def test_missing_owner_panel_never_requests_administration_status(self):
        self.scenario("""
          owner=false;await context.refreshAdvanced();
          assert.equal(calls.length,0);assert.equal(timers.size,0);assert.equal(opens.length,0);
        """)

    def test_default_disabled_cannot_open_or_claim_tunnels_verified(self):
        self.scenario("""
          context.advancedReport({state:'disabled',open_url:null,client_tunnel_verified:false});
          assert.equal(element('rclone-open').disabled,true);
          assert.equal(element('rclone-tunnels').disabled,true);
          assert.match(element('rclone-admin-state').textContent,/disabled/);
          assert.match(element('rclone-admin-detail').textContent,/does not start rclone/);
          element('rclone-open').handlers.click();assert.equal(opens.length,0);
        """)

    def test_ready_requires_explicit_checkbox_then_opens_only_fixed_gui(self):
        self.scenario("""
          responses.push(ready);await context.refreshAdvanced();assert.equal(calls.length,1);
          assert.equal(element('rclone-open').disabled,true);assert.equal(opens.length,0);
          assert.match(element('rclone-admin-detail').textContent,/has not checked either SSH tunnel/);
          element('rclone-open').handlers.click();assert.equal(opens.length,0);
          element('rclone-tunnels').checked=true;element('rclone-tunnels').handlers.change();
          assert.equal(element('rclone-open').disabled,false);
          element('rclone-open').handlers.click();
          assert.deepEqual(opens,[['http://127.0.0.1:5573/','_blank','noopener,noreferrer']]);
          element('rclone-tunnels').checked=false;element('rclone-tunnels').handlers.change();
          element('rclone-open').handlers.click();assert.equal(opens.length,1);
        """)

    def test_unapproved_origin_path_or_tunnel_claim_is_refused(self):
        self.scenario("""
          for(const open_url of ['https://public.example/','http://127.0.0.1:5572/','http://localhost:5573/','http://127.0.0.1:5573/?token=synthetic']) {
            context.advancedReport({...ready,open_url});
            assert.equal(element('rclone-open').disabled,true);assert.equal(element('rclone-tunnels').disabled,true);
          }
          context.advancedReport({...ready,client_tunnel_verified:true});
          assert.equal(element('rclone-open').disabled,true);assert.equal(opens.length,0);
        """)

    def test_failure_or_hung_refresh_clears_ready_and_checkbox(self):
        self.scenario("""
          context.advancedReport(ready);element('rclone-tunnels').checked=true;
          element('rclone-tunnels').handlers.change();assert.equal(element('rclone-open').disabled,false);
          responses.push(new Error('unavailable'));await context.refreshAdvanced();
          assert.equal(element('rclone-tunnels').checked,false);assert.equal(element('rclone-open').disabled,true);
          context.advancedReport(ready);element('rclone-tunnels').checked=true;
          responses.push(new Promise(()=>{}));const first=context.refreshAdvanced(),second=context.refreshAdvanced();
          assert.equal(first,second);assert.equal(calls.length,2);assert.equal(timers.size,1);
          assert.equal(element('rclone-open').disabled,true);
          timers.values().next().value();await first;
          assert.equal(timers.size,0);assert.equal(element('rclone-open').disabled,true);
          assert.equal(element('rclone-tunnels').checked,false);
          assert.match(element('rclone-admin-state').textContent,/unavailable/);assert.equal(opens.length,0);
        """)


if __name__ == "__main__":
    unittest.main()
