import os
import requests
import time


class SearchTool:
    """
    Base Search Client with source-level fallback.
    Primary: SerpApi (SERPAPI_KEY)
    Secondary: Google Programmable Search Engine (GOOGLE_PSE_API_KEY + GOOGLE_PSE_CX)
    Returns structured envelope responses for machine-readable consumption.
    """
    SERPAPI_URL = "https://serpapi.com/search"
    GOOGLE_PSE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self):
        # Primary: SerpApi
        self.serpapi_key = os.environ.get("SERPAPI_KEY")
        
        # Secondary: Google PSE (supports multiple env var names)
        self.pse_key = os.environ.get("GOOGLE_PSE_API_KEY") or os.environ.get("GOOGLE_PSE_KEY")
        self.pse_cx = os.environ.get("GOOGLE_PSE_CX") or os.environ.get("GOOGLE_CSE_CX")
        
        if not self.serpapi_key:
            print("⚠️ WARNING: SERPAPI_KEY not found. Primary search will fail.")
        if not self.pse_key or not self.pse_cx:
            print("⚠️ WARNING: Google PSE credentials missing. Fallback search disabled.")

    def _classify_http_error(self, status_code):
        """
        Classify HTTP errors into recoverable vs non-recoverable.
        Returns: (category, recoverable)
        """
        if status_code >= 500:
            return ("server_error", True)  # 5xx - server issues, try fallback
        elif status_code == 429:
            return ("rate_limit", False)   # Rate limit - don't waste secondary quota
        elif status_code == 402:
            return ("payment_required", False)
        elif status_code in (401, 403):
            return ("auth_error", False)   # Auth issues - config problem
        else:
            return ("client_error", False) # Other 4xx

    def _make_serpapi_request(self, params, retries=2):
        """
        Make request to SerpApi with structured error handling.
        Returns envelope with recoverable flag for fallback decisions.
        """
        if not self.serpapi_key:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "provider": "serpapi",
                    "code": "MISSING_KEY",
                    "message": "SERPAPI_KEY not found",
                    "category": "config",
                    "recoverable": False,
                    "http_status": None
                }
            }

        params["api_key"] = self.serpapi_key
        
        for i in range(retries):
            try:
                response = requests.get(self.SERPAPI_URL, params=params, timeout=10)
                
                if response.status_code != 200:
                    category, recoverable = self._classify_http_error(response.status_code)
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "serpapi",
                            "code": f"HTTP_{response.status_code}",
                            "message": f"API returned status {response.status_code}",
                            "category": category,
                            "recoverable": recoverable,
                            "http_status": response.status_code
                        }
                    }
                
                try:
                    data = response.json()
                except ValueError as e:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "serpapi",
                            "code": "JSON_PARSE_ERROR",
                            "message": str(e),
                            "category": "parse_error",
                            "recoverable": True,
                            "http_status": 200
                        }
                    }
                
                if "error" in data:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "serpapi",
                            "code": "API_ERROR",
                            "message": data["error"],
                            "category": "api_error",
                            "recoverable": False,
                            "http_status": 200
                        }
                    }
                
                return {"ok": True, "data": data, "error": None}
                
            except requests.exceptions.Timeout as e:
                if i == retries - 1:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "serpapi",
                            "code": "TIMEOUT",
                            "message": str(e),
                            "category": "timeout",
                            "recoverable": True,
                            "http_status": None
                        }
                    }
                time.sleep(0.5)
            except requests.exceptions.RequestException as e:
                if i == retries - 1:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "serpapi",
                            "code": "REQUEST_ERROR",
                            "message": str(e),
                            "category": "connection",
                            "recoverable": True,
                            "http_status": None
                        }
                    }
                time.sleep(0.5)
        
        return {
            "ok": False,
            "data": None,
            "error": {
                "provider": "serpapi",
                "code": "UNKNOWN",
                "message": "All retries exhausted",
                "category": "unknown",
                "recoverable": True,
                "http_status": None
            }
        }

    def _make_pse_request(self, params, retries=2):
        """
        Make request to Google Programmable Search Engine.
        """
        if not self.pse_key or not self.pse_cx:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "provider": "google_pse",
                    "code": "MISSING_KEY",
                    "message": "GOOGLE_PSE_API_KEY or GOOGLE_PSE_CX not found",
                    "category": "config",
                    "recoverable": False,
                    "http_status": None
                }
            }

        params["key"] = self.pse_key
        params["cx"] = self.pse_cx
        
        for i in range(retries):
            try:
                response = requests.get(self.GOOGLE_PSE_URL, params=params, timeout=10)
                
                if response.status_code != 200:
                    category, recoverable = self._classify_http_error(response.status_code)
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "google_pse",
                            "code": f"HTTP_{response.status_code}",
                            "message": f"API returned status {response.status_code}",
                            "category": category,
                            "recoverable": recoverable,
                            "http_status": response.status_code
                        }
                    }
                
                try:
                    data = response.json()
                except ValueError as e:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "google_pse",
                            "code": "JSON_PARSE_ERROR",
                            "message": str(e),
                            "category": "parse_error",
                            "recoverable": True,
                            "http_status": 200
                        }
                    }
                
                if "error" in data:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "google_pse",
                            "code": "API_ERROR",
                            "message": data["error"].get("message", str(data["error"])),
                            "category": "api_error",
                            "recoverable": False,
                            "http_status": 200
                        }
                    }
                
                return {"ok": True, "data": data, "error": None}
                
            except requests.exceptions.Timeout as e:
                if i == retries - 1:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "google_pse",
                            "code": "TIMEOUT",
                            "message": str(e),
                            "category": "timeout",
                            "recoverable": True,
                            "http_status": None
                        }
                    }
                time.sleep(0.5)
            except requests.exceptions.RequestException as e:
                if i == retries - 1:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "provider": "google_pse",
                            "code": "REQUEST_ERROR",
                            "message": str(e),
                            "category": "connection",
                            "recoverable": True,
                            "http_status": None
                        }
                    }
                time.sleep(0.5)
        
        return {
            "ok": False,
            "data": None,
            "error": {
                "provider": "google_pse",
                "code": "UNKNOWN",
                "message": "All retries exhausted",
                "category": "unknown",
                "recoverable": True,
                "http_status": None
            }
        }

    def _web_search_serpapi(self, query, num=3):
        """
        SerpApi provider implementation.
        Returns structured search result.
        """
        params = {"engine": "google", "q": query, "num": num}
        envelope = self._make_serpapi_request(params)
        
        if not envelope["ok"]:
            error = envelope["error"].copy()
            error["did_fallback"] = False
            return {
                "ok": False,
                "provider": "serpapi",
                "query": query,
                "results": [],
                "error": error
            }
        
        data = envelope["data"]
        if "organic_results" not in data:
            return {
                "ok": False,
                "provider": "serpapi",
                "query": query,
                "results": [],
                "error": {
                    "provider": "serpapi",
                    "code": "MISSING_KEY",
                    "message": "Response missing 'organic_results'",
                    "category": "parse_error",
                    "recoverable": True,
                    "did_fallback": False
                }
            }
        
        results = []
        for r in data["organic_results"][:num]:
            results.append({
                "title": r.get("title", "No Title"),
                "snippet": r.get("snippet", "No summary available."),
                "url": r.get("link", "#")
            })
        
        return {
            "ok": True,
            "provider": "serpapi",
            "query": query,
            "results": results,
            "error": None
        }

    def _web_search_google_pse(self, query, num=3):
        """
        Google PSE provider implementation.
        Returns structured search result.
        """
        params = {"q": query, "num": num}
        envelope = self._make_pse_request(params)
        
        if not envelope["ok"]:
            error = envelope["error"].copy()
            error["did_fallback"] = False
            return {
                "ok": False,
                "provider": "google_pse",
                "query": query,
                "results": [],
                "error": error
            }
        
        data = envelope["data"]
        items = data.get("items", [])
        
        results = []
        for r in items[:num]:
            results.append({
                "title": r.get("title", "No Title"),
                "snippet": r.get("snippet", "No summary available."),
                "url": r.get("link", "#")
            })
        
        return {
            "ok": True,
            "provider": "google_pse",
            "query": query,
            "results": results,
            "error": None
        }

    def _retry_search(self, query, num=3):
        """
        Orchestrator for source-level fallback.
        Primary: SerpApi → Secondary: Google PSE (only if primary error is recoverable)
        """
        # Try primary (SerpApi)
        primary_result = self._web_search_serpapi(query, num)
        
        if primary_result["ok"]:
            return primary_result
        
        primary_error = primary_result["error"]
        
        # Check if error is recoverable (should try fallback)
        if not primary_error.get("recoverable", False):
            # Non-recoverable: auth/quota errors - don't waste secondary
            return primary_result
        
        # Try secondary (Google PSE)
        print(f"🔄 Primary search failed ({primary_error.get('code')}), trying fallback...")
        secondary_result = self._web_search_google_pse(query, num)
        
        if secondary_result["ok"]:
            # Fallback succeeded - preserve primary error info
            secondary_result["error"] = {
                "did_fallback": True,
                "primary_error": primary_error
            }
            return secondary_result
        
        # Both failed
        return {
            "ok": False,
            "provider": "none",
            "query": query,
            "results": [],
            "error": {
                "code": "SEARCH_UNAVAILABLE",
                "message": "All search providers failed",
                "did_fallback": True,
                "primary_error": primary_error,
                "secondary_error": secondary_result["error"]
            }
        }

    def web_search(self, query):
        """
        Performs a web search with source-level fallback.
        Returns structured dict:
        {
            "ok": bool,
            "provider": "serpapi" | "google_pse" | "none",
            "query": str,
            "results": [{"title": str, "snippet": str, "url": str}, ...],
            "error": dict | None
        }
        """
        return self._retry_search(query, num=3)

    def search_image_api(self, query, top_k=8):
        """
        Performs image search via official Bing Image Search API.
        Returns structured dict:
        {
            "ok": bool,
            "provider": "bing_images",
            "query": str,
            "results": [{"url": str, "title": str, "source_page": str}, ...],
            "error": dict | None
        }
        """
        # Parameter protection
        top_k = max(1, min(10, top_k))
        
        # Get Bing API credentials
        bing_key = os.environ.get("BING_IMAGE_SEARCH_KEY")
        bing_endpoint = os.environ.get(
            "BING_IMAGE_SEARCH_ENDPOINT",
            "https://api.bing.microsoft.com/v7.0/images/search"
        )
        
        if not bing_key:
            return {
                "ok": False,
                "provider": "bing_images",
                "query": query,
                "results": [],
                "error": {
                    "code": "MISSING_KEY",
                    "message": "BING_IMAGE_SEARCH_KEY not found in environment",
                    "category": "config"
                }
            }
        
        headers = {"Ocp-Apim-Subscription-Key": bing_key}
        params = {"q": query, "count": top_k, "safeSearch": "Moderate"}
        
        try:
            response = requests.get(bing_endpoint, headers=headers, params=params, timeout=10)
            
            if response.status_code != 200:
                return {
                    "ok": False,
                    "provider": "bing_images",
                    "query": query,
                    "results": [],
                    "error": {
                        "code": f"HTTP_{response.status_code}",
                        "message": f"Bing API returned status {response.status_code}",
                        "category": "http_error",
                        "http_status": response.status_code
                    }
                }
            
            try:
                data = response.json()
            except ValueError as e:
                return {
                    "ok": False,
                    "provider": "bing_images",
                    "query": query,
                    "results": [],
                    "error": {
                        "code": "JSON_PARSE_ERROR",
                        "message": str(e),
                        "category": "parse_error"
                    }
                }
            
            # Check for API-level error
            if "error" in data:
                return {
                    "ok": False,
                    "provider": "bing_images",
                    "query": query,
                    "results": [],
                    "error": {
                        "code": "API_ERROR",
                        "message": data["error"].get("message", str(data["error"])),
                        "category": "api_error"
                    }
                }
            
            # Parse results
            results = []
            for item in data.get("value", [])[:top_k]:
                results.append({
                    "url": item.get("contentUrl", ""),
                    "title": item.get("name", "No Title"),
                    "source_page": item.get("hostPageUrl", "")
                })
            
            return {
                "ok": True,
                "provider": "bing_images",
                "query": query,
                "results": results,
                "error": None
            }
            
        except requests.exceptions.Timeout as e:
            return {
                "ok": False,
                "provider": "bing_images",
                "query": query,
                "results": [],
                "error": {
                    "code": "TIMEOUT",
                    "message": str(e),
                    "category": "timeout"
                }
            }
        except requests.exceptions.RequestException as e:
            return {
                "ok": False,
                "provider": "bing_images",
                "query": query,
                "results": [],
                "error": {
                    "code": "REQUEST_ERROR",
                    "message": str(e),
                    "category": "connection"
                }
            }

    def search_image(self, query):
        """
        Performs image search. Returns a single direct image URL.
        Internally uses Bing Image Search API, falls back to placeholder on error.
        """
        # Try Bing API first
        bing_result = self.search_image_api(query, top_k=1)
        
        if bing_result["ok"] and bing_result["results"]:
            url = bing_result["results"][0].get("url", "")
            if url:
                return url
        
        # Fallback to SerpApi if Bing key not configured
        if not os.environ.get("BING_IMAGE_SEARCH_KEY") and self.serpapi_key:
            params = {"engine": "google_images", "q": query, "num": 1}
            envelope = self._make_serpapi_request(params)
            
            if envelope["ok"] and envelope["data"]:
                data = envelope["data"]
                if "images_results" in data and len(data["images_results"]) > 0:
                    return data["images_results"][0].get("original", "https://placehold.co/800x600?text=Image+Unavailable")
        
        return "https://placehold.co/800x600?text=Image+Unavailable"

    def search_video(self, query):
        """
        Performs a Google Video search via SerpApi.
        Returns: A YouTube embed URL if found.
        """
        if not self.serpapi_key:
            return None

        params = {"engine": "google_videos", "q": query, "num": 1}
        envelope = self._make_serpapi_request(params)
        
        if envelope["ok"] and envelope["data"]:
            data = envelope["data"]
            if "video_results" in data and len(data["video_results"]) > 0:
                link = data["video_results"][0].get("link", "")
                if "youtube.com/watch?v=" in link:
                    video_id = link.split("watch?v=")[1].split("&")[0]
                    return f"https://www.youtube.com/embed/{video_id}"
                elif "youtu.be/" in link:
                    video_id = link.split("youtu.be/")[1].split("?")[0]
                    return f"https://www.youtube.com/embed/{video_id}"
        
        return None

    def search_audio(self, query):
        """Placeholder for audio search."""
        return None


class ToolBelt(SearchTool):
    """
    Main tool wrapper compatible with the existing GenerativeUI app.
    Inherits robustness and fallback from SearchTool.
    """
    def __init__(self):
        super().__init__()

    def generate_image_url(self, prompt):
        """
        Generates AI image via Pollinations.ai (No API key required).
        """
        safe_prompt = prompt.replace(" ", "%20")
        return f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=768&nologo=true"


# ==========================================
# Self-Test Execution
# ==========================================
if __name__ == "__main__":
    import json
    
    print("🧪 Running ToolBelt Self-Test...")
    
    # Check for Keys
    print("\n📋 Environment Check:")
    print(f"  SERPAPI_KEY: {'✅ Found' if os.environ.get('SERPAPI_KEY') else '❌ Missing'}")
    print(f"  GOOGLE_PSE_API_KEY: {'✅ Found' if os.environ.get('GOOGLE_PSE_API_KEY') else '❌ Missing'}")
    print(f"  GOOGLE_PSE_CX: {'✅ Found' if os.environ.get('GOOGLE_PSE_CX') else '❌ Missing'}")

    tools = ToolBelt()
    
    # Test Web Search
    print("\n🌐 Testing web_search('latest AI news')...")
    result = tools.web_search("latest AI news")
    print("-" * 50)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("-" * 50)

    # Test Image Gen
    print("\n🎨 Testing generate_image_url('cyberpunk city')...")
    img_url = tools.generate_image_url("cyberpunk city")
    print(f"URL: {img_url}")
