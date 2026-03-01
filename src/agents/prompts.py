"""
Prompt Management Module for GenerativeUI Project
Modularized prompts based on the paper's methodology
"""

from src.modules.renderer.styles import get_style_prompt
from src.modules.renderer.templates import get_templates_by_tags, render_templates_guide

def get_planner_prompt(user_prompt, file_context_preview):
    """Generate the planning phase prompt with search query optimization"""
    return f"""
You are an AI Planner for a Generative UI system. Analyze the user request and file context.
Identify what MISSING information or ASSETS are needed to build a rich, interactive HTML page.

**User Request**: "{user_prompt}"

**File Context Preview**: {file_context_preview}

**Available Tools**:
1. **web_searches**: For facts, news, data, current information
2. **images_to_search_real**: For REAL people, places, events (e.g., "Elon Musk", "Tokyo Tower")
3. **images_to_generate_creative**: For ABSTRACT concepts, backgrounds, fictional characters, artistic elements
4. **videos_to_search**: For YouTube video content (e.g., "SpaceX Launch", "Python tutorial")
5. **audio_to_search**: For background music or sound effects (use sparingly, only when appropriate)

**Your Task**:
Analyze the request and determine which tools are needed.

You MUST craft HIGH-PRECISION search queries for `web_searches` (and for any real-world asset searches).
These queries will be sent directly to a search engine. Your queries MUST be short keyword strings, NOT sentences.

### Search Query Format (STRICT)
For each query string:
- Length: **6–12 words max** (hard limit). Aim for **8–10**.
- Style: **entity terms + constraint terms + optional operators**.
- Avoid natural language questions and full sentences.
- Prefer search operators when useful:
  - `site:example.com`
  - `filetype:pdf|ppt|pptx|csv|xlsx|json`
  - `intitle:keyword`
  - `inurl:keyword`
- Minimal punctuation: avoid `? , ; !` and long quoted phrases.

### Search Query Self-Check (MANDATORY)
Before you output JSON, validate each query:
1) If a query looks like a sentence (contains "how", "what", "please", "can you", or ends with `?`) → REWRITE as keywords.
2) If a query has **> 12 space-separated tokens** → REWRITE shorter.
3) If a query contains excessive punctuation (`? , ; !`) → REWRITE.
4) If you need a domain/source, use `site:` instead of describing it in words.

### Focus Keywords for Local RAG (IMPORTANT)
If the user provided a large text file (PDF/TXT), we need to retrieve ONLY the most relevant chunks.
You MUST provide `focus_keywords`:
- Provide **6–12** items (strings). Use short terms or short noun phrases (1–4 words).
- Extract highly specific terms from the User Request (e.g., "starship", "payload", "launch date").
- If the request is broad, include general topic keywords.

### Good Examples
- `site:who.int vitamin D deficiency symptoms guidance`
- `filetype:pdf intitle:annual report spacex starship 2024`
- `tokyo tower height facts history`
- `site:github.com alpinejs toast component`

Return ONLY a valid JSON object:

{{
    "focus_keywords": ["keyword1", "keyword2"],
    "web_searches": ["query1", "query2"],
    "images_to_search_real": ["query1"],
    "images_to_generate_creative": ["prompt1", "prompt2"],
    "videos_to_search": ["query1"],
    "audio_to_search": ["query1"]
}}

**Rules**:
- Return ONLY the JSON object, no markdown code blocks
- Only include keys where tools are actually needed
- Be strategic: don't request unnecessary assets
- For educational/info requests: prioritize web_searches
- For visual/creative requests: prioritize images
- Audio should only be requested if explicitly mentioned or highly relevant (music apps, games, etc.)

**JSON Output Rules (STRICT)**:
- Do NOT include any explanations, comments, or trailing text.
- Use the exact keys shown above (when needed):
  `focus_keywords`, `template_tags`, `web_searches`, `images_to_search_real`, `images_to_generate_creative`, `videos_to_search`, `audio_to_search`.
- Values must be arrays of strings.

Return your response now:
"""


def get_architect_prompt(collected_context, file_context, user_prompt, style_name="Dark Mode", template_tags=None):
    """Generate the architecture/building phase prompt"""
    
    style_guide = get_style_prompt(style_name)
    templates = get_templates_by_tags(template_tags)
    template_guide = render_templates_guide(templates)
    
    return f"""
You are an expert Full-Stack Prototype Architect building a **Generative UI** system.
Your goal is to create a **highly interactive, resilient, single-file HTML application**.

{style_guide}

### 🎯 INTERACTION & PLAYABILITY (HIGHEST PRIORITY)

**Engine**: You MUST use **Alpine.js** for ALL state management and interactivity.
- Use `x-data` for component state
- Use `x-on:` or `@click` for event handlers
- Use `x-show`, `x-if`, `x-for` for conditional rendering
- Use `x-transition` for smooth animations

**Transform Content into Experiences**:
- Topic/educational content → **Interactive Quiz** or **Flashcards**
- Data/statistics → **Dashboard** with filters and charts
- Story/narrative → **Timeline** or **Visual novel**
- Comparison → **Tabs** or **Card flips**
- Gallery → **Carousel** with navigation

{template_guide}

### 🚨 ERROR HANDLING & OBSERVABILITY (MANDATORY)

You MUST implement a robust global error handling system. The user must NEVER see a white screen or silent failure.

1. **Global Error Reporter**:
   - Inject this script immediately after `<head>`:
     ```html
     <script>
       window.__errors = [];
       window.reportError = function(err, context = 'global') {{
         console.error(`[${{context}}]`, err);
         window.__errors.push({{ msg: err.message || String(err), ctx: context, time: new Date().toISOString() }});
         // Try to push to Alpine store if available
         if (window.Alpine && window.Alpine.store('err')) {{
             window.Alpine.store('err').push(err.message, context);
         }}
       }};
       window.onerror = function(msg, url, line) {{ reportError(msg, `global: ${{line}}`); }};
       window.onunhandledrejection = function(e) {{ reportError(e.reason, 'promise'); }};
     </script>
     ```

2. **Alpine.js Error Store & UI**:
   - Initialize a global error store in `document.addEventListener('alpine:init', ...)`:
     ```javascript
     Alpine.store('err', {{
        list: [],
        push(msg, ctx) {{ this.list.push({{msg, ctx, id: Date.now()}}); setTimeout(()=>this.remove(this.list.length-1), 5000); }},
        remove(idx) {{ if(this.list[idx]) this.list.splice(idx, 1); }}
     }});
     ```
   - **MANDATORY**: Include a visual "Error Toast" container fixed at the bottom/top of the screen that iterates over `list` ($store.err.list) and displays errors. It must be visible if errors exist.

3. **Component Safety**:
   - All complex logic (API calls, data processing) inside `x-init` or handler functions MUST be wrapped in **try/catch**.
   - In the `catch` block, YOU MUST CALL `reportError(e, 'component_name')`.
   - **Example**:
     ```javascript
     init() {{
       try {{
         this.data = JSON.parse(this.rawData);
       }} catch(e) {{
         reportError(e, 'Dashboard.init');
         this.errorState = true; 
       }}
     }}
     ```

### 🎨 TECH STACK (MANDATORY)

**Required Libraries** (include in `<head>`):
```html
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
```

**Optional but Recommended**:
- Chart.js for data visualization: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`
- Tone.js for audio (only if needed): `<script src="https://cdnjs.cloudflare.com/ajax/libs/tone/14.8.49/Tone.js"></script>`

### 📥 INPUT CONTEXT

**User Request**: {user_prompt}

**Gathered Intelligence**:
{collected_context}

**User File Data**:
{file_context[:15000] if file_context else "No file data provided."}

### 🎯 OUTPUT REQUIREMENTS

Generate the complete HTML page following these rules:

1. **Start with `<!DOCTYPE html>`**
2. **Sophisticated Design**: Modern, visually stunning, fully responsive
3. **No Placeholders**: All content must be real and functional
4. **Interactive First**: Prioritize user engagement over static text
5. **Error Handling**: Wrap complex JS in try-catch blocks
6. **Self-Contained**: No external file dependencies (except CDN libraries)

**CRITICAL OUTPUT RULE**:
- Output **ONLY** the raw HTML code.
- Do **NOT** use markdown code blocks (no ```html ... ```).
- Do **NOT** include any preamble or explanation.
- Start directly with `<!DOCTYPE html>`.

Generate the complete, interactive HTML page now:
"""

def get_review_prompt_v1():
    """First round of review - Basic checks"""
    return """
**Self-Review Round 1: Basic Quality Checks**

Review your generated code and fix any issues:

1. **Image Sources**: 
   - Check ALL `<img src="...">` attributes
   - Remove any placeholder paths like "path/to/", "your-image", "example.jpg"
   - Ensure all images use actual URLs from [GATHERED INTELLIGENCE] or valid icon/gradient alternatives

2. **Alpine.js Setup**:
   - Verify `x-data` is properly initialized
   - Check all `x-` directives have correct syntax
   - Ensure Alpine.js CDN is included in `<head>`

3. **Data Integration**:
   - Confirm you used the provided User File Data (if any)
   - Verify search results are incorporated into content

4. **CDN Links**:
   - Ensure all `<script>` and `<link>` tags use valid CDN URLs
   - Check Font Awesome, Tailwind, Alpine.js are all included

Output the **FIXED HTML** code. Ensure it starts with `<!DOCTYPE html>`.
"""

def get_review_prompt_v2():
    """Second round of review - Interactivity & Accessibility"""
    return """
**Self-Review Round 2: Interactivity & Accessibility**

Further refine your code:

1. **Interactivity Check**:
   - Does the page have meaningful interactions? (clicks, filters, animations)
   - Are there at least 2-3 interactive elements?
   - Test Alpine.js logic: would it work if executed?

2. **Accessibility**:
   - Do all images have `alt` attributes?
   - Are there proper heading hierarchies (h1, h2, h3)?
   - Is color contrast sufficient for readability?

3. **Responsive Design**:
   - Check mobile responsiveness (Tailwind sm:, md:, lg: classes)
   - Ensure text is readable on small screens
   - Verify images scale properly

4. **Visual Polish**:
   - Add smooth transitions where appropriate
   - Ensure consistent spacing and alignment
   - Check that the design follows the specified style guide

Output the **POLISHED HTML** code.
"""

def get_review_prompt_v3():
    """Third round of review - Performance & Final touches"""
    return """
**Self-Review Round 3: Final Polish**

Final optimization pass:

1. **Performance**:
   - Remove any duplicate code or unused CSS classes
   - Ensure images use appropriate sizes (not oversized)
   - Check for any infinite loops in Alpine.js logic

2. **User Experience**:
   - Is the page immediately understandable?
   - Are interactive elements obvious (hover states, cursor pointers)?
   - Does the page load and display correctly?

3. **Code Quality**:
   - Clean up any console.log statements
   - Ensure JavaScript error handling is in place
   - Verify all URLs are properly formatted

4. **Content Quality**:
   - Check for any Lorem Ipsum or dummy text
   - Ensure all content is relevant and accurate
   - Verify data visualizations (if any) use real data

Output the **FINAL, PRODUCTION-READY HTML** code.
"""

def get_simple_review_prompt(lint_report: str = ""):
    """Single-round comprehensive review (for faster generation)"""
    
    lint_section = ""
    if lint_report:
        lint_section = f"""
### 🕵️ Lint Findings (RULE-BASED) - Fix These Issues:
{lint_report}
"""

    return f"""
**Self-Review: Quality Check**

{lint_section}

Review and fix your code:

1. **Resources**: All `src` attributes must be valid URLs or use fallbacks (icons/gradients). No placeholders.
2. **Interactivity**: Alpine.js `x-data` correctly initialized, all directives working.
3. **Data**: User file data and search results properly integrated.
4. **Accessibility**: Alt tags on images, proper heading hierarchy.
5. **Responsiveness**: Works on mobile (Tailwind responsive classes).

Output the **FIXED and OPTIMIZED** raw HTML code. Must start with `<!DOCTYPE html>`.
"""


# ==========================================
# Phase 1: DSL Mode Prompts
# ==========================================

def get_architect_dsl_prompt(user_request: str, file_context: str = "", style: str = "modern") -> str:
    """
    Phase 1 Prompt: Forces strict JSON output compliant with HMIPanel schema.
    """
    
    # Inline simplified schema definition to save context tokens while maintaining strictness
    schema_hint = """
    OUTPUT FORMAT: JSON ONLY.
    Schema Structure:
    {
      "version": "0.1",
      "title": "string",
      "description": "string (optional)",
      "theme": "dark" | "light",
      "widgets": [
        {
          "type": "slider" | "switch" | "gauge",
          "id": "unique_id",
          "label": "string",
          "min": float, "max": float, "step": float, "value": float, // for slider/gauge
          "on_label": "str", "off_label": "str", "color_on": "str", // for switch
          "binding": { "protocol": "mqtt" | "modbus" | "mock", "address": "str", "access_mode": "r" | "w" | "rw" },
          "safety": { "max_value": float, "min_value": float, "unit": "str" }
        }
      ],
      "layout": [
        { "i": "widget_id", "x": int, "y": int, "w": int, "h": int }
      ]
    }
    """
    
    prompt = f"""
ROLE: You are an Industrial HMI Architect.
TASK: Convert the user request into a strictly structured JSON definition for an HMI panel.

USER REQUEST: "{user_request}"
CONTEXT: {file_context[:500] if file_context else "No additional context."}
STYLE PREFERENCE: {style}

{schema_hint}

RULES:
1. Output pure JSON. No Markdown. No comments. No explanation.
2. Ensure every widget has a corresponding entry in the 'layout' list with matching 'i' field.
3. Layout: 12-column grid system. x, y are grid coordinates; w, h are spans.
4. Safety: Infer reasonable physical limits (e.g., Voltage max 24V if not specified).
5. Widget IDs must be unique lowercase with underscores (e.g., "motor_speed", "temp_gauge").
6. Include at least one widget relevant to the user's request.

WIDGET TYPES:
- slider: For adjustable numeric values (requires min, max, step, value)
- switch: For on/off toggles (requires on_label, off_label, value as boolean)
- gauge: For read-only displays (requires min, max, value, thresholds optional)

EXAMPLE OUTPUT:
{{
  "version": "0.1",
  "title": "Motor Control Panel",
  "description": "Speed and power control interface",
  "theme": "dark",
  "widgets": [
    {{ "type": "switch", "id": "power_sw", "label": "Power", "on_label": "ON", "off_label": "OFF", "value": false, "binding": {{ "protocol": "mqtt", "address": "motor/pwr" }} }},
    {{ "type": "slider", "id": "speed_val", "label": "Speed", "min": 0, "max": 100, "step": 1, "value": 50, "binding": {{ "protocol": "mqtt", "address": "motor/speed" }}, "safety": {{ "max_value": 90, "unit": "%" }} }}
  ],
  "layout": [
    {{ "i": "power_sw", "x": 0, "y": 0, "w": 4, "h": 2 }},
    {{ "i": "speed_val", "x": 4, "y": 0, "w": 6, "h": 2 }}
  ]
}}

OUTPUT YOUR JSON NOW:
"""
    return prompt


def get_json_repair_prompt(bad_json: str, error_msg: str) -> str:
    """
    Prompt for repairing invalid JSON after a validation failure.
    """
    return f"""
SYSTEM: Your previous JSON output caused a validation error.

ERROR MESSAGE:
{error_msg}

BAD JSON SNIPPET:
{bad_json[:1500]}

TASK: Fix the JSON to comply with the HMIPanel schema.
- Ensure all widget IDs appear in both 'widgets' and 'layout' lists.
- Match 'i' in layout to 'id' in widgets.
- Use correct field names and types.

Output ONLY the corrected JSON. Do not explain. Start with {{ and end with }}.
"""
