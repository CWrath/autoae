import customtkinter as ctk
from abc import ABC, abstractmethod
import theme

class BaseTool(ABC):
    """Base class for all tools"""
    
    def __init__(self):
        self.name = "Base Tool"
        self.icon = None  # Icon name from assets/icons (e.g. "film") or a path to a PNG
        self.description = "Tool description"
        self.category = None  # Launcher sidebar group, e.g. "Media"
        
    @abstractmethod
    def create_window(self, parent):
        """Create and return a new window for this tool"""
        pass
    
    def create_tool_window(self, parent, title, width=800, height=600):
        """Helper method to create a standard tool window"""
        window = ctk.CTkToplevel(parent)
        window.title(title)

        # Size it, keep it on screen (small laptops / 150% scaling), and center it
        theme.center(window, width, height)

        return window