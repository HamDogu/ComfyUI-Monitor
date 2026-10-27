"""Convert ComfyUI UI/canvas workflow JSON into executable API (queue) format.

Mirrors the frontend's graphToPrompt using the server's /object_info node
definitions, so no browser is needed. Handles links, Reroute/Primitive/Set/Get
virtual nodes, muted (mode 2) and bypassed (mode 4) nodes, subgraphs (inner
node ids become "instance:inner" exactly like the frontend), promoted subgraph
widgets, control_after_generate/upload helper widgets, dict widgets_values
(VideoHelperSuite), rgthree Power Lora Loader rows and cg-use-everywhere
broadcasts (which the frontend only adds at queue time).
"""
import copy
import datetime as dt
import json
import re

SUBGRAPH_IN = -10
SUBGRAPH_OUT = -20
MUTED, BYPASS = 2, 4
CONTROL_VALUES = {'fixed', 'increment', 'decrement', 'randomize', 'increment-wrap'}
UPLOAD_OPTIONS = ('image_upload', 'video_upload', 'audio_upload', 'animated_image_upload', 'file_upload')
WIDGET_TYPES = {'INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO'}


class ConversionError(ValueError):
    pass


def is_ui_workflow(raw):
    return isinstance(raw, dict) and isinstance(raw.get('nodes'), list) and 'links' in raw


def ordered_specs(inputs, order=None):
    order = order or {}
    for group in ('required', 'optional'):
        specs = inputs.get(group) or {}
        for name in order.get(group) or list(specs):
            spec = specs.get(name)
            if isinstance(spec, (list, tuple)) and spec:
                yield name, spec[0], (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {})


def types_match(wanted, offered):
    if not wanted or not offered or '*' in (wanted, offered):
        return True
    return bool(set(str(wanted).split(',')) & set(str(offered).split(',')))


def map_widget_values(definition, values):
    """Pair saved widgets_values with server input names, in frontend widget order.

    INT/COMBO widgets with control_after_generate and *_upload combos are followed
    by a helper widget whose value is saved but never sent to the server. V3
    dynamic combos ("format" -> "format.codec") add their chosen option's widgets.
    """
    if isinstance(values, dict):
        # VideoHelperSuite stores widgets by name, including format-specific extras.
        return {k: v for k, v in values.items() if k != 'videopreview' and not isinstance(v, dict)}
    if not isinstance(values, list):
        return {}
    result, cursor = {}, [0]

    def take():
        value = values[cursor[0]] if cursor[0] < len(values) else None
        cursor[0] += 1
        return value

    def walk(inputs, order, prefix):
        for name, kind, options in ordered_specs(inputs, order):
            if cursor[0] >= len(values):
                return
            if options.get('forceInput') or options.get('rawLink'):
                continue
            if isinstance(kind, list):
                kind = 'COMBO'
            if kind == 'COMFY_DYNAMICCOMBO_V3':
                chosen = take()
                result[prefix + name] = chosen
                option = next((o for o in options.get('options') or []
                               if isinstance(o, dict) and o.get('key') == chosen), None)
                if option:
                    walk(option.get('inputs') or {}, None, prefix + name + '.')
                continue
            if kind not in WIDGET_TYPES:
                continue
            result[prefix + name] = take()
            nxt = values[cursor[0]] if cursor[0] < len(values) else None
            if (options.get('control_after_generate') or name in ('seed', 'noise_seed')) \
                    and isinstance(nxt, str) and nxt in CONTROL_VALUES:
                cursor[0] += 1
            elif any(options.get(key) for key in UPLOAD_OPTIONS) and (
                    nxt is None or nxt in ('image', 'video', 'audio', 'file', '')):
                cursor[0] += 1

    walk(definition.get('input') or {}, definition.get('input_order'), '')
    index = cursor[0]
    # rgthree Power Lora Loader keeps one dict per LoRA row after the defined widgets.
    rows = [v for v in values[index:] if isinstance(v, dict)]
    lora = 1
    for row in rows:
        if 'lora' in row and 'on' in row:
            result[f'lora_{lora}'] = row
            lora += 1
        elif row.get('type') == 'PowerLoraLoaderHeaderWidget':
            result['PowerLoraLoaderHeaderWidget'] = row
    return result


def hidden_state(node, definition):
    """Frontend extensions (Pixaroma) inject JSON state into hidden STRING inputs."""
    hidden = (definition.get('input') or {}).get('hidden') or {}
    result = {}
    for name, kind, _ in ordered_specs({'required': hidden}):
        if kind != 'STRING':
            continue
        values = node.get('widgets_values')
        state = next((v for v in values if isinstance(v, dict)), None) if isinstance(values, list) else None
        if state is None:
            for key, value in (node.get('properties') or {}).items():
                if 'state' in key.lower() and isinstance(value, str):
                    try:
                        state = json.loads(value)
                        break
                    except ValueError:
                        pass
        if not isinstance(state, dict):
            continue
        if node.get('type') == 'PixaromaLoraLoader':
            # Same fields as the extension's promptState(); cosmetic prefs are stripped.
            state = {'version': 1, 'sep': state.get('sep'), 'cacheMode': state.get('cacheMode'),
                     'loras': [{'name': e.get('name'), 'on': bool(e.get('on')), 'sm': e.get('sm'),
                                'sc': e.get('sc'), 'triggers': e.get('triggers')}
                               for e in state.get('loras') or [] if isinstance(e, dict)]}
        result[name] = json.dumps(state, separators=(',', ':'), ensure_ascii=False)
    return result


class Graph:
    def __init__(self, data, path, parent=None, instance=None):
        self.path = path            # tuple of instance ids leading to this graph
        self.parent = parent        # enclosing Graph, for subgraphs
        self.instance = instance    # the subgraph node (in parent) this graph expands
        self.nodes = {}
        for node in data.get('nodes') or []:
            if isinstance(node, dict) and 'id' in node:
                self.nodes[node['id']] = node
        self.links = {}
        for link in data.get('links') or []:
            if isinstance(link, list) and len(link) >= 5:
                self.links[link[0]] = {'origin_id': link[1], 'origin_slot': link[2],
                                       'target_id': link[3], 'target_slot': link[4],
                                       'type': link[5] if len(link) > 5 else '*'}
            elif isinstance(link, dict) and 'id' in link:
                self.links[link['id']] = link
        self.extra_links = {}       # (node_id, input_index) -> (origin_id, origin_slot), from Use Everywhere

    def dto_id(self, node_id):
        return ':'.join(str(p) for p in self.path + (node_id,))


class Converter:
    def __init__(self, workflow, object_info):
        if not is_ui_workflow(workflow):
            raise ConversionError('Not a ComfyUI UI workflow (expected "nodes" and "links")')
        self.info = object_info
        self.subgraphs = {}
        for sub in ((workflow.get('definitions') or {}).get('subgraphs') or []):
            if isinstance(sub, dict) and sub.get('id'):
                self.subgraphs[sub['id']] = sub
        self.root = Graph(workflow, ())
        self.warnings = []
        self.output = {}

    # -- node classification ---------------------------------------------------
    def kind(self, node):
        node_type = node.get('type')
        if node_type in self.subgraphs:
            return 'subgraph'
        if node_type in self.info:
            return 'real'
        if node_type == 'PrimitiveNode':
            return 'primitive'
        if node_type == 'GetNode':
            return 'get'
        return 'virtual'

    def missing_type(self, node):
        name = str(node.get('type'))
        if any(name in w for w in self.warnings):
            return
        self.warnings.append(f'Node type "{name}" is not installed in ComfyUI; links through it were dropped')

    # -- link resolution -------------------------------------------------------
    def link_of(self, graph, node, index):
        extra = graph.extra_links.get((node['id'], index))
        if extra:
            return extra
        inputs = node.get('inputs') or []
        if index >= len(inputs):
            return None
        link = graph.links.get(inputs[index].get('link'))
        return (link['origin_id'], link['origin_slot']) if link else None

    def resolve_input(self, graph, node, index, depth=0):
        """Where node input `index` gets its value: ('link', id, slot), ('value', v) or None."""
        if depth > 200:
            raise ConversionError('Link cycle detected while resolving reroutes/subgraphs')
        origin = self.link_of(graph, node, index)
        if origin is None:
            return None
        origin_id, origin_slot = origin
        if origin_id == SUBGRAPH_IN:
            return self.resolve_subgraph_input(graph, origin_slot, depth + 1)
        return self.resolve_output(graph, origin_id, origin_slot, depth + 1)

    def resolve_subgraph_input(self, graph, slot, depth):
        instance, parent = graph.instance, graph.parent
        if instance is None or parent is None:
            return None
        definition = self.subgraphs[instance['type']]
        sub_inputs = definition.get('inputs') or []
        name = sub_inputs[slot].get('name') if slot < len(sub_inputs) else None
        inst_inputs = instance.get('inputs') or []
        index = next((i for i, x in enumerate(inst_inputs) if x.get('name') == name), slot)
        resolved = self.resolve_input(parent, instance, index, depth + 1) if index < len(inst_inputs) else None
        if resolved is not None:
            return resolved
        # Promoted widget on the subgraph node: its value lives in the instance's widgets_values.
        values = instance.get('widgets_values')
        widget_inputs = [x for x in inst_inputs if x.get('widget')]
        if isinstance(values, list) and index < len(inst_inputs) and inst_inputs[index].get('widget'):
            position = widget_inputs.index(inst_inputs[index])
            if position < len(values):
                return ('value', values[position])
        return None

    def matching_input(self, node, slot):
        outputs = node.get('outputs') or []
        inputs = node.get('inputs') or []
        wanted = outputs[slot].get('type') if slot < len(outputs) else None
        if slot < len(inputs) and types_match(wanted, inputs[slot].get('type')):
            return slot
        for i, x in enumerate(inputs):
            if types_match(wanted, x.get('type')):
                return i
        return 0 if len(inputs) == 1 else None

    def resolve_output(self, graph, node_id, slot, depth=0):
        if depth > 200:
            raise ConversionError('Link cycle detected while resolving reroutes/subgraphs')
        node = graph.nodes.get(node_id)
        if node is None:
            return None
        kind, mode = self.kind(node), node.get('mode', 0)
        if mode == MUTED:
            return None
        if mode == BYPASS and kind in ('real', 'subgraph'):
            index = self.matching_input(node, slot)
            return None if index is None else self.resolve_input(graph, node, index, depth + 1)
        if kind == 'real':
            return ('link', graph.dto_id(node_id), slot)
        if kind == 'subgraph':
            sub = self.child_graph(graph, node)
            link = next((l for l in sub.links.values()
                         if l['target_id'] == SUBGRAPH_OUT and l['target_slot'] == slot), None)
            if link is None:
                return None
            if link['origin_id'] == SUBGRAPH_IN:
                return self.resolve_subgraph_input(sub, link['origin_slot'], depth + 1)
            return self.resolve_output(sub, link['origin_id'], link['origin_slot'], depth + 1)
        if kind == 'primitive':
            values = node.get('widgets_values')
            return ('value', values[0]) if isinstance(values, list) and values else None
        if kind == 'get':
            name = (node.get('widgets_values') or [None])[0]
            for scope in (graph, self.root):
                setter = next((n for n in scope.nodes.values() if n.get('type') == 'SetNode'
                               and (n.get('widgets_values') or [None])[0] == name), None)
                if setter:
                    return self.resolve_input(scope, setter, 0, depth + 1)
            self.warnings.append(f'GetNode "{name}" has no matching SetNode')
            return None
        # Reroute and other frontend-only nodes pass a matching input straight through.
        index = self.matching_input(node, slot)
        if index is None:
            self.missing_type(node)
            return None
        return self.resolve_input(graph, node, index, depth + 1)

    # -- graph walking ---------------------------------------------------------
    def child_graph(self, graph, node):
        cache = node.setdefault('__graph__', {})
        key = graph.path
        if key not in cache:
            data = self.subgraphs[node['type']]
            cache[key] = Graph(data, graph.path + (node['id'],), graph, node)
            self.apply_everywhere(cache[key])
        return cache[key]

    def apply_everywhere(self, graph):
        """cg-use-everywhere: send each UE input to unconnected inputs of the same type."""
        sources, ambiguous = {}, set()
        for node in graph.nodes.values():
            if 'Everywhere' not in str(node.get('type')) or node.get('mode', 0) != 0:
                continue
            props = (node.get('properties') or {}).get('ue_properties') or {}
            restricted = any(props.get(k) for k in ('group_restricted', 'color_restricted', 'title_regex',
                                                    'input_regex', 'group_regex'))
            if restricted:
                self.warnings.append(f'Use Everywhere node {node["id"]} uses group/colour/regex rules; '
                                     'those restrictions are ignored, check the result')
            for index, x in enumerate(node.get('inputs') or []):
                link = graph.links.get(x.get('link'))
                if link and x.get('type') not in (None, '*'):
                    origin = (link['origin_id'], link['origin_slot'])
                    if sources.setdefault(x['type'], origin) != origin:
                        ambiguous.add(x['type'])
        for name in ambiguous:
            # UE refuses to pick between equal-priority broadcasters of one type.
            sources.pop(name, None)
            self.warnings.append(f'Several Use Everywhere nodes broadcast {name}; none was connected')
        if not sources:
            return
        for node in graph.nodes.values():
            if 'Everywhere' in str(node.get('type')) or node.get('mode', 0) != 0:
                continue
            props = node.get('properties') or {}
            if props.get('rejects_ue_links'):
                continue
            blocked = (props.get('ue_properties') or {}).get('input_ue_unconnectable') or {}
            for index, x in enumerate(node.get('inputs') or []):
                if x.get('link') is not None or x.get('widget') or x.get('name') in blocked:
                    continue
                if str(node.get('type')).startswith('VHS_') and x.get('name') == 'vae':
                    continue  # VideoHelperSuite's reject_ue_connection hook
                # UE never sends a value back into the node that produced it.
                if x.get('type') in sources and sources[x['type']][0] != node['id']:
                    graph.extra_links[(node['id'], index)] = sources[x['type']]

    def emit(self, graph):
        for node_id, node in graph.nodes.items():
            kind, mode = self.kind(node), node.get('mode', 0)
            if mode in (MUTED, BYPASS):
                continue
            if kind == 'subgraph':
                self.emit(self.child_graph(graph, node))
                continue
            if kind != 'real':
                if kind == 'virtual' and any(o.get('links') for o in node.get('outputs') or []) \
                        and node.get('type') not in ('Reroute', 'SetNode'):
                    self.missing_type(node)
                continue
            definition = self.info[node['type']]
            inputs = map_widget_values(definition, node.get('widgets_values'))
            inputs.update(hidden_state(node, definition))
            for index, x in enumerate(node.get('inputs') or []):
                name = x.get('name')
                if not name or self.link_of(graph, node, index) is None:
                    continue
                resolved = self.resolve_input(graph, node, index)
                if resolved is None:
                    inputs.pop(name, None)
                elif resolved[0] == 'value':
                    inputs[name] = resolved[1]
                else:
                    inputs[name] = [resolved[1], resolved[2]]
            inputs = {k: ({'__value__': v} if isinstance(v, list) and not self.is_link(v) else v)
                      for k, v in inputs.items()}
            title = node.get('title') or definition.get('display_name') or node['type']
            self.output[graph.dto_id(node_id)] = {'inputs': inputs, 'class_type': node['type'],
                                                  '_meta': {'title': str(title)}}

    @staticmethod
    def is_link(value):
        return len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)

    def convert(self):
        self.apply_everywhere(self.root)
        self.emit(self.root)
        for entry in self.output.values():
            for name, value in list(entry['inputs'].items()):
                if isinstance(value, list) and self.is_link(value) and value[0] not in self.output:
                    del entry['inputs'][name]
        if not self.output:
            raise ConversionError('No executable nodes found (all muted, bypassed or not installed)')
        return self.output


DATE_TOKENS = re.compile(r'yyyy|yy|MM|M|dd|d|hh|h|mm|m|ss|s')


def format_date(pattern, now):
    values = {'yyyy': f'{now.year:04d}', 'yy': f'{now.year % 100:02d}', 'MM': f'{now.month:02d}',
              'M': str(now.month), 'dd': f'{now.day:02d}', 'd': str(now.day), 'hh': f'{now.hour:02d}',
              'h': str(now.hour), 'mm': f'{now.minute:02d}', 'm': str(now.minute),
              'ss': f'{now.second:02d}', 's': str(now.second)}
    return DATE_TOKENS.sub(lambda match: values[match.group(0)], pattern)


def apply_text_replacements(prompt, now=None):
    """Expand %date:FORMAT% and %Node title.widget% in filename_prefix values.

    The frontend does this as it queues; doing it per submission keeps
    scheduled/repeated runs from overwriting each other's dated names.
    """
    now = now or dt.datetime.now()

    def lookup(match):
        token = match.group(1)
        if token.startswith('date:'):
            return format_date(token[5:], now)
        if '.' in token:
            node_name, widget = token.rsplit('.', 1)
            for node in prompt.values():
                if node_name in ((node.get('_meta') or {}).get('title'), node.get('class_type')):
                    value = (node.get('inputs') or {}).get(widget)
                    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                        return str(value)
        return match.group(0)

    for node in prompt.values():
        inputs = node.get('inputs') or {}
        value = inputs.get('filename_prefix')
        if isinstance(value, str) and '%' in value:
            inputs['filename_prefix'] = re.sub(r'%([^%]+)%', lookup, value)
    return prompt


def convert(workflow, object_info):
    """Return (api_prompt, warnings) for a UI workflow."""
    converter = Converter(copy.deepcopy(workflow), object_info)
    return converter.convert(), converter.warnings
