"""
Phase 1 DSL Renderer - Deterministic HTML Generation

Key Features:
  - Gravity-Based Collision Resolution (Layout Post-Processing)
  - XSS Prevention via HTML Entity Escaping
  - Mobile Responsive CSS Media Queries
"""
import json
import html
import hashlib
from typing import List, Dict, Any

# Try importing schema, handle if not present (for standalone testing)
try:
    from src.models.schema import HMIPanel, AnyWidget, WidgetType, LayoutItem
except ImportError:
    # Fallback for safe imports if run directly
    pass

# ==========================================
# Constant Templates (Deterministic)
# ==========================================

HTML_SKELETON = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.13.3/dist/cdn.min.js"></script>
    <script src="https://cdn.rawgit.com/Mikhus/canvas-gauges/gh-pages/download/2.1.7/all/gauge.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
    <script src="https://cdn.jsdelivr.net/npm/mqtt@5.10.4/dist/mqtt.min.js"></script>
    
    <style>
        /* Custom Scrollbar for Industrial Look */
        ::-webkit-scrollbar {{ width: 8px; }}
        ::-webkit-scrollbar-track {{ background: #1e293b; }}
        ::-webkit-scrollbar-thumb {{ background: #475569; border-radius: 4px; }}
        
        .grid-container {{
            display: grid;
            grid-template-columns: repeat(12, minmax(0, 1fr));
            grid-auto-rows: 100px;
            gap: 1rem;
            padding: 1rem;
        }}
        
        /* Widget Card Style */
        .widget-card {{
            background-color: {bg_color};
            border: 1px solid {border_color};
            border-radius: 0.5rem;
            padding: 1rem;
            display: flex;
            flex-direction: column;
            position: relative;
            overflow: hidden;
        }}
        
        /* Mobile Breakpoint - Responsive Design */
        @media (max-width: 768px) {{
            .grid-container {{
                display: flex;
                flex-direction: column;
                gap: 1.5rem;
            }}
            .widget-card {{
                grid-column: span 12 !important;
                grid-row: auto !important;
                min-height: 120px; /* Finger-friendly touch targets */
            }}
        }}
    </style>
</head>
<body class="{body_class} text-slate-200 min-h-screen font-sans" x-data="hmiStore">

    <header class="bg-slate-900 border-b border-slate-700 p-4 mb-4 flex justify-between items-center">
        <div>
            <h1 class="text-xl font-bold text-blue-400">{title}</h1>
            <p class="text-xs text-slate-500">{description}</p>
        </div>
        <div class="text-right">
            <div class="text-xs text-slate-500 font-mono">DSL v{version}</div>
            <div class="text-[10px] text-slate-600 font-mono">Hash: {dsl_hash}</div>
        </div>
    </header>

    <main class="container mx-auto">
        <div class="grid-container">
            {widgets_html}
        </div>
    </main>

    <script>
        document.addEventListener('alpine:init', () => {{
            Alpine.data('hmiStore', () => ({{
                // Initial State
                values: {initial_values_json},

                // Bindings Metadata
                bindings: {bindings_json},
                constraints: {guard_constraints_json},
                runtimeConfig: {runtime_config_json},
                events: [],
                transport: {{
                    mqttClients: {{}},
                    mqttClientByWidget: {{}},
                    mqttConnected: {{}},
                    modbusSocket: null,
                    mockTimer: null,
                    tick: 0
                }},

                init() {{
                    console.log('HMI System Initialized');
                    this.logEvent('telemetry', {{
                        kind: 'init',
                        widget_count: Object.keys(this.values || {{}}).length
                    }});
                    this.initTransports();
                    window.__hmiSession = () => this.exportSessionLog();
                    this.startMockDataStream();
                }},

                _normalizeAccessMode(bind) {{
                    const raw = String((bind || {{}}).access || (bind || {{}}).access_mode || 'rw').toLowerCase();
                    if (raw === 'read') return 'r';
                    if (raw === 'write') return 'w';
                    if (raw === 'r' || raw === 'w' || raw === 'rw') return raw;
                    return 'rw';
                }},

                _canWrite(bind) {{
                    const mode = this._normalizeAccessMode(bind);
                    return mode === 'w' || mode === 'rw';
                }},

                _canRead(bind) {{
                    const mode = this._normalizeAccessMode(bind);
                    return mode === 'r' || mode === 'rw';
                }},

                _bindingEndpoint(bind) {{
                    return (bind || {{}}).topic
                        || (bind || {{}}).address
                        || (bind || {{}}).register
                        || (bind || {{}}).modbus_register
                        || '';
                }},

                _toNumeric(value) {{
                    if (typeof value === 'number') return value;
                    if (typeof value === 'boolean') return value ? 1 : 0;
                    const n = Number(value);
                    return Number.isFinite(n) ? n : value;
                }},

                initTransports() {{
                    this._initMqttClients();
                    this._initModbusBridge();
                }},

                _mqttClientKey(bind) {{
                    const host = (bind && bind.host) ? bind.host : 'localhost';
                    const tls = !!(bind && bind.tls);
                    const portDefault = tls ? 8084 : 8083;
                    const port = (bind && bind.port) ? bind.port : portDefault;
                    return `${{tls ? 'wss' : 'ws'}}://${{host}}:${{port}}`;
                }},

                _initMqttClients() {{
                    const widgetEntries = Object.entries(this.bindings || {{}})
                        .filter(([, bind]) => bind && String(bind.protocol || '').toLowerCase() === 'mqtt');
                    if (!widgetEntries.length) return;

                    if (!window.mqtt || typeof window.mqtt.connect !== 'function') {{
                        this.logEvent('error', {{
                            code: 'mqtt_sdk_missing',
                            message: 'mqtt.js not loaded'
                        }});
                        return;
                    }}

                    for (const [wid, bind] of widgetEntries) {{
                        const clientKey = this._mqttClientKey(bind);
                        if (!this.transport.mqttClients[clientKey]) {{
                            const tls = !!bind.tls;
                            const host = bind.host || 'localhost';
                            const port = bind.port || (tls ? 8084 : 8083);
                            const wsPath = bind.ws_path || '/mqtt';
                            const url = `${{tls ? 'wss' : 'ws'}}://${{host}}:${{port}}${{wsPath}}`;

                            const client = window.mqtt.connect(url, {{
                                clientId: bind.client_id || `genui_${{Date.now()}}_${{Math.floor(Math.random() * 10000)}}`,
                                username: bind.username || undefined,
                                password: bind.password || undefined,
                                clean: true,
                                reconnectPeriod: 3000,
                                connectTimeout: 5000
                            }});

                            client.on('connect', () => {{
                                this.transport.mqttConnected[clientKey] = true;
                                this.logEvent('telemetry', {{
                                    kind: 'mqtt_connected',
                                    broker: clientKey
                                }});
                            }});

                            client.on('reconnect', () => {{
                                this.logEvent('telemetry', {{
                                    kind: 'mqtt_reconnect',
                                    broker: clientKey
                                }});
                            }});

                            client.on('error', (err) => {{
                                this.logEvent('error', {{
                                    code: 'mqtt_error',
                                    broker: clientKey,
                                    message: String((err && err.message) || err || 'unknown')
                                }});
                            }});

                            client.on('message', (topic, payloadBuf) => {{
                                const payloadRaw = payloadBuf ? payloadBuf.toString() : '';
                                for (const [widgetId, mappedClientKey] of Object.entries(this.transport.mqttClientByWidget || {{}})) {{
                                    if (mappedClientKey !== clientKey) continue;
                                    const b = this.bindings[widgetId] || {{}};
                                    const bTopic = this._bindingEndpoint(b);
                                    if (topic === bTopic && this._canRead(b)) {{
                                        this._handleTelemetry(widgetId, payloadRaw, 'mqtt');
                                    }}
                                }}
                            }});

                            this.transport.mqttClients[clientKey] = client;
                        }}

                        this.transport.mqttClientByWidget[wid] = clientKey;

                        const endpoint = this._bindingEndpoint(bind);
                        if (endpoint && this._canRead(bind)) {{
                            const client = this.transport.mqttClients[clientKey];
                            try {{
                                client.subscribe(endpoint, {{ qos: bind.qos || 0 }}, (err) => {{
                                    if (err) {{
                                        this.logEvent('error', {{
                                            code: 'mqtt_subscribe_failed',
                                            widget_id: wid,
                                            endpoint: endpoint,
                                            message: String(err.message || err)
                                        }});
                                    }} else {{
                                        this.logEvent('telemetry', {{
                                            kind: 'mqtt_subscribed',
                                            widget_id: wid,
                                            endpoint: endpoint
                                        }});
                                    }}
                                }});
                            }} catch (e) {{
                                this.logEvent('error', {{
                                    code: 'mqtt_subscribe_exception',
                                    widget_id: wid,
                                    endpoint: endpoint,
                                    message: String(e && e.message ? e.message : e)
                                }});
                            }}
                        }}
                    }}
                }},

                _initModbusBridge() {{
                    const wsUrl = (this.runtimeConfig || {{}}).modbus_ws_url;
                    if (!wsUrl) return;
                    try {{
                        const socket = new WebSocket(wsUrl);
                        this.transport.modbusSocket = socket;

                        socket.addEventListener('open', () => {{
                            this.logEvent('telemetry', {{
                                kind: 'modbus_bridge_connected',
                                endpoint: wsUrl
                            }});
                        }});

                        socket.addEventListener('message', (ev) => {{
                            let payload = null;
                            try {{
                                payload = JSON.parse(ev.data);
                            }} catch (_e) {{
                                payload = {{ value: ev.data }};
                            }}
                            const widgetId = payload.widget_id || payload.target || payload.id;
                            if (widgetId && Object.prototype.hasOwnProperty.call(this.values, widgetId)) {{
                                this._handleTelemetry(widgetId, payload, 'modbus_ws');
                            }} else {{
                                this.logEvent('telemetry', {{
                                    kind: 'modbus_message',
                                    payload: payload
                                }});
                            }}
                        }});

                        socket.addEventListener('error', (ev) => {{
                            this.logEvent('error', {{
                                code: 'modbus_bridge_error',
                                endpoint: wsUrl,
                                detail: String(ev && ev.message ? ev.message : 'bridge_error')
                            }});
                        }});
                    }} catch (e) {{
                        this.logEvent('error', {{
                            code: 'modbus_bridge_exception',
                            endpoint: wsUrl,
                            detail: String(e && e.message ? e.message : e)
                        }});
                    }}
                }},

                _handleTelemetry(widgetId, payload, protocol) {{
                    let value = payload;
                    if (typeof payload === 'string') {{
                        try {{
                            const parsed = JSON.parse(payload);
                            value = parsed;
                        }} catch (_e) {{
                            value = payload;
                        }}
                    }}

                    let nextVal = value;
                    if (value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'value')) {{
                        nextVal = value.value;
                    }}
                    nextVal = this._toNumeric(nextVal);
                    if (Object.prototype.hasOwnProperty.call(this.values, widgetId)) {{
                        this.values[widgetId] = nextVal;
                    }}
                    this.logEvent('telemetry', {{
                        kind: 'value_update',
                        widget_id: widgetId,
                        value: nextVal,
                        protocol: protocol || 'unknown'
                    }});
                }},

                sendBindingCommand(widgetId, bind, value) {{
                    if (!bind) return;
                    const protocol = String(bind.protocol || 'mock').toLowerCase();
                    const endpoint = this._bindingEndpoint(bind);

                    if (!this._canWrite(bind)) {{
                        this.logEvent('error', {{
                            code: 'binding_write_forbidden',
                            widget_id: widgetId,
                            protocol: protocol,
                            endpoint: endpoint
                        }});
                        return;
                    }}

                    if (protocol === 'mqtt') {{
                        const clientKey = this.transport.mqttClientByWidget[widgetId] || this._mqttClientKey(bind);
                        const client = this.transport.mqttClients[clientKey];
                        if (!client || !endpoint) {{
                            this.logEvent('error', {{
                                code: 'mqtt_publish_unavailable',
                                widget_id: widgetId,
                                endpoint: endpoint || ''
                            }});
                            return;
                        }}
                        const payload = JSON.stringify({{
                            widget_id: widgetId,
                            value: value,
                            ts_utc: new Date().toISOString()
                        }});
                        try {{
                            client.publish(endpoint, payload, {{ qos: bind.qos || 0 }}, (err) => {{
                                if (err) {{
                                    this.logEvent('error', {{
                                        code: 'mqtt_publish_failed',
                                        widget_id: widgetId,
                                        endpoint: endpoint,
                                        message: String(err.message || err)
                                    }});
                                }} else {{
                                    this.logEvent('ack', {{
                                        widget_id: widgetId,
                                        protocol: 'mqtt',
                                        endpoint: endpoint,
                                        value: value
                                    }});
                                }}
                            }});
                        }} catch (e) {{
                            this.logEvent('error', {{
                                code: 'mqtt_publish_exception',
                                widget_id: widgetId,
                                endpoint: endpoint,
                                message: String(e && e.message ? e.message : e)
                            }});
                        }}
                        return;
                    }}

                    if (protocol === 'modbus') {{
                        const socket = this.transport.modbusSocket;
                        const registerAddr = bind.register ?? bind.modbus_register ?? bind.address;
                        if (!socket || socket.readyState !== 1) {{
                            this.logEvent('error', {{
                                code: 'modbus_bridge_unavailable',
                                widget_id: widgetId,
                                register: registerAddr
                            }});
                            return;
                        }}
                        const payload = {{
                            action: 'write_register',
                            widget_id: widgetId,
                            register: registerAddr,
                            value: value
                        }};
                        socket.send(JSON.stringify(payload));
                        this.logEvent('ack', {{
                            widget_id: widgetId,
                            protocol: 'modbus',
                            endpoint: registerAddr,
                            value: value
                        }});
                        return;
                    }}

                    this.logEvent('ack', {{
                        widget_id: widgetId,
                        protocol: protocol,
                        endpoint: endpoint,
                        value: value
                    }});
                }},

                // Core Communication Hook
                updateValue(id, value) {{
                    const guard = this.guardCommand({{
                        widget_id: id,
                        value: value,
                        unit: (this.constraints[id] || {{}}).unit || 'unitless',
                        action: 'set_value'
                    }});

                    this.logEvent('command_guard', {{
                        command: {{
                            widget_id: id,
                            value: value,
                            unit: (this.constraints[id] || {{}}).unit || 'unitless',
                            action: 'set_value'
                        }},
                        allowed: guard.allowed,
                        reason: guard.reason,
                        rule_source: guard.rule_source,
                        constraint: guard.constraint
                    }});

                    if (!guard.allowed) {{
                        this.logEvent('error', {{
                            code: 'runtime_guard_reject',
                            widget_id: id,
                            value: value,
                            reason: guard.reason
                        }});
                        return;
                    }}

                    this.values[id] = guard.value;
                    const bind = this.bindings[id];
                    this.logEvent('command', {{
                        widget_id: id,
                        value: guard.value,
                        unit: (this.constraints[id] || {{}}).unit || 'unitless',
                        action: 'set_value',
                        guard: {{ allowed: true, reason: guard.reason }}
                    }});

                    if (bind) {{
                        this.sendBindingCommand(id, bind, guard.value);
                    }}
                }},

                guardCommand(command) {{
                    const c = this.constraints[command.widget_id];
                    if (!c) {{
                        return {{
                            allowed: true,
                            reason: 'no_constraint',
                            value: command.value,
                            rule_source: null,
                            constraint: null
                        }};
                    }}

                    const toNum = (v) => {{
                        if (typeof v === 'number') return v;
                        if (typeof v === 'boolean') return v ? 1 : 0;
                        const n = Number(v);
                        return Number.isFinite(n) ? n : null;
                    }};

                    const vNum = toNum(command.value);
                    if (vNum === null) {{
                        return {{
                            allowed: false,
                            reason: 'non_numeric_value',
                            value: command.value,
                            rule_source: c.source_ref || null,
                            constraint: c
                        }};
                    }}

                    if (c.max !== null && c.max !== undefined && vNum > Number(c.max)) {{
                        return {{
                            allowed: false,
                            reason: `exceeds_max:${{c.max}}`,
                            value: command.value,
                            rule_source: c.source_ref || null,
                            constraint: c
                        }};
                    }}

                    if (c.min !== null && c.min !== undefined && vNum < Number(c.min)) {{
                        return {{
                            allowed: false,
                            reason: `below_min:${{c.min}}`,
                            value: command.value,
                            rule_source: c.source_ref || null,
                            constraint: c
                        }};
                    }}

                    return {{
                        allowed: true,
                        reason: 'within_range',
                        value: vNum,
                        rule_source: c.source_ref || null,
                        constraint: c
                    }};
                }},

                logEvent(eventType, payload) {{
                    this.events.push({{
                        ts_utc: new Date().toISOString(),
                        event_type: eventType,
                        payload: payload || {{}}
                    }});
                }},

                replaySession(events) {{
                    const nextValues = JSON.parse(JSON.stringify(this.values || {{}}));
                    const seq = Array.isArray(events) ? events : [];
                    for (const ev of seq) {{
                        if (!ev || !ev.event_type) continue;
                        if (ev.event_type === 'command' || ev.event_type === 'command_guard') {{
                            const p = ev.payload || {{}};
                            const cmd = p.command || p;
                            const guard = p.allowed === undefined ? p.guard : {{ allowed: p.allowed }};
                            if ((guard && guard.allowed === false) || p.allowed === false) continue;
                            const id = cmd.widget_id || cmd.target;
                            if (id && Object.prototype.hasOwnProperty.call(cmd, 'value')) {{
                                nextValues[id] = cmd.value;
                            }}
                        }}
                    }}
                    this.values = nextValues;
                    return nextValues;
                }},

                exportSessionLog() {{
                    return {{
                        generated_utc: new Date().toISOString(),
                        events: this.events.slice(),
                        values: JSON.parse(JSON.stringify(this.values || {{}})),
                        transport: {{
                            mqtt_connected: JSON.parse(JSON.stringify(this.transport.mqttConnected || {{}})),
                            has_modbus_bridge: !!(this.transport.modbusSocket && this.transport.modbusSocket.readyState === 1)
                        }}
                    }};
                }},

                _hasLiveTelemetry() {{
                    const hasMqtt = Object.keys(this.transport.mqttClients || {{}}).length > 0;
                    const hasModbusBridge = !!(this.transport.modbusSocket && this.transport.modbusSocket.readyState <= 1);
                    return hasMqtt || hasModbusBridge;
                }},
                
                // Simulation Loop (Deterministic Mock)
                startMockDataStream() {{
                    if (this._hasLiveTelemetry()) return;
                    if (this.transport.mockTimer) return;
                    const ids = Object.keys(this.values || {{}}).sort();
                    this.transport.mockTimer = setInterval(() => {{
                        this.transport.tick += 1;
                        for (let i = 0; i < ids.length; i += 1) {{
                            const wid = ids[i];
                            const bind = this.bindings[wid] || null;
                            const protocol = bind ? String(bind.protocol || '').toLowerCase() : 'mock';
                            const current = this.values[wid];
                            if (typeof current !== 'number') continue;

                            // Keep writable endpoints under operator control.
                            if (bind && this._canWrite(bind)) continue;

                            const c = this.constraints[wid] || {{}};
                            const min = Number.isFinite(Number(c.min)) ? Number(c.min) : 0;
                            const max = Number.isFinite(Number(c.max)) ? Number(c.max) : (min + 100);
                            const span = Math.max(max - min, 1);
                            const phase = (this.transport.tick + i) / 4;
                            const next = min + ((Math.sin(phase) + 1) / 2) * span;
                            const nextRounded = Number(next.toFixed(3));
                            this.values[wid] = nextRounded;
                            this.logEvent('telemetry', {{
                                kind: 'mock_stream',
                                widget_id: wid,
                                value: nextRounded,
                                protocol: protocol || 'mock'
                            }});
                        }}
                    }}, 1500);
                }}
            }}))
        }})
    </script>
</body>
</html>
"""


# ==========================================
# Layout Collision Resolution (Neuro-Symbolic)
# ==========================================

def _resolve_layout_collisions(layout_items: List[Any], cols: int = 12) -> List[Any]:
    """
    Gravity-Based Collision Resolver
    
    学术意义: LLM的空间推理能力较弱, 我们不能信任它生成的坐标.
    此算法作为后处理(Post-processing), 强制消除组件重叠.
    
    Algorithm:
    1. Sort by (y, x) to respect user/LLM intent ordering
    2. For each widget, check if it overlaps occupied cells
    3. If overlap, push widget down (increase y) until safe
    4. Mark cells as occupied
    
    This is a classic Symbolic Solver in a Neuro-Symbolic architecture.
    """
    if not layout_items:
        return layout_items
    
    # 1. Sort by explicit intent
    sorted_items = sorted(layout_items, key=lambda i: (i.y, i.x))
    
    # 2. Occupancy Grid: set of (row, col) tuples
    occupied = set()

    for item in sorted_items:
        # Collision detection loop
        while True:
            is_overlapping = False
            
            # Check all cells this item would cover
            for r in range(item.y, item.y + item.h):
                for c in range(item.x, min(item.x + item.w, cols)):
                    if (r, c) in occupied:
                        is_overlapping = True
                        break
                if is_overlapping:
                    break
            
            if is_overlapping:
                # Strategy: simple gravity - push down one row
                item.y += 1
            else:
                # Found safe spot, mark as occupied
                for r in range(item.y, item.y + item.h):
                    for c in range(item.x, min(item.x + item.w, cols)):
                        occupied.add((r, c))
                break
    
    return sorted_items


# ==========================================
# Widget Renderers
# ==========================================

def _render_slider(w: AnyWidget) -> str:
    """Render a Slider widget with Alpine binding. XSS-Safe."""
    # SECURITY: Always escape user-provided strings
    safe_label = html.escape(w.label)
    
    return f"""
    <div class="h-full flex flex-col justify-between">
        <div class="flex justify-between items-center mb-2">
            <label class="text-sm font-medium text-slate-300">{safe_label}</label>
            <span class="text-xs font-mono bg-slate-700 px-2 py-1 rounded" x-text="values['{w.id}']"></span>
        </div>
        <div class="flex-grow flex items-center">
            <input type="range" 
                   min="{w.min}" max="{w.max}" step="{w.step}"
                   x-model.number="values['{w.id}']"
                   @input="updateValue('{w.id}', $event.target.value)"
                   class="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500">
        </div>
        <div class="flex justify-between text-[10px] text-slate-500 mt-1">
            <span>{w.min}</span>
            <span>{w.max}</span>
        </div>
    </div>
    """

def _render_switch(w: AnyWidget) -> str:
    """Render a Toggle Switch. XSS-Safe."""
    # SECURITY: Always escape user-provided strings
    safe_label = html.escape(w.label)
    safe_on_label = html.escape(w.on_label)
    safe_off_label = html.escape(w.off_label)
    
    color_on = getattr(w, 'color_on', 'green')
    color_class = f"peer-checked:bg-{html.escape(color_on)}-600"
    
    return f"""
    <div class="h-full flex flex-col justify-center items-center">
        <div class="text-sm font-medium text-slate-300 mb-3">{safe_label}</div>
        <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" 
                   class="sr-only peer"
                   x-model="values['{w.id}']"
                   @change="updateValue('{w.id}', $event.target.checked)">
            <div class="w-14 h-7 bg-slate-700 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-500 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-0.5 after:left-[4px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-6 after:w-6 after:transition-all {color_class}"></div>
        </label>
        <div class="mt-2 text-xs font-mono text-slate-400">
            <span x-show="values['{w.id}']">{safe_on_label}</span>
            <span x-show="!values['{w.id}']">{safe_off_label}</span>
        </div>
    </div>
    """

def _render_gauge(w: AnyWidget) -> str:
    """Render a Canvas Gauge. XSS-Safe."""
    # SECURITY: Always escape user-provided strings
    safe_label = html.escape(w.label)
    safe_unit = html.escape(w.safety.unit) if w.safety and w.safety.unit else ""
    
    return f"""
    <div class="h-full flex flex-col items-center justify-center">
        <div class="text-sm font-medium text-slate-300 mb-1">{safe_label}</div>
        <canvas data-type="radial-gauge"
                data-width="150"
                data-height="150"
                data-units="{safe_unit}"
                data-min-value="{w.min}"
                data-max-value="{w.max}"
                data-major-ticks="0,20,40,60,80,100"
                data-minor-ticks="2"
                data-stroke-ticks="true"
                data-color-plate="transparent"
                data-border-shadow-width="0"
                data-borders="false"
                data-needle-type="arrow"
                data-needle-width="2"
                data-needle-circle-size="7"
                data-needle-circle-outer="true"
                data-needle-circle-inner="false"
                data-animation-duration="1500"
                data-animation-rule="linear"
                data-value="{w.value}"
                x-bind:data-value="values['{w.id}']"
        ></canvas>
    </div>
    """


def _render_input(w: AnyWidget) -> str:
    """Render a numeric/text input widget."""
    safe_label = html.escape(w.label)
    safe_type = "number" if getattr(w, "input_type", "number") == "number" else "text"
    min_attr = f' min="{getattr(w, "min")}"' if getattr(w, "min", None) is not None else ""
    max_attr = f' max="{getattr(w, "max")}"' if getattr(w, "max", None) is not None else ""
    step_attr = f' step="{getattr(w, "step")}"' if getattr(w, "step", None) is not None else ""
    return f"""
    <div class="h-full flex flex-col justify-center">
        <label class="text-sm font-medium text-slate-300 mb-2">{safe_label}</label>
        <input type="{safe_type}"
               {min_attr}{max_attr}{step_attr}
               value="{html.escape(str(getattr(w, 'value', '')))}"
               x-model=\"values['{w.id}']\"
               @input=\"updateValue('{w.id}', $event.target.value)\"
               class="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-slate-100">
    </div>
    """


def _render_select(w: AnyWidget) -> str:
    """Render a dropdown select widget for discrete choices."""
    safe_label = html.escape(w.label)
    options = getattr(w, "options", []) or []
    options_html = []
    for opt in options:
        opt_str = str(opt)
        safe_opt = html.escape(opt_str)
        options_html.append(f'<option value="{safe_opt}">{safe_opt}</option>')

    return f"""
    <div class="h-full flex flex-col justify-center">
        <label class="text-sm font-medium text-slate-300 mb-2">{safe_label}</label>
        <select x-model=\"values['{w.id}']\"
                @change=\"updateValue('{w.id}', $event.target.value)\"
                class="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-slate-100">
            {"".join(options_html)}
        </select>
    </div>
    """


def _render_radio(w: AnyWidget) -> str:
    """Render a radio-group widget for discrete choices."""
    safe_label = html.escape(w.label)
    options = getattr(w, "options", []) or []
    option_parts = []

    for idx, opt in enumerate(options):
        opt_str = str(opt)
        safe_opt = html.escape(opt_str)
        option_id = f"{html.escape(str(w.id))}_opt_{idx}"
        option_parts.append(
            f"""
            <label for="{option_id}" class="inline-flex items-center gap-2 text-sm text-slate-300">
                <input id="{option_id}"
                       type="radio"
                       name="{html.escape(str(w.id))}"
                       value="{safe_opt}"
                       x-model=\"values['{w.id}']\"
                       @change=\"updateValue('{w.id}', $event.target.value)\"
                       class="accent-blue-500">
                <span>{safe_opt}</span>
            </label>
            """
        )

    return f"""
    <div class="h-full flex flex-col justify-center">
        <div class="text-sm font-medium text-slate-300 mb-2">{safe_label}</div>
        <div class="flex flex-wrap gap-3">
            {"".join(option_parts)}
        </div>
    </div>
    """


def _render_plot(w: AnyWidget) -> str:
    """Render a minimal line chart container bound to DSL defaults."""
    safe_label = html.escape(w.label)
    canvas_id = f"plot_{html.escape(w.id)}"
    return f"""
    <div class="h-full flex flex-col">
        <div class="text-sm font-medium text-slate-300 mb-2">{safe_label}</div>
        <canvas id="{canvas_id}" style="height:100%;width:100%"></canvas>
        <script>
            (function(){{
                const el = document.getElementById('{canvas_id}');
                if (!el || !window.Chart) return;
                const ctx = el.getContext('2d');
                new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        labels: ['t-4','t-3','t-2','t-1','t'],
                        datasets: [{{ label: '{safe_label}', data: [1,2,1.5,2.2,2.0], borderColor: '#60a5fa', tension: 0.35 }}]
                    }},
                    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
                }});
            }})();
        </script>
    </div>
    """

def _get_layout_style(widget_id: str, layout_list: List[Any]) -> str:
    """
    Convert DSL layout item (x, y, w, h) to CSS Grid style.
    Grid is 12 columns.
    """
    item = next((i for i in layout_list if i.i == widget_id), None)
    if not item:
        # Fallback if validation failed (though Pydantic should catch this)
        return "grid-column: span 12; grid-row: span 2;"
    
    # CSS Grid lines start at 1
    col_start = item.x + 1
    col_span = item.w
    row_start = item.y + 1
    row_span = item.h
    
    return f"grid-column: {col_start} / span {col_span}; grid-row: {row_start} / span {row_span};"


# ==========================================
# Main Entry Point
# ==========================================

def render_panel(panel: 'HMIPanel') -> str:
    """
    Main Entry Point: Converts HMIPanel object to HTML string.
    
    Features:
      - Determinism: Widgets sorted by ID before rendering
      - Safety: All labels escaped against XSS
      - Robustness: Layout collisions auto-resolved
    """
    
    # Step 0: Deterministic Layout Fix (Gravity-Based Collision Resolution)
    # This ensures even if LLM generates overlapping coords, the UI is tidy
    panel.layout = _resolve_layout_collisions(panel.layout)
    
    # 1. Sort widgets for deterministic output order
    sorted_widgets = sorted(panel.widgets, key=lambda x: x.id)
    
    # 2. Extract Initial State & Bindings
    initial_values = {}
    bindings_map = {}
    guard_constraints_map = {}
    
    widgets_html_parts = []
    
    for w in sorted_widgets:
        # State
        initial_values[w.id] = getattr(w, 'value', 0)
        
        # Binding
        if w.binding:
            bindings_map[w.id] = w.binding.model_dump(by_alias=True)

        if getattr(w, "safety", None):
            guard_constraints_map[w.id] = {
                "min": getattr(w.safety, "min_value", None),
                "max": getattr(w.safety, "max_value", None),
                "unit": getattr(w.safety, "unit", "unitless"),
                "source_ref": getattr(w.safety, "source_ref", None),
            }
        else:
            w_min = getattr(w, "min", None)
            w_max = getattr(w, "max", None)
            if w_min is not None or w_max is not None:
                guard_constraints_map[w.id] = {
                    "min": w_min,
                    "max": w_max,
                    "unit": getattr(w, "unit", "unitless") or "unitless",
                    "source_ref": None,
                }
        
        # Render Body
        inner_html = ""
        if w.type == "slider":
            inner_html = _render_slider(w)
        elif w.type == "switch":
            inner_html = _render_switch(w)
        elif w.type == "gauge":
            inner_html = _render_gauge(w)
        elif w.type == "input":
            inner_html = _render_input(w)
        elif w.type == "select":
            inner_html = _render_select(w)
        elif w.type == "radio":
            inner_html = _render_radio(w)
        elif w.type == "plot":
            inner_html = _render_plot(w)
        else:
            # SECURITY: Escape unknown type string
            inner_html = f"<div class='text-red-500'>Unknown widget type: {html.escape(str(w.type))}</div>"
            
        # Wrap in Layout Card
        style = _get_layout_style(w.id, panel.layout)
        
        binding_badge = ""
        if w.binding:
            # SECURITY: Escape protocol/address in badge
            safe_protocol = html.escape(str(w.binding.protocol))
            endpoint = getattr(w.binding, "topic", None)
            if endpoint is None:
                endpoint = getattr(w.binding, "modbus_register", None)
            if endpoint is None:
                endpoint = getattr(w.binding, "address", "")
            safe_address = html.escape(str(endpoint))
            binding_badge = f"""
            <div class="absolute top-1 right-1 px-1.5 py-0.5 bg-black/50 rounded text-[9px] text-slate-400 font-mono border border-slate-700" 
                 title="Protocol: {safe_protocol}&#10;Address: {safe_address}">
                {safe_protocol.upper()}:{safe_address}
            </div>
            """
            
        card_html = f"""
        <div class="widget-card group" style="{style}" x-data>
            {binding_badge}
            {inner_html}
        </div>
        """
        widgets_html_parts.append(card_html)

    # 3. Calculate Deterministic Hash of the Input DSL
    # We dump the model to JSON, sort keys, and hash it
    dsl_json = panel.model_dump_json()
    dsl_hash = hashlib.sha256(dsl_json.encode('utf-8')).hexdigest()[:8]

    # 4. Assemble Final HTML
    # Use industrial blue/dark theme logic based on panel.theme
    bg_color = "#1e293b" if panel.theme == "dark" else "#f1f5f9"
    body_class = "bg-slate-900" if panel.theme == "dark" else "bg-slate-100"
    card_bg = "#334155" if panel.theme == "dark" else "#ffffff"
    card_border = "#475569" if panel.theme == "dark" else "#cbd5e1"
    
    # SECURITY: Escape title and description
    safe_title = html.escape(panel.title)
    safe_description = html.escape(panel.description or "")
    runtime_config = {}
    if isinstance(panel.metadata, dict):
        rc = panel.metadata.get("runtime")
        if isinstance(rc, dict):
            runtime_config = rc
    
    return HTML_SKELETON.format(
        title=safe_title,
        description=safe_description,
        version=html.escape(panel.version),
        dsl_hash=dsl_hash,
        bg_color=card_bg,
        border_color=card_border,
        body_class=body_class,
        widgets_html="\n".join(widgets_html_parts),
        initial_values_json=json.dumps(initial_values),
        bindings_json=json.dumps(bindings_map),
        guard_constraints_json=json.dumps(guard_constraints_map),
        runtime_config_json=json.dumps(runtime_config)
    )
