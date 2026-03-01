"""
UI Style Templates for GenUI Agent
Provides various pre-defined UI styles based on the paper's methodology
"""

STYLES = {
    "Cyberpunk": {
        "name": "Cyberpunk",
        "description": "Dark neon aesthetic with vibrant accents",
        "prompt_guide": """
**Style Guide: Cyberpunk**
- **Colors**: Dark background (#0a0e27, #0f172a), Neon accents (#00f0ff, #ff006e, #8b5cf6)
- **Fonts**: Orbitron or Rajdhani from Google Fonts
- **Effects**: Glowing text-shadows, neon borders, scan-line animations
- **Layout**: Grid-based with sharp edges, hexagonal elements
- **Typography**: Bold headings, uppercase labels, monospace for data
""",
        "colors": {
            "primary": "#00f0ff",
            "secondary": "#ff006e",
            "accent": "#8b5cf6",
            "background": "#0a0e27",
            "surface": "#0f172a",
            "text": "#e2e8f0"
        }
    },
    
    "Classic": {
        "name": "Classic",
        "description": "Clean and professional design",
        "prompt_guide": """
**Style Guide: Classic**
- **Colors**: White backgrounds (#ffffff), Navy blues (#1e3a8a, #3b82f6), Grays (#64748b)
- **Fonts**: Lora or Merriweather for headings, Open Sans for body
- **Effects**: Subtle shadows, smooth transitions, elegant hover states
- **Layout**: Traditional grid, prominent headers, clear hierarchy
- **Typography**: Serif headings, sans-serif body, proper line-height
""",
        "colors": {
            "primary": "#3b82f6",
            "secondary": "#1e3a8a",
            "accent": "#0ea5e9",
            "background": "#ffffff",
            "surface": "#f8fafc",
            "text": "#1e293b"
        }
    },
    
    "Minimalist": {
        "name": "Minimalist",
        "description": "Clean, spacious, and focused",
        "prompt_guide": """
**Style Guide: Minimalist**
- **Colors**: Pure whites (#ffffff), Soft grays (#f1f5f9, #cbd5e1), Single accent (#0ea5e9)
- **Fonts**: Inter or Helvetica Neue
- **Effects**: Minimal shadows, ample whitespace, clean lines
- **Layout**: Generous spacing, asymmetric layouts, focus on content
- **Typography**: Light font weights, generous letter-spacing
""",
        "colors": {
            "primary": "#0ea5e9",
            "secondary": "#64748b",
            "accent": "#06b6d4",
            "background": "#ffffff",
            "surface": "#f8fafc",
            "text": "#334155"
        }
    },
    
    "Wizard Green": {
        "name": "Wizard Green",
        "description": "Magical emerald theme with mystical elements",
        "prompt_guide": """
**Style Guide: Wizard Green**
- **Colors**: Deep forest (#064e3b, #065f46), Emerald greens (#10b981, #34d399), Gold accents (#fbbf24)
- **Fonts**: Cinzel or Philosopher for headings, Lato for body
- **Effects**: Soft glows on green elements, particle effects, gradient backgrounds
- **Layout**: Organic shapes, flowing elements, mystical borders
- **Typography**: Decorative headings, magical icons (stars, crystals)
""",
        "colors": {
            "primary": "#10b981",
            "secondary": "#34d399",
            "accent": "#fbbf24",
            "background": "#064e3b",
            "surface": "#065f46",
            "text": "#ecfdf5"
        }
    },
    
    "Neon": {
        "name": "Neon",
        "description": "Vibrant '80s inspired design",
        "prompt_guide": """
**Style Guide: Neon**
- **Colors**: Black background (#000000), Hot pink (#ff006e), Electric blue (#00f0ff), Lime (#ccff00)
- **Fonts**: Teko or Audiowide from Google Fonts
- **Effects**: Heavy neon glows, retro grid patterns, animated gradients
- **Layout**: Bold geometric shapes, diagonal elements, grid backgrounds
- **Typography**: All caps, wide tracking, glowing effects
""",
        "colors": {
            "primary": "#ff006e",
            "secondary": "#00f0ff",
            "accent": "#ccff00",
            "background": "#000000",
            "surface": "#1a0a1f",
            "text": "#ffffff"
        }
    },
    
    "Dark Mode": {
        "name": "Dark Mode",
        "description": "Modern dark interface",
        "prompt_guide": """
**Style Guide: Dark Mode**
- **Colors**: Dark backgrounds (#0f172a, #1e293b), Blue accents (#3b82f6, #60a5fa)
- **Fonts**: Inter or Roboto
- **Effects**: Subtle shadows, smooth transitions, elevated cards
- **Layout**: Modern card-based layout, clear sections
- **Typography**: Medium font weights, clear hierarchy
""",
        "colors": {
            "primary": "#3b82f6",
            "secondary": "#60a5fa",
            "accent": "#8b5cf6",
            "background": "#0f172a",
            "surface": "#1e293b",
            "text": "#e2e8f0"
        }
    },
    
    "Light Mode": {
        "name": "Light Mode",
        "description": "Clean light interface",
        "prompt_guide": """
**Style Guide: Light Mode**
- **Colors**: Light backgrounds (#ffffff, #f8fafc), Blue accents (#2563eb, #3b82f6)
- **Fonts**: Inter or System UI
- **Effects**: Soft shadows, clean borders, smooth animations
- **Layout**: Airy card layouts, generous padding
- **Typography**: Regular weights, optimal readability
""",
        "colors": {
            "primary": "#2563eb",
            "secondary": "#3b82f6",
            "accent": "#8b5cf6",
            "background": "#ffffff",
            "surface": "#f8fafc",
            "text": "#1e293b"
        }
    }
}

def get_style_prompt(style_name):
    """Get the prompt guide for a specific style"""
    return STYLES.get(style_name, STYLES["Dark Mode"])["prompt_guide"]

def get_style_names():
    """Get list of all available style names"""
    return list(STYLES.keys())

def get_style_description(style_name):
    """Get description of a style"""
    return STYLES.get(style_name, STYLES["Dark Mode"])["description"]
