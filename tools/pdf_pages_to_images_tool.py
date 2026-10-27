import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from base_tool import BaseTool
from drag_drop import enable_path_drop
import theme


RENDER_DPI = 150
JPEG_QUALITY = 95


def export_pdf_pages(pdf_path, output_folder, image_format, progress_callback=None):
    """Render each PDF page to an image, returning written paths and page errors."""
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for PDF page export. Install it with: pip install PyMuPDF"
        ) from exc

    if image_format not in {"PNG", "JPEG"}:
        raise ValueError("Image format must be PNG or JPEG.")

    extension = "png" if image_format == "PNG" else "jpg"
    os.makedirs(output_folder, exist_ok=True)
    written_paths = []
    page_errors = []

    document = pymupdf.open(pdf_path)
    try:
        if document.needs_pass:
            raise RuntimeError("This PDF is password-protected and cannot be opened.")
        if document.page_count == 0:
            raise RuntimeError("This PDF has no pages to export.")

        total_pages = document.page_count
        matrix = pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
        pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]

        for page_index in range(total_pages):
            page_number = page_index + 1
            output_path = os.path.join(
                output_folder, "{}_page_{:03d}.{}".format(pdf_stem, page_number, extension)
            )
            try:
                pixmap = document[page_index].get_pixmap(
                    matrix=matrix, colorspace=pymupdf.csRGB, alpha=False
                )
                if image_format == "JPEG":
                    pixmap.save(output_path, jpg_quality=JPEG_QUALITY)
                else:
                    pixmap.save(output_path)
            except Exception as exc:
                page_errors.append((page_number, str(exc)))
                if progress_callback:
                    progress_callback(page_number, total_pages, output_path, str(exc))
            else:
                written_paths.append(output_path)
                if progress_callback:
                    progress_callback(page_number, total_pages, output_path, None)
    finally:
        document.close()

    return written_paths, page_errors


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "PDF Pages to Images"
        self.description = "Save every page of a PDF as a PNG or JPEG image"
        self.category = "Media"
        self.icon = "image"
        self._worker_queue = queue.Queue()
        self._running = False

    def create_window(self, parent):
        window = self.create_tool_window(parent, self.name, 820, 620)
        window.minsize(700, 520)
        self.window = window

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        actions = theme.action_bar(page)
        self.export_button = theme.Button(
            actions, "Export Pages", kind="primary", width=140, command=self.start_export
        )
        self.export_button.pack(side="right")
        self.status_label = theme.muted(actions, "Ready", role="body")
        self.status_label.pack(side="left")

        settings = theme.Card(page, title="PDF & output")
        settings.pack(fill="x")
        fields = settings.body
        fields.grid_columnconfigure(1, weight=1)

        theme.muted(fields, "PDF file", role="body", width=110).grid(row=0, column=0, sticky="w")
        self.pdf_entry = ctk.CTkEntry(fields, placeholder_text="Choose a PDF file", height=34)
        self.pdf_entry.grid(row=0, column=1, padx=(0, 8), sticky="ew")
        theme.Button(fields, "Browse…", kind="secondary", width=90,
                     command=self.browse_pdf).grid(row=0, column=2)

        theme.muted(fields, "Output folder", role="body", width=110).grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.output_entry = ctk.CTkEntry(
            fields, placeholder_text="Blank creates a <PDF name>_pages folder beside the PDF", height=34
        )
        self.output_entry.grid(row=1, column=1, padx=(0, 8), pady=(10, 0), sticky="ew")
        theme.Button(fields, "Choose…", kind="secondary", width=90,
                     command=self.browse_output).grid(row=1, column=2, pady=(10, 0))

        theme.muted(fields, "Image format", role="body", width=110).grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.format_var = tk.StringVar(value="PNG")
        ctk.CTkOptionMenu(
            fields, variable=self.format_var, values=["PNG", "JPEG"], width=140, height=34
        ).grid(row=2, column=1, sticky="w", pady=(10, 0))
        theme.muted(
            fields, "Pages render at 150 DPI. JPEG images use 95% quality.", role="caption"
        ).grid(row=2, column=2, sticky="e", padx=(8, 0), pady=(10, 0))

        log_card = theme.Card(page, title="Export log")
        log_card.pack(fill="both", expand=True, pady=(14, 0))
        self.log_box = theme.LogBox(log_card.body, wrap="word")
        self.log_box.pack(fill="both", expand=True)
        self.log("Ready. Choose or drag in a PDF and export its pages as images.")
        enable_path_drop(window, self.pdf_entry, self.drop_pdf, settings)
        enable_path_drop(window, self.output_entry, self.drop_output)
        return window

    def drop_pdf(self, paths):
        pdf = next((p for p in paths if os.path.isfile(p) and p.lower().endswith(".pdf")), None)
        if pdf is None:
            self.log("Drop a .pdf file to export its pages.")
            return
        self.pdf_entry.delete(0, tk.END)
        self.pdf_entry.insert(0, pdf)

    def drop_output(self, paths):
        folder = next((p for p in paths if os.path.isdir(p)), None)
        if folder is None:
            self.log("Drop a folder to use as the output location.")
            return
        self.output_entry.delete(0, tk.END)
        self.output_entry.insert(0, folder)

    def browse_pdf(self):
        pdf_path = filedialog.askopenfilename(
            title="Choose a PDF file",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if pdf_path:
            self.pdf_entry.delete(0, tk.END)
            self.pdf_entry.insert(0, pdf_path)

    def browse_output(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)

    def start_export(self):
        if self._running:
            return

        pdf_path = self.pdf_entry.get().strip()
        if not pdf_path:
            self.status_label.configure(text="Choose a PDF file.")
            self.log("Warning: Choose a PDF file before exporting.")
            return
        if not os.path.isfile(pdf_path):
            self.status_label.configure(text="PDF file not found.")
            self.log("Error: The selected PDF file does not exist.")
            return

        output_folder = self.output_entry.get().strip()
        if not output_folder:
            pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]
            output_folder = os.path.join(os.path.dirname(pdf_path), pdf_stem + "_pages")
        image_format = self.format_var.get()

        self._worker_queue = queue.Queue()
        self._running = True
        self.export_button.configure(state="disabled")
        self.status_label.configure(text="Exporting pages…")
        self.log("Starting: {} → {} ({})".format(pdf_path, output_folder, image_format))
        threading.Thread(
            target=self._export_worker,
            args=(pdf_path, output_folder, image_format),
            daemon=True,
        ).start()
        self.window.after(100, self._poll_worker_queue)

    def _export_worker(self, pdf_path, output_folder, image_format):
        try:
            written, errors = export_pdf_pages(
                pdf_path,
                output_folder,
                image_format,
                progress_callback=lambda number, total, path, error: self._worker_queue.put(
                    ("page", number, total, path, error)
                ),
            )
            self._worker_queue.put(("finished", len(written), len(errors), output_folder))
        except Exception as exc:
            self._worker_queue.put(("failed", str(exc)))

    def _poll_worker_queue(self):
        while True:
            try:
                event = self._worker_queue.get_nowait()
            except queue.Empty:
                break

            if event[0] == "page":
                _, number, total, path, error = event
                if error:
                    self.log("Error: Page {} of {} could not be saved: {}".format(number, total, error))
                else:
                    self.log("Converted: Page {} of {} → {}".format(number, total, os.path.basename(path)))
                self.status_label.configure(text="Exporting page {} of {}…".format(number, total))
            elif event[0] == "finished":
                _, written, errors, output_folder = event
                self._finish_export()
                if errors:
                    self.status_label.configure(text="Finished with {} page error(s).".format(errors))
                    self.log("Warning: Exported {} page(s); {} page(s) failed.".format(written, errors))
                else:
                    self.status_label.configure(text="Export complete: {} page(s).".format(written))
                    self.log("Complete: {} page(s) saved to {}".format(written, output_folder))
            elif event[0] == "failed":
                self._finish_export()
                self.status_label.configure(text="Export failed.")
                self.log("Failed: {}".format(event[1]))

        if self._running:
            self.window.after(100, self._poll_worker_queue)

    def _finish_export(self):
        self._running = False
        self.export_button.configure(state="normal")

    def log(self, text):
        self.log_box.insert(tk.END, text)
        self.log_box.see(tk.END)
