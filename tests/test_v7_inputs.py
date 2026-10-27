"""v7: UI->API workflow conversion, Fantastic H3 Media Loader inputs, 9:16 grid, largest video preview."""
import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_monitor as monitor
import workflow_convert as wc

INFO = {
 'KSampler': {'input': {'required': {'model': ['MODEL'], 'seed': ['INT', {'control_after_generate': True}],
                                     'steps': ['INT', {}], 'sampler_name': [['euler', 'er_sde']]}},
              'input_order': {'required': ['model', 'seed', 'steps', 'sampler_name']}, 'display_name': 'KSampler'},
 'LoadImage': {'input': {'required': {'image': [['a.png'], {'image_upload': True}]}},
               'input_order': {'required': ['image']}, 'display_name': 'Load Image'},
 'ModelLoader': {'input': {'required': {'name': [['m.safetensors']]}}, 'display_name': 'Model Loader'},
 'Blur': {'input': {'required': {'model': ['MODEL']}}, 'display_name': 'Blur'},
 'SaveVideo': {'input': {'required': {'filename_prefix': ['STRING', {}], 'format': ['COMFY_DYNAMICCOMBO_V3', {
     'options': [{'key': 'auto', 'inputs': {'required': {'codec': ['COMFY_DYNAMICCOMBO_V3', {
         'options': [{'key': 'h264', 'inputs': {'required': {}}}]}]}}}]}]}},
     'input_order': {'required': ['filename_prefix', 'format']}},
 'Anything Everywhere': {'input': {'optional': {}}},
 'MiniMaxH3MediaLoader': {'input': {'required': {'media_state': ['STRING', {}]}}},
}


def node(nid, ntype, inputs=(), outputs=(), widgets=None, mode=0, title=None):
    return {'id': nid, 'type': ntype, 'mode': mode, 'inputs': list(inputs), 'outputs': list(outputs),
            'widgets_values': widgets, 'title': title, 'properties': {}}


class ConverterTests(unittest.TestCase):
    def workflow(self):
        sub = {'id': 'sub-1', 'inputs': [{'name': 'model', 'type': 'MODEL'}, {'name': 'steps', 'type': 'INT'}],
               'outputs': [{'name': 'MODEL', 'type': 'MODEL'}],
               'nodes': [node(1, 'KSampler', [{'name': 'model', 'type': 'MODEL', 'link': 101},
                                              {'name': 'steps', 'type': 'INT', 'link': 102, 'widget': {'name': 'steps'}}],
                              widgets=[7, 'randomize', 3, 'er_sde'])],
               'links': [{'id': 101, 'origin_id': -10, 'origin_slot': 0, 'target_id': 1, 'target_slot': 0, 'type': 'MODEL'},
                         {'id': 102, 'origin_id': -10, 'origin_slot': 1, 'target_id': 1, 'target_slot': 1, 'type': 'INT'},
                         {'id': 103, 'origin_id': 2, 'origin_slot': 0, 'target_id': -20, 'target_slot': 0, 'type': 'MODEL'}]}
        sub['nodes'].append(node(2, 'Blur', [{'name': 'model', 'type': 'MODEL', 'link': None}],
                                 [{'name': 'MODEL', 'type': 'MODEL', 'links': [103]}]))
        nodes = [
            node(10, 'ModelLoader', outputs=[{'name': 'MODEL', 'type': 'MODEL', 'links': [1, 5]}], widgets=['m.safetensors']),
            node(11, 'Reroute', [{'name': '', 'type': '*', 'link': 1}], [{'name': '', 'type': 'MODEL', 'links': [2]}]),
            node(12, 'Blur', [{'name': 'model', 'type': 'MODEL', 'link': 2}],
                 [{'name': 'MODEL', 'type': 'MODEL', 'links': [3]}], mode=4),          # bypassed
            node(13, 'KSampler', [{'name': 'model', 'type': 'MODEL', 'link': 3},
                                  {'name': 'seed', 'type': 'INT', 'link': 4, 'widget': {'name': 'seed'}}],
                 widgets=[1, 'fixed', 20, 'euler'], title='Main sampler'),
            node(14, 'PrimitiveNode', outputs=[{'name': 'INT', 'type': 'INT', 'links': [4]}], widgets=[99, 'fixed']),
            node(15, 'LoadImage', widgets=['photo.png', 'image']),
            node(16, 'Blur', mode=2),                                                        # muted
            node(17, 'Anything Everywhere', [{'name': 'anything', 'type': 'MODEL', 'link': 5}]),
            node(18, 'sub-1', [{'name': 'model', 'type': 'MODEL', 'link': None},
                               {'name': 'steps', 'type': 'INT', 'link': None, 'widget': {'name': 'steps'}}],
                 widgets=[42]),
            node(19, 'SaveVideo', widgets=['clip_%date:yyyy%', 'auto', 'h264']),
            node(20, 'Note'),
        ]
        links = [[1, 10, 0, 11, 0, 'MODEL'], [2, 11, 0, 12, 0, 'MODEL'], [3, 12, 0, 13, 0, 'MODEL'],
                 [4, 14, 0, 13, 1, 'INT'], [5, 10, 0, 17, 0, 'MODEL']]
        return {'nodes': nodes, 'links': links, 'definitions': {'subgraphs': [sub]}}

    def test_converts_like_frontend(self):
        prompt, warnings = wc.convert(self.workflow(), INFO)
        self.assertEqual(warnings, [])
        self.assertEqual(set(prompt), {'10', '13', '15', '17', '18:1', '18:2', '19'})
        main = prompt['13']['inputs']
        # Reroute and the bypassed node collapse to the loader; Primitive value fills the linked widget.
        self.assertEqual(main, {'model': ['10', 0], 'seed': 99, 'steps': 20, 'sampler_name': 'euler'})
        self.assertEqual(prompt['13']['_meta']['title'], 'Main sampler')
        self.assertEqual(prompt['15']['inputs'], {'image': 'photo.png'})  # upload helper value dropped
        # Subgraph: inner ids are "instance:inner"; promoted widget value and UE broadcast reach inside.
        self.assertEqual(prompt['18:1']['inputs']['steps'], 42)
        self.assertEqual(prompt['18:1']['inputs']['seed'], 7)
        self.assertEqual(prompt['18:1']['inputs']['model'], ['10', 0])
        # UE works per graph: a root UE node does not reach an inner node's own unconnected input.
        self.assertEqual(prompt['18:2']['inputs'], {})
        self.assertEqual(prompt['19']['inputs'], {'filename_prefix': 'clip_%date:yyyy%', 'format': 'auto',
                                                  'format.codec': 'h264'})

    def test_missing_node_warns(self):
        wf = {'nodes': [node(1, 'Blur', [{'name': 'model', 'type': 'MODEL', 'link': 1}]),
                        node(2, 'NotInstalled', outputs=[{'name': 'MODEL', 'type': 'MODEL', 'links': [1]}])],
              'links': [[1, 2, 0, 1, 0, 'MODEL']]}
        prompt, warnings = wc.convert(wf, INFO)
        self.assertEqual(prompt['1']['inputs'], {})
        self.assertTrue(any('NotInstalled' in w for w in warnings))

    def test_text_replacements(self):
        prompt = {'1': {'class_type': 'SaveImage', '_meta': {'title': 'Save'},
                        'inputs': {'filename_prefix': 'run_%date:yyyyMMdd_hhmmss%_%Sampler.steps%'}},
                  '2': {'class_type': 'KSampler', '_meta': {'title': 'Sampler'}, 'inputs': {'steps': 30}}}
        wc.apply_text_replacements(prompt, dt.datetime(2026, 10, 26, 7, 5, 9))
        self.assertEqual(prompt['1']['inputs']['filename_prefix'], 'run_20261026_070509_30')

    def test_detects_formats(self):
        self.assertTrue(wc.is_ui_workflow(self.workflow()))
        self.assertFalse(wc.is_ui_workflow({'1': {'class_type': 'X', 'inputs': {}}}))


class MediaLoaderTests(unittest.TestCase):
    ORIGINAL = json.dumps([{'kind': 'picture', 'file': 'minimax_h3/a.png [input]', 'name': 'a.png'}])

    def prompt(self):
        return {'4': {'class_type': 'MiniMaxH3MediaLoader', 'inputs': {'media_state': self.ORIGINAL},
                      '_meta': {'title': 'Fantastic H3 Media Loader'}}}

    def test_editor_and_validation(self):
        nodes = monitor.node_editors(self.prompt())
        self.assertEqual(nodes[0]['fields'][0]['kind'], 'media')
        reordered = json.dumps([{'kind': 'picture', 'file': 'minimax_h3/a.png [input]', 'enabled': False}])
        out = monitor.apply_overrides(self.prompt(), [{'node': '4', 'key': 'media_state', 'value': reordered}])
        self.assertFalse(json.loads(out['4']['inputs']['media_state'])[0]['enabled'])
        unknown = json.dumps([{'kind': 'video', 'file': 'minimax_h3/never_uploaded.mp4 [input]'}])
        with self.assertRaises(ValueError):
            monitor.apply_overrides(self.prompt(), [{'node': '4', 'key': 'media_state', 'value': unknown}])
        monitor.STAGED_MEDIA.add('minimax_h3/new.mp4 [input]')
        added = json.dumps([{'kind': 'video', 'file': 'minimax_h3/new.mp4 [input]'}])
        monitor.apply_overrides(self.prompt(), [{'node': '4', 'key': 'media_state', 'value': added}])
        too_many = json.dumps([{'kind': 'video', 'file': 'minimax_h3/new.mp4 [input]'}] * 4)
        with self.assertRaises(ValueError):
            monitor.apply_overrides(self.prompt(), [{'node': '4', 'key': 'media_state', 'value': too_many}])

    def test_input_reference(self):
        self.assertEqual(monitor.input_reference('minimax_h3/a b.png [input]'),
                         {'filename': 'a b.png', 'subfolder': 'minimax_h3', 'type': 'input'})
        self.assertEqual(monitor.input_reference('photo.jpg')['subfolder'], '')
        for bad in ('../secret.png', 'a/../../b.png', 'x.png [output]', 'notes.txt', 'C:\\x.png', ''):
            with self.assertRaises(ValueError):
                monitor.input_reference(bad)


@unittest.skipUnless(os.environ.get('CHROMIUM_PATH') or Path('/usr/bin/chromium').exists(), 'needs a Chromium')
class V7UITests(unittest.TestCase):
    def test_media_editor_grid_and_largest_video(self):
        from playwright.sync_api import sync_playwright
        base = Path(monitor.__file__).parent
        html = monitor.full_html()
        for name in ('schedule', 'outputs', 'recent'):
            html = html.replace(f'<script src="/{name}.js?v=7"></script>',
                                '<script>' + (base / f'{name}.js').read_text(encoding='utf-8') + '</script>')
        state = {'queue': {'running': [], 'pending': []}, 'history': [{'id': 'h-1', 'success': True, 'files': [
            {'filename': 'small.mp4', 'node': '7', 'kind': 'gifs', 'index': 0, 'size': 1000},
            {'filename': 'big-audio.mp4', 'node': '7', 'kind': 'gifs', 'index': 1, 'size': 90000}]}]}
        media = json.dumps([{'kind': 'picture', 'file': 'minimax_h3/a.png [input]', 'name': 'a.png'},
                            {'kind': 'video', 'file': 'minimax_h3/b.mp4 [input]', 'name': 'b.mp4', 'has_audio': True}])
        fixtures = {'/api/state': state, '/api/outputs': {'roots': [{'index': 0, 'label': 'out'}], 'count': 1,
                    'next_offset': None, 'items': [{'kind': 'file', 'media': 'image', 'name': 'p.png', 'path': 'p.png',
                                                    'size': 5, 'modified': 1792900000}]},
                    '/api/workflows': [{'id': 'disk:0:ui.json', 'title': 'ui.json', 'format': 'ui', 'group': 'ui'}],
                    '/api/schedules': [], '/api/models/loras': [], '/api/assets': [],
                    '/api/workflow': {'title': 'ui.json', 'conversion': {'path': 'workflows/converted/ui.json', 'nodes': 3,
                                      'warnings': ['Node type "X" is not installed']},
                                      'nodes': [{'id': '4', 'title': 'Loader', 'class_type': 'MiniMaxH3MediaLoader',
                                                 'fields': [{'key': 'media_state', 'kind': 'media', 'value': media}]},
                                                {'id': '9', 'title': 'Headshot', 'class_type': 'LoadImage',
                                                 'fields': [{'key': 'image', 'kind': 'image', 'value': 'x.png'}]}]},
                    '/api/run': {'prompt_id': 'p-9'}}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                                                 headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('about:blank')
            page.evaluate('''fixtures=>{window.posted=[];window.fetch=async(url,opts={})=>{
                const key=String(url).split('?')[0];if(opts.body)window.posted.push([key,JSON.parse(opts.body)]);
                const data=fixtures[key];return new Response(JSON.stringify(data===undefined?{error:'missing '+key}:data),
                {status:data===undefined?404:200,headers:{'Content-Type':'application/json'}});}}''', fixtures)
            page.set_content(html, wait_until='load')
            page.wait_for_selector('#output-grid .output-card')
            box = page.locator('#output-grid .output-media').first.bounding_box()
            self.assertAlmostEqual(box['height'] / box['width'], 16 / 9, delta=0.05)
            src = page.locator('#recent-grid video').first.get_attribute('data-src')
            self.assertIn('index=1', src)  # the larger of the two videos
            page.locator('#nav-plan').click()
            page.wait_for_selector('#workflow-select option[value="disk:0:ui.json"]', state='attached')
            page.select_option('#workflow-select', 'disk:0:ui.json')
            page.wait_for_selector('.media-item')
            self.assertEqual(page.locator('.media-item').count(), 2)
            self.assertIn('converted/ui.json', page.locator('#conversion-status').inner_text())
            self.assertTrue(page.locator('#convert-workflow').is_visible())
            self.assertIn('/api/input-preview', page.locator('.input-thumb').get_attribute('src'))
            self.assertIn('1/9 pictures', page.locator('.media-editor').inner_text())
            page.locator('.media-item').first.get_by_role('button', name='Switch off (kept in the list)').click()
            page.locator('.media-item').nth(1).get_by_role('button', name='Move up').click()
            page.locator('#run-now').click()
            page.wait_for_timeout(200)
            run = [body for key, body in page.evaluate('window.posted') if key == '/api/run'][0]
            items = json.loads(run['changes'][0]['value'])
            self.assertEqual([i['name'] for i in items], ['b.mp4', 'a.png'])
            self.assertFalse(items[1]['enabled'])
            page.locator('.media-editor').get_by_role('button', name='＋ Add media').click()
            self.assertIn('video/*', page.locator('#phone-image').get_attribute('accept'))
            self.assertEqual(errors, [])
            browser.close()


if __name__ == '__main__':
    unittest.main()
