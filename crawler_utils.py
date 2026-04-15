import asyncio
import httpx
from bs4 import BeautifulSoup
import re
import time
from typing import Dict, Optional, Tuple

# ================= CONFIG =================
TIMEOUT = 8
MAX_REDIRECTS = 3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Tags to completely remove
STRIP_TAGS = [
    "script", "style", "nav", "footer", "header", "aside", 
    "noscript", "iframe", "form", "button", "svg", "ads"
]

# Tags that likely contain main content
CONTENT_TAGS = ["article", "main", "p", "h1", "h2", "h3"]

# ================= STATE =================
# Inflight deduplication: stores URL -> Future
inflight_requests: Dict[str, asyncio.Future] = {}

async def fetch_and_clean_content(url: str) -> Tuple[str, str, str]:
    """
    Fetches URL content, cleans HTML, and returns (title, clean_text, final_url)
    """
    # 1. Inflight Deduplication
    if url in inflight_requests:
        print(f"🔄 Inflight deduplication hit for: {url}")
        return await inflight_requests[url]
    
    future = asyncio.get_event_loop().create_future()
    inflight_requests[url] = future
    
    try:
        result = await _do_fetch(url)
        future.set_result(result)
        return result
    except Exception as e:
        future.set_exception(e)
        raise e
    finally:
        # Cleanup inflight state after a small delay to ensure others can pick it up
        # or just remove it immediately if we want to allow re-fetching soon
        if url in inflight_requests:
            del inflight_requests[url]

async def _do_fetch(url: str) -> Tuple[str, str, str]:
    headers = {"User-Agent": USER_AGENT}
    
    try:
        async with httpx.AsyncClient(follow_redirects=True, max_redirects=MAX_REDIRECTS, timeout=TIMEOUT) as client:
            response = await client.get(url, headers=headers)
            
            # 2. Content-Type Check
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                print(f"⚠️ Skipping non-HTML content: {content_type} for {url}")
                return "", "Non-HTML content", str(response.url)

            # 3. Handle status codes
            if response.status_code != 200:
                print(f"⚠️ Fetch failed: {response.status_code} for {url}")
                return "", f"Error status: {response.status_code}", str(response.url)

            # 4. Parse & Clean
            final_url = str(response.url)
            html_content = response.text
            
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Title extraction
            title = soup.title.string if soup.title else ""
            title = title.strip() if title else ""

            # HTML Cleanup
            for tag in soup(STRIP_TAGS):
                tag.decompose()

            # Extraction Priority
            main_content = []
            
            # Try to find specific content containers first
            for tag_name in ["article", "main", "div.content", "div.post", "section"]:
                # BeautifulSoup search for class needs a dict or specific syntax
                if "." in tag_name:
                    tag, cls = tag_name.split(".")
                    found = soup.find(tag, class_=cls)
                else:
                    found = soup.find(tag_name)
                    
                if found:
                    # If we found a main container, we prioritize its text
                    text = found.get_text(" ", strip=True)
                    if len(text) > 200:
                        main_content.append(text)
                        break

            # If no container found or too small, get all p, h1, h2, h3
            if not main_content:
                for p in soup.find_all(CONTENT_TAGS):
                    txt = p.get_text(" ", strip=True)
                    if len(txt) > 20: 
                        main_content.append(txt)

            # Join and truncate
            full_text = " ".join(main_content)
            
            # Fallback to general text if still empty
            if not full_text.strip():
                full_text = soup.get_text(" ", strip=True)

            # Limit to 3000 chars as requested
            clean_text = full_text[:3000].strip()
            
            # Fix duplicate spaces
            clean_text = re.sub(r'\s+', ' ', clean_text)

            return title, clean_text, final_url

    except Exception as e:
        print(f"❌ Crawler error for {url}: {e}")
        return "", f"Crawler error: {str(e)}", url
