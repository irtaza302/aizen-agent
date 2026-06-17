import os
import threading
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, Browser, Playwright
except ImportError:
    sync_playwright = None
    Page = None
    Browser = None
    Playwright = None

class BrowserManager:
    _instance = None
    _lock = threading.Lock()
    
    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.ctx_manager = None
        
    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure_browser(self):
        if not sync_playwright:
            raise ImportError("playwright is not installed. Run 'pip install playwright' and 'playwright install'")
            
        if not self.playwright:
            self.ctx_manager = sync_playwright()
            self.playwright = self.ctx_manager.__enter__()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.page = self.browser.new_page()

    def goto(self, url: str) -> str:
        self._ensure_browser()
        try:
            self.page.goto(url, timeout=15000)
            return f"Successfully navigated to {url}. Title: {self.page.title()}"
        except Exception as e:
            return f"Error navigating: {e}"

    def click(self, selector: str) -> str:
        self._ensure_browser()
        try:
            self.page.click(selector, timeout=5000)
            return f"Clicked element: {selector}"
        except Exception as e:
            return f"Error clicking: {e}"

    def get_content(self) -> str:
        self._ensure_browser()
        try:
            content = self.page.content()
            # truncate to avoid blowing up context
            return content[:15000] + ("\n...[truncated]" if len(content) > 15000 else "")
        except Exception as e:
            return f"Error getting content: {e}"

    def screenshot(self, filepath: str) -> str:
        self._ensure_browser()
        try:
            self.page.screenshot(path=filepath, full_page=True)
            return f"Saved screenshot to {filepath}"
        except Exception as e:
            return f"Error saving screenshot: {e}"

    def evaluate(self, script: str) -> str:
        self._ensure_browser()
        try:
            result = self.page.evaluate(script)
            return str(result)
        except Exception as e:
            return f"Error evaluating script: {e}"

def browser_goto(url: str) -> str:
    return BrowserManager.get_instance().goto(url)

def browser_click(selector: str) -> str:
    return BrowserManager.get_instance().click(selector)

def browser_get_content() -> str:
    return BrowserManager.get_instance().get_content()

def browser_screenshot(filepath: str) -> str:
    return BrowserManager.get_instance().screenshot(filepath)

def browser_evaluate(script: str) -> str:
    return BrowserManager.get_instance().evaluate(script)
