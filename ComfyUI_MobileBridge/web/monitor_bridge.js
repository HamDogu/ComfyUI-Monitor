// Optional ComfyUI frontend bridge. Uses ComfyUI's own graphToPrompt() exporter.
// Only the active canvas is captured; previously viewed tabs retain their last snapshot.
import { app } from '../../scripts/app.js';

app.registerExtension({
  name: 'comfy.mobile.monitor.bridge',
  async setup() {
    let lastSignature = '', lastSent = 0, sending = false;
    async function snapshot() {
      if(sending || document.hidden || !app.graph?.nodes?.length) return;
      sending=true;
      try {
        const serialized = await app.graphToPrompt();
        const graph = serialized.workflow || {};
        const prompt = serialized.output || {};
        if(!Object.keys(prompt).length)return;
        const active = app.extensionManager?.workflow?.activeWorkflow ||
                       app.workflowStore?.activeWorkflow || null;
        const graph_id = String(active?.path || active?.id || graph.id || graph.extra?.uuid || 'active');
        const title = String(active?.name || active?.filename || active?.path ||
                             document.title.replace(/\s*-\s*ComfyUI.*/i, '') || 'Active workflow');
        // The UI graph lets the monitor add Use Everywhere links, which graphToPrompt() omits.
        // Canvas pan/zoom is dropped so moving around the canvas doesn't resend the snapshot.
        const workflow={...graph,extra:{...(graph.extra||{}),ds:undefined}};
        const payload={graph_id,title,prompt,workflow};
        const encoded=JSON.stringify(payload);
        if(encoded===lastSignature && Date.now()-lastSent<60000)return;
        const res=await fetch('/comfy_mobile_bridge/snapshot',{
          method:'POST',headers:{'Content-Type':'application/json'},body:encoded
        });
        if(!res.ok) throw Error('Monitor not running or bridge rejected snapshot: '+res.status);
        lastSignature=encoded;lastSent=Date.now();
      } catch(err) {
        // Keep ComfyUI working normally if monitor is unavailable.
        if(Date.now()-lastSent>60000)console.debug('[Comfy Mobile Bridge]',err);
      } finally{sending=false;}
    }
    setInterval(snapshot,7000);
    setTimeout(snapshot,3000);
  }
});
