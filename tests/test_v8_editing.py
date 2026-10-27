"""v8: Use Everywhere links in bridge snapshots, LoRA sliders, output settings, total progress."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_monitor as monitor
from live_monitor import LiveMonitor

INFO = {
    'VAELoader': {'input': {'required': {'vae_name': [['v.safetensors']]}}, 'output': ['VAE']},
    'VAEDecode': {'input': {'required': {'samples': ['LATENT'], 'vae': ['VAE']}}, 'output': ['IMAGE']},
    'EmptyLatent': {'input': {'required': {}}, 'output': ['LATENT']},
    'Anything Everywhere': {'input': {'optional': {}}},
}


def node(nid, ntype, inputs=(), outputs=(), widgets=None):
    return {'id': nid, 'type': ntype, 'mode': 0, 'inputs': list(inputs), 'outputs': list(outputs),
            'widgets_values': widgets, 'properties': {}}


class BridgeLinkTests(unittest.TestCase):
    def test_use_everywhere_links_added_to_snapshot(self):
        workflow = {'nodes': [
            node(1, 'VAELoader', outputs=[{'name': 'VAE', 'type': 'VAE', 'links': [1]}], widgets=['v.safetensors']),
            node(2, 'Anything Everywhere', [{'name': 'anything', 'type': 'VAE', 'link': 1}]),
            node(3, 'EmptyLatent', outputs=[{'name': 'LATENT', 'type': 'LATENT', 'links': [2]}]),
            node(4, 'VAEDecode', [{'name': 'samples', 'type': 'LATENT', 'link': 2},
                                  {'name': 'vae', 'type': 'VAE', 'link': None}])],
            'links': [[1, 1, 0, 2, 0, 'VAE'], [2, 3, 0, 4, 0, 'LATENT']]}
        # What graphToPrompt() gives: the broadcast VAE is missing.
        prompt = {'1': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'v.safetensors'}},
                  '3': {'class_type': 'EmptyLatent', 'inputs': {}},
                  '4': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0]}}}
        monitor.OBJECT_INFO.update(data=INFO, at=monitor.time.time())
        self.assertEqual(monitor.fill_frontend_links(prompt, workflow), 1)
        self.assertEqual(prompt['4']['inputs']['vae'], ['1', 0])

    def test_skipped_outputs_are_described(self):
        prompt = {'9': {'class_type': 'SaveImage', 'inputs': {}, '_meta': {'title': 'Save'}}}
        result = {'prompt_id': 'x', 'node_errors': {'9': {'class_type': 'SaveImage', 'errors': [
            {'message': 'Required input is missing', 'details': 'images'}]}}}
        self.assertEqual(monitor.node_error_lines(result, prompt),
                         ['Save (node 9): Required input is missing: images'])
        self.assertEqual(monitor.node_error_lines({'node_errors': {}}, prompt), [])


class EditorTests(unittest.TestCase):
    def prompt(self):
        state = {'version': 1, 'sep': ', ', 'cacheMode': 'last', 'loras': [
            {'name': 'a.safetensors', 'on': True, 'sm': 1, 'sc': 1, 'triggers': []},
            {'name': 'b.safetensors', 'on': False, 'sm': 0.7, 'sc': 0.4, 'triggers': ['x']}]}
        return {
            '1': {'class_type': 'PixaromaLoraLoader', 'inputs': {'model': ['5', 0], 'LoraLoaderState': json.dumps(state)}},
            '2': {'class_type': 'Power Lora Loader (rgthree)', 'inputs': {
                'lora_1': {'on': True, 'lora': 'c.safetensors', 'strength': 1}, 'model': ['5', 0]}},
            '3': {'class_type': 'ResolutionSelector', 'inputs': {'megapixels': 0.7, 'multiple': 32}},
            '4': {'class_type': 'PrimitiveFloat', 'inputs': {'value': 18}, '_meta': {'title': 'Video Length (seconds)'}},
            '6': {'class_type': 'PrimitiveInt', 'inputs': {'value': 7}, '_meta': {'title': 'Seed'}},
        }

    def test_fields(self):
        kinds = {(n['id'], f['key']): f['kind'] for n in monitor.node_editors(self.prompt()) for f in n['fields']}
        self.assertEqual(kinds, {('1', 'LoraLoaderState'): 'lora_stack', ('2', 'lora_1'): 'lora_row',
                                 ('3', 'megapixels'): 'setting', ('4', 'value'): 'setting'})

    def test_overrides_keep_other_state(self):
        rows = [{'name': 'a.safetensors', 'on': False, 'sm': -0.2},
                {'name': 'b.safetensors', 'on': True, 'sm': 0.5, 'sc': 0.3}]
        out = monitor.apply_overrides(self.prompt(), [
            {'node': '1', 'key': 'LoraLoaderState', 'value': json.dumps({'loras': rows})},
            {'node': '2', 'key': 'lora_1', 'value': {'on': False, 'lora': 'c.safetensors', 'strength': 0.25}},
            {'node': '3', 'key': 'megapixels', 'value': 0.9},
            {'node': '4', 'key': 'value', 'value': 12.5}])
        state = json.loads(out['1']['inputs']['LoraLoaderState'])
        self.assertEqual(state['cacheMode'], 'last')
        self.assertEqual([(e['on'], e['sm'], e['sc']) for e in state['loras']], [(False, -0.2, -0.2), (True, 0.5, 0.3)])
        self.assertEqual(state['loras'][1]['triggers'], ['x'])
        self.assertEqual(out['2']['inputs']['lora_1'], {'on': False, 'lora': 'c.safetensors', 'strength': 0.25})
        self.assertEqual(out['3']['inputs']['megapixels'], 0.9)
        self.assertEqual(out['4']['inputs']['value'], 12.5)

    def test_bad_edits_rejected(self):
        for change in ({'node': '1', 'key': 'LoraLoaderState', 'value': json.dumps({'loras': []})},
                       {'node': '2', 'key': 'lora_1', 'value': {'on': True, 'lora': 'other.safetensors', 'strength': 1}},
                       {'node': '3', 'key': 'megapixels', 'value': -1},
                       {'node': '3', 'key': 'multiple', 'value': 64}):
            with self.assertRaises(ValueError):
                monitor.apply_overrides(self.prompt(), [change])


class ProgressTests(unittest.TestCase):
    def test_total_percent(self):
        prompt = {'1': {'inputs': {}}, '2': {'inputs': {'x': ['1', 0]}}, '3': {'inputs': {'x': ['2', 0]}},
                  '4': {'inputs': {'x': ['2', 0]}}, '9': {'inputs': {}}}
        expected = monitor.execution_nodes(prompt, ['3', '4'])
        self.assertEqual(expected, {'1', '2', '3', '4'})   # '9' is not needed by any output
        live = LiveMonitor()
        send = lambda kind, **data: live.handle_text(json.dumps({'type': kind, 'data': {'prompt_id': 'p', **data}}))
        send('execution_start')
        send('execution_cached', nodes=['1'])
        send('executing', node='2')
        send('progress', value=3, max=4, node='2')
        snap = live.snapshot('p', prompt, expected)
        self.assertEqual(snap['step_percent'], 75.0)
        self.assertEqual(snap['total_percent'], round((1 + .75) / 4 * 100, 1))
        send('executing', node='3')
        self.assertEqual(live.snapshot('p', prompt, expected)['total_percent'], 50.0)
        self.assertNotIn('done', snap)


if __name__ == '__main__':
    unittest.main()
