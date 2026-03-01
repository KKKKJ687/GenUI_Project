"""
Interactive Component Templates for GenerativeUI Project (Phase 3.2).
Provides code examples to guide LLM in generating interactive components.
Now supports dynamic injection based on tags.
"""

from dataclasses import dataclass
from typing import List, Sequence, Dict

@dataclass(frozen=True)
class Template:
    name: str
    tags: List[str]
    description: str
    snippet: str

# Base templates that are always good to have or very common
_BASE_TEMPLATE = Template(
    name="base_page",
    tags=["default"],
    description="**Base Layout**: Standard responsive layout structure",
    snippet="" # Base layout is usually implied by instructions, but we can keep minimal or empty if specific snippet needed.
               # For now, we focus on interactive components.
)

_TOAST_TEMPLATE = Template(
    name="toast",
    tags=["default", "feedback", "error"],
    description="**Toast Notification**: Non-blocking feedback for user actions",
    snippet="""
<!-- TOAST NOTIFICATION -->
<div x-data="{ show: false, message: '', type: 'info' }"
     @notify.window="show = true; message = $event.detail.message; type = $event.detail.type || 'info'; setTimeout(() => show = false, 3000)"
     x-show="show" x-transition
     class="fixed bottom-4 right-4 px-6 py-3 rounded shadow-lg text-white"
     :class="{
        'bg-blue-500': type === 'info',
        'bg-green-500': type === 'success',
        'bg-red-500': type === 'error'
     }">
    <span x-text="message"></span>
</div>
"""
)

# Component definitions with tags
_TEMPLATES_DATA = [
    Template(
        name="quiz",
        tags=["quiz", "test", "education", "flashcard"],
        description="**Quiz/Test**: For educational content or knowledge checking",
        snippet="""
<!-- QUIZ TEMPLATE -->
<div x-data="{
    currentQ: 0,
    score: 0,
    showResult: false,
    questions: [
        {q: 'Question?', options: ['A', 'B'], correct: 0},
    ],
    selectAnswer(idx) {
        if (idx === this.questions[this.currentQ].correct) this.score++;
        if (this.currentQ < this.questions.length - 1) this.currentQ++;
        else this.showResult = true;
    }
}" class="max-w-2xl mx-auto p-6 bg-gray-800 rounded-lg">
    <div x-show="!showResult">
        <h2 class="text-xl font-bold mb-4" x-text="'Question ' + (currentQ + 1)"></h2>
        <p class="mb-6" x-text="questions[currentQ].q"></p>
        <template x-for="(opt, idx) in questions[currentQ].options" :key="idx">
            <button @click="selectAnswer(idx)" 
                    class="block w-full p-4 mb-2 bg-gray-700 hover:bg-gray-600 rounded text-left">
                <span x-text="opt"></span>
            </button>
        </template>
    </div>
    <div x-show="showResult" class="text-center">
        <h2 class="text-2xl font-bold">Complete!</h2>
        <p class="text-xl mt-2" x-text="'Score: ' + score + '/' + questions.length"></p>
    </div>
</div>
"""
    ),
    Template(
        name="dashboard",
        tags=["dashboard", "chart", "data", "stats", "analytics"],
        description="**Dashboard**: Data visualization with filters",
        snippet="""
<!-- DASHBOARD TEMPLATE -->
<div x-data="{
    filter: 'all',
    items: [ {name: 'A', cat: 'x', val: 10} ],
    get visible() { return this.filter === 'all' ? this.items : this.items.filter(i => i.cat === this.filter) }
}" class="p-6">
    <div class="mb-6 space-x-2">
        <button @click="filter = 'all'" :class="{'bg-blue-600': filter==='all', 'bg-gray-700': filter!=='all'}" class="px-4 py-2 rounded">All</button>
        <!-- Add more filter buttons -->
    </div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <template x-for="item in visible" :key="item.name">
            <div class="bg-gray-800 p-4 rounded shadow">
                <h3 x-text="item.name" class="font-bold"></h3>
                <p x-text="item.val" class="text-2xl mt-2"></p>
            </div>
        </template>
    </div>
</div>
"""
    ),
    Template(
        name="chartjs_boilerplate",
        tags=["chart", "graph", "plot", "dashboard"],
        description="**Chart.js Setup**: Basic canvas and init logic",
        snippet="""
<!-- CHART.JS BOILERPLATE -->
<div class="bg-gray-800 p-4 rounded-lg">
    <canvas id="myChart"></canvas>
</div>
<script>
  // Ensure Chart.js is loaded
  const ctx = document.getElementById('myChart');
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Red', 'Blue', 'Yellow'],
      datasets: [{ label: '# of Votes', data: [12, 19, 3], borderWidth: 1 }]
    },
    options: { scales: { y: { beginAtZero: true } } }
  });
</script>
"""
    ),
    Template(
        name="timeline",
        tags=["timeline", "history", "chronology", "story"],
        description="**Timeline**: Chronological event list",
        snippet="""
<!-- TIMELINE TEMPLATE -->
<div class="space-y-8 relative before:absolute before:inset-0 before:ml-5 before:-translate-x-px md:before:mx-auto md:before:translate-x-0 before:h-full before:w-0.5 before:bg-gradient-to-b before:from-transparent before:via-slate-300 before:to-transparent">
    <div class="relative flex items-center justify-between md:justify-normal md:odd:flex-row-reverse group is-active">
        <div class="flex items-center justify-center w-10 h-10 rounded-full border border-white bg-slate-300 group-[.is-active]:bg-emerald-500 text-slate-500 group-[.is-active]:text-emerald-50 shadow shrink-0 md:order-1 md:group-odd:-translate-x-1/2 md:group-even:translate-x-1/2">
            <i class="fas fa-check"></i>
        </div>
        <div class="w-[calc(100%-4rem)] md:w-[calc(50%-2.5rem)] p-4 rounded border border-slate-200 shadow">
            <h3 class="font-bold">Event Title</h3>
            <time class="block mb-2 text-sm font-normal leading-none text-gray-400">Time</time>
            <p>Description...</p>
        </div>
    </div>
</div>
"""
    ),
    Template(
        name="card_flip",
        tags=["card", "flip", "flashcard", "reveal"],
        description="**Flip Card**: Reveal content on click",
        snippet="""
<!-- FLIP CARD TEMPLATE -->
<div x-data="{ flipped: false }" class="group w-64 h-64 [perspective:1000px]">
    <div @click="flipped = !flipped" :class="flipped ? '[transform:rotateY(180deg)]' : ''" class="relative w-full h-full transition-all duration-500 [transform-style:preserve-3d] cursor-pointer">
        <!-- Front -->
        <div class="absolute inset-0 bg-blue-600 rounded-xl p-6 flex items-center justify-center [backface-visibility:hidden]">
            <h3 class="text-xl font-bold">Front</h3>
        </div>
        <!-- Back -->
        <div class="absolute inset-0 bg-purple-600 rounded-xl p-6 flex items-center justify-center [transform:rotateY(180deg)] [backface-visibility:hidden]">
            <p>Back Content</p>
        </div>
    </div>
</div>
"""
    ),
    Template(
        name="tabs",
        tags=["tabs", "navigation", "sections", "menu"],
        description="**Tabs**: Switchable content sections",
        snippet="""
<!-- TABS TEMPLATE -->
<div x-data="{ tab: '1' }">
    <div class="flex border-b border-gray-700">
        <button @click="tab = '1'" :class="{'border-blue-500 text-blue-500': tab==='1'}" class="px-4 py-2 border-b-2 border-transparent">Tab 1</button>
        <button @click="tab = '2'" :class="{'border-blue-500 text-blue-500': tab==='2'}" class="px-4 py-2 border-b-2 border-transparent">Tab 2</button>
    </div>
    <div class="p-4">
        <div x-show="tab === '1'" x-transition>Content 1</div>
        <div x-show="tab === '2'" x-transition>Content 2</div>
    </div>
</div>
"""
    ),
    Template(
        name="modal",
        tags=["modal", "popup", "dialog", "overlay"],
        description="**Modal**: Pop-up dialog window",
        snippet="""
<!-- MODAL TEMPLATE -->
<div x-data="{ open: false }">
    <button @click="open = true" class="px-4 py-2 bg-blue-600 rounded">Open</button>
    <div x-show="open" class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50" x-transition>
        <div @click.away="open = false" class="bg-gray-800 p-6 rounded-lg max-w-sm w-full">
            <h3 class="text-xl font-bold mb-4">Title</h3>
            <p class="mb-4">Content...</p>
            <button @click="open = false" class="text-gray-400 hover:text-white">Close</button>
        </div>
    </div>
</div>
"""
    ),
    Template(
        name="carousel",
        tags=["carousel", "slider", "gallery", "images"],
        description="**Carousel**: Image or content slider",
        snippet="""
<!-- CAROUSEL TEMPLATE -->
<div x-data="{ curr: 0, items: ['A', 'B', 'C'] }" class="relative w-full max-w-lg mx-auto overflow-hidden rounded-lg">
    <div class="flex transition-transform duration-500" :style="'transform: translateX(-' + (curr * 100) + '%)'">
        <template x-for="item in items">
            <div class="w-full flex-shrink-0 h-64 bg-gray-700 flex items-center justify-center text-3xl" x-text="item"></div>
        </template>
    </div>
    <button @click="curr = (curr - 1 + items.length) % items.length" class="absolute left-2 top-1/2 -translate-y-1/2 p-2 bg-black/50 rounded-full">&lt;</button>
    <button @click="curr = (curr + 1) % items.length" class="absolute right-2 top-1/2 -translate-y-1/2 p-2 bg-black/50 rounded-full">&gt;</button>
</div>
"""
    ),
    Template(
        name="form",
        tags=["form", "input", "contact", "signup", "login"],
        description="**Form**: Input fields with state",
        snippet="""
<!-- FORM TEMPLATE -->
<form x-data="{ name: '', email: '', loading: false }" @submit.prevent="loading=true; setTimeout(()=>$dispatch('notify', {message:'Sent!'}), 1000)" class="space-y-4 max-w-md">
    <div>
        <label class="block text-sm font-medium mb-1">Name</label>
        <input x-model="name" type="text" class="w-full p-2 rounded bg-gray-700 border-gray-600 text-white" required>
    </div>
    <button type="submit" :disabled="loading" class="w-full py-2 bg-blue-600 rounded disabled:opacity-50">
        <span x-show="!loading">Submit</span>
        <span x-show="loading">Sending...</span>
    </button>
</form>
"""
    ),
]

_TEMPLATES_MAP = {t.name: t for t in _TEMPLATES_DATA}

def get_templates_by_tags(tags: Sequence[str] | None, *, max_templates: int = 4) -> List[Template]:
    """
    Retrieve templates matching the given tags.
    If tags is empty/None, returns a minimal default set (toast only).
    Always returns unique templates.
    """
    
    # 1. Start with always-included templates (like toast)
    selected = [] # We'll append manually to control order, then uniqify
    
    # 2. Process tags
    if tags:
        normalized_tags = set(str(t).lower().strip() for t in tags)
        
        # Simple scoring: earlier tags in list valid? No, just match any.
        # Let's iterate through all templates and check if they match any tag.
        # To prioritize valid relevance, we can search per tag.
        
        for t_tag in tags: # Use original order of request tags
            t_tag = t_tag.lower().strip()
            for tmpl in _TEMPLATES_DATA:
                if t_tag in tmpl.tags:
                    selected.append(tmpl)
    
    # 3. Add Base/Default if list is empty or explicitly requested
    if not selected:
        # Default fallback: just toast. Base page is implied by prompt instructions usually.
        # But if user asks for "default" tag, we can give something.
        pass

    # Always add Toast for feedback if not present
    if _TOAST_TEMPLATE not in selected:
        # Put toast at the end usually, but here order matters for prompt reading.
        # Maybe put at start?
        selected.append(_TOAST_TEMPLATE)
        
    # 4. Dedup and limit
    seen = set()
    final_list = []
    
    # Prioritize: Matches first, then Toast.
    for tmpl in selected:
        if tmpl.name not in seen:
            seen.add(tmpl.name)
            final_list.append(tmpl)
            if len(final_list) >= max_templates:
                break
                
    return final_list

def render_templates_guide(templates: Sequence[Template]) -> str:
    """Render the template guide string for the prompt"""
    if not templates:
        return ""
        
    guide = "### 🎯 INTERACTIVE COMPONENT LIBRARY (Use these patterns)\n\n"
    
    # 1. Descriptions
    guide += "**Available Patterns**:\n"
    for t in templates:
        if t.snippet: # Only list if it has a snippet or is useful
            guide += f"- {t.description}\n"
    
    # 2. Snippets
    guide += "\n**Code Snippets** (Adapt these to your needs):\n"
    for t in templates:
        if t.snippet:
            guide += f"\n{t.snippet.strip()}\n"
            
    return guide

# Deprecated but kept for compatibility if needed (but we updated prompts.py to not use it)
def get_all_templates_guide():
    """DEPRECATED: Returns small default set instead of full dump"""
    return render_templates_guide([_TOAST_TEMPLATE])
