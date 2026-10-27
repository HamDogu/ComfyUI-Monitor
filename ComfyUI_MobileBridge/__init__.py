"""Optional local ComfyUI frontend bridge: current tab -> mobile monitor.
Copy this directory into ComfyUI/custom_nodes/ and restart ComfyUI.
"""
import asyncio
import json
import urllib.request

from aiohttp import web
from server import PromptServer

NODE_CLASS_MAPPINGS = {}
WEB_DIRECTORY = './web'

@PromptServer.instance.routes.post('/comfy_mobile_bridge/snapshot')
async def publish_snapshot(request):
    try:
        payload = await request.read()
        if len(payload) > 3 * 1024 * 1024:
            raise ValueError('Workflow snapshot exceeds 3 MB')
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError('Expected snapshot object')
        def send():
            req = urllib.request.Request(
                'http://127.0.0.1:8765/api/bridge', data=payload,
                headers={'Content-Type': 'application/json',
                         'X-Comfy-Monitor-Bridge': 'local-comfy-ui'}, method='POST')
            with urllib.request.urlopen(req, timeout=6) as response:
                return json.load(response)
        result = await asyncio.to_thread(send)
        return web.json_response(result)
    except Exception as exc:
        return web.json_response({'error': str(exc)[:300]}, status=502)
