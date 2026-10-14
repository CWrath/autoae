import customtkinter as ctk
import webbrowser
from urllib.parse import urlparse
from base_tool import BaseTool
import theme
from theme import C

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Installers"
        self.description = "Quick links to download video editing software"
        self.category = "Project Setup"
        self.icon = "download"

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Software Installers", 640, 510)
        window.minsize(520, 500)

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        # Software links
        software = [
            ("Avid Media Composer", "https://www.avid.com/media-composer"),
            ("Wave Agent", "https://www.sounddevices.com/download/?prod=wave-agent-software"),
            ("DaVinci Resolve", "https://www.blackmagicdesign.com/products/davinciresolve"),
            ("Adobe Creative Cloud", "https://www.adobe.com/creativecloud.html"),
            ("FFmpeg", "https://ffmpeg.org/download.html"),
            ("VLC Media Player", "https://www.videolan.org/vlc/")
        ]

        card = theme.Card(page, title="Download pages")
        card.pack(fill="x")
        card.body.grid_columnconfigure(0, weight=1)

        # One row per app: name + site on the left, Open on the right
        for i, (name, url) in enumerate(software):
            if i:
                theme.divider(card.body).grid(row=i * 2 - 1, column=0, columnspan=2, sticky="ew", pady=2)
            text = ctk.CTkFrame(card.body, fg_color="transparent")
            text.grid(row=i * 2, column=0, sticky="w", pady=6)
            ctk.CTkLabel(text, text=name, font=theme.font("heading"), anchor="w", height=20).pack(anchor="w")
            theme.muted(text, urlparse(url).netloc.replace("www.", ""), height=16).pack(anchor="w")
            theme.Button(
                card.body, "Open", kind="secondary", width=86,
                image=theme.icon("external-link", C["text_muted"], 15), compound="right",
                command=lambda u=url: webbrowser.open(u)
            ).grid(row=i * 2, column=1, sticky="e", pady=6)

        return window
