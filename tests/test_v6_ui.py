"""Full HTTP browser verification for tabs, home composition, collapsed recent history, and downloads."""
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_monitor as monitor
from playwright.sync_api import sync_playwright

SAMPLE={
 'queue':{'running':[{'id':'r-1','title':'Video example','total_nodes':7,'preview_input_node':None,
    'live':{'node':'5','node_title':'Sampler','step':14,'total_steps':20,'step_percent':70,
            'elapsed_seconds':105,'eta_seconds':45,'has_preview':False}}],
    'pending':[{'id':'p-1','title':'Pending example','total_nodes':8,'preview_input_node':None}]},
 'history':[{'id':'h-1','success':True,'elapsed_seconds':139,
             'files':[{'filename':'sample.mp4','node':'7','kind':'gifs','index':0}]},
            {'id':'h-2','success':True,'elapsed_seconds':None,'files':[]}]}
OUTPUTS={
 'roots':[{'index':0,'label':'ComfyUI output'}],'root':0,'path':'','count':2,'next_offset':None,
 'items':[{'kind':'folder','name':'generated','path':'generated'},
          {'kind':'file','media':'video','name':'sample.mp4','path':'sample.mp4','size':650000,
            'modified':1792900000}]}

class UITests(unittest.TestCase):
 def test_navigation_and_compact_jobs_mobile(self):
  with sync_playwright() as playwright:
   browser=playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
   page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   base=Path(monitor.__file__).parent
   html=monitor.full_html().replace('<script src="/schedule.js?v=7"></script>', '<script>'+ (base/'schedule.js').read_text(encoding='utf-8')+'</script>')
   html=html.replace('<script src="/outputs.js?v=7"></script>', '<script>'+ (base/'outputs.js').read_text(encoding='utf-8')+'</script>')
   html=html.replace('<script src="/recent.js?v=7"></script>', '<script>'+ (base/'recent.js').read_text(encoding='utf-8')+'</script>')
   page.goto('about:blank')
   page.evaluate('''fixtures=>{window.fakeCalls=[];window.fetch=async(url,opts={})=>{
       const key=String(url).split('?')[0];window.fakeCalls.push(key);
       const data=fixtures[key];return new Response(JSON.stringify(data===undefined?{error:'missing '+key}:data),
       {status:data===undefined?404:200,headers:{'Content-Type':'application/json'}});
    }}''',{'/api/state':SAMPLE,'/api/outputs':OUTPUTS,
    '/api/workflows':[{'id':'disk:0:sample.json','title':'Example workflow'}],
    '/api/schedules':[],'/api/models/loras':[],
    '/api/workflow':{'title':'Example','nodes':[{'id':'5','title':'CLIPTextEncode','class_type':'CLIPTextEncode','fields':[{'key':'text','kind':'prompt','value':'testing'}]}]}})
   page.set_content(html,wait_until='load')
   page.wait_for_selector('#output-grid .output-card')
   self.assertTrue(page.locator('#home-tab').is_visible())
   self.assertTrue(page.locator('#current-job').get_by_text('Step 14 / 20').is_visible())
   self.assertTrue(page.get_by_text('Output browser',exact=False).first.is_visible())
   self.assertEqual(page.locator('#output-grid .output-card').count(),2)
   self.assertFalse(page.locator('#queue-tab').is_visible())
   page.locator('#view-queue').click()
   self.assertTrue(page.locator('#queue-tab').is_visible())
   self.assertFalse(page.locator('#home-tab').is_visible())
   self.assertEqual(page.locator('#queue .queue-card').count(),2)
   self.assertEqual(page.locator('.history-item').count(),2)
   self.assertEqual(page.locator('.history-item[open]').count(),0)
   self.assertTrue(page.locator('#gallery').get_by_text('2m 19s').is_visible())
   self.assertFalse(page.get_by_text('Download MP4').is_visible())
   page.locator('.history-item').first.locator('summary').first.click()
   self.assertEqual(page.locator('.history-item[open]').count(),1)
   page.wait_for_timeout(200)
   self.assertTrue(page.get_by_text('Download MP4').is_visible())
   self.assertFalse(page.get_by_text('Download job ZIP').is_visible())
   page.get_by_text('Files · tap to download individually').click()
   self.assertTrue(page.get_by_text('Download job ZIP').is_visible())
   page.locator('#nav-plan').click()
   self.assertTrue(page.locator('#plan-tab').is_visible())
   page.wait_for_selector('select#workflow-select option[value="disk:0:sample.json"]',state='attached')
   page.locator('#workflow-select').select_option('disk:0:sample.json')
   page.wait_for_selector('.node-edit')
   self.assertTrue(page.locator('.node-edit').first.is_visible())
   page.locator('#nav-outputs').click()
   self.assertTrue(page.locator('#outputs-page').is_visible())
   self.assertTrue(page.locator('#output-browser-panel').is_visible())
   self.assertEqual(page.locator('#outputs-browser-host #output-grid .output-card').count(),2)
   page.locator('#nav-home').click()
   self.assertTrue(page.locator('#home-tab').is_visible())
   self.assertEqual(page.locator('#home-browser-host #output-grid .output-card').count(),2)
   page.screenshot(path=str(Path(monitor.__file__).parent/'mobile_preview_v6.png'),full_page=True)
   self.assertTrue(page.locator('#output-grid video').first.evaluate('v=>v.muted&&v.loop&&v.autoplay'))
   page.locator('#output-view-list').click()
   self.assertEqual(page.locator('#output-grid.list-view').count(),1)
   self.assertEqual(page.locator('#output-grid .output-card').count(),2)
   self.assertTrue(page.locator('#output-grid .dl-small').first.is_visible())
   page.locator('#output-view-grid').click()
   self.assertEqual(page.locator('#output-grid.list-view').count(),0)
   self.assertFalse(errors,errors)
   # The navigation is self-contained: even if optional external modules fail, tabs still work.
   page.evaluate("window.switchComfyTab('queue')")
   self.assertTrue(page.locator('#queue-tab').is_visible())
   # Simulate optional modules failing to load: inline navigation remains functional.
   stripped=monitor.full_html().replace('<script src="/schedule.js?v=7"></script>','').replace('<script src="/outputs.js?v=7"></script>','').replace('<script src="/recent.js?v=7"></script>','')
   page.set_content(stripped,wait_until='load')
   page.locator('#nav-plan').click()
   self.assertTrue(page.locator('#plan-tab').is_visible())
   page.locator('#nav-outputs').click()
   self.assertTrue(page.locator('#outputs-page').is_visible())
   browser.close()
 def test_history_duration_from_comfy_timestamps(self):
  record={'status':{'messages':[
   ['execution_start',{'timestamp':1790000000000}],
   ['execution_success',{'timestamp':1790000140000}]]}}
  self.assertEqual(monitor.history_elapsed(record),140)
  self.assertIsNone(monitor.history_elapsed({'status':{'messages':[]}}))
  self.assertIsNone(monitor.history_elapsed({'status':{'messages':[
   ['execution_success',{'timestamp':1790000140000}]]}}))

if __name__ == '__main__':unittest.main()
