#!/usr/bin/env python3
"""
Media File to Spreadsheet Converter

Converts EDL, ALE, and XML files to Excel spreadsheets for easier reading.
Supports:
- CMX 3600 format EDLs
- Avid Log Exchange (ALE) files
- Final Cut Pro XML and DaVinci Resolve XML exports
"""

import re
import pandas as pd
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Union
import csv
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
from base_tool import BaseTool
import theme
from theme import C


class MediaFileParser:
    def __init__(self):
        self.events = []
        
    def detect_file_type(self, filepath: str) -> str:
        """Detect the type of media file based on extension and content"""
        path = Path(filepath)
        extension = path.suffix.lower()
        
        if extension == '.edl':
            return 'edl'
        elif extension == '.ale':
            return 'ale'
        elif extension == '.xml':
            return 'xml'
        else:
            # Try to detect based on content
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    first_lines = ''.join(f.readlines()[:10]).lower()
                    if 'avid log exchange' in first_lines or 'heading' in first_lines:
                        return 'ale'
                    elif '<?xml' in first_lines:
                        return 'xml'
                    else:
                        return 'edl'  # Default assumption
            except:
                return 'edl'
    
    def parse_timecode(self, timecode: str) -> str:
        """Parse and validate timecode format (HH:MM:SS:FF)"""
        if not timecode or timecode == "00:00:00:00":
            return timecode
        
        # Handle different timecode formats
        if isinstance(timecode, (int, float)):
            # Convert frame count or seconds to timecode
            if timecode > 86400:  # Likely frame count
                return self.frames_to_timecode(int(timecode))
            else:  # Likely seconds
                return self.seconds_to_timecode(float(timecode))
        
        # Basic validation - should be in HH:MM:SS:FF format
        if re.match(r'^\d{2}:\d{2}:\d{2}[:.]\d{2}$', str(timecode)):
            return str(timecode).replace('.', ':')
        return str(timecode)
    
    def frames_to_timecode(self, frames: int, fps: int = 30) -> str:
        """Convert frame count to timecode"""
        hours = frames // (fps * 3600)
        minutes = (frames % (fps * 3600)) // (fps * 60)
        seconds = (frames % (fps * 60)) // fps
        frame = frames % fps
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"
    
    def seconds_to_timecode(self, seconds: float, fps: int = 30) -> str:
        """Convert seconds to timecode"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        frames = int((seconds % 1) * fps)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"
    
    def calculate_duration(self, timecode_in: str, timecode_out: str) -> str:
        """Calculate duration between two timecodes"""
        try:
            # Parse timecodes
            h1, m1, s1, f1 = map(int, timecode_in.replace('.', ':').split(':'))
            h2, m2, s2, f2 = map(int, timecode_out.replace('.', ':').split(':'))
            
            # Convert to frames (assuming 30fps)
            frames_in = h1 * 108000 + m1 * 1800 + s1 * 30 + f1
            frames_out = h2 * 108000 + m2 * 1800 + s2 * 30 + f2
            
            duration_frames = frames_out - frames_in
            
            # Convert back to timecode
            hours = duration_frames // 108000
            minutes = (duration_frames % 108000) // 1800
            seconds = (duration_frames % 1800) // 30
            frames = duration_frames % 30
            
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
        except:
            return "00:00:00:00"


class EDLParser(MediaFileParser):
    def parse_edl_line(self, line: str) -> Optional[Dict]:
        """Parse a single EDL line and extract event information"""
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('TITLE:'):
            return None
            
        # Match EDL event line format
        pattern = r'^(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})'
        match = re.match(pattern, line)
        
        if match:
            event_num, reel, track, edit_type, src_in, src_out, rec_in, rec_out = match.groups()
            
            src_duration = self.calculate_duration(src_in, src_out)
            rec_duration = self.calculate_duration(rec_in, rec_out)
            
            return {
                'Event': int(event_num),
                'Reel/Source': reel,
                'Track': track,
                'Edit Type': edit_type,
                'Source In': self.parse_timecode(src_in),
                'Source Out': self.parse_timecode(src_out),
                'Source Duration': src_duration,
                'Record In': self.parse_timecode(rec_in),
                'Record Out': self.parse_timecode(rec_out),
                'Record Duration': rec_duration,
                'Comments': ''
            }
        
        # Handle comment lines
        if line.startswith('*') and self.events:
            comment = line[1:].strip()
            if self.events[-1]['Comments']:
                self.events[-1]['Comments'] += ' | ' + comment
            else:
                self.events[-1]['Comments'] = comment
                
        return None
    
    def parse(self, filepath: str) -> List[Dict]:
        """Parse an EDL file"""
        self.events = []
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(filepath, 'r', encoding='latin-1') as f:
                lines = f.readlines()
        
        for line in lines:
            event = self.parse_edl_line(line)
            if event:
                self.events.append(event)
        
        return self.events


class ALEParser(MediaFileParser):
    def parse(self, filepath: str) -> List[Dict]:
        """Parse an ALE file"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(filepath, 'r', encoding='latin-1') as f:
                content = f.read()
        
        lines = content.split('\n')
        
        # Find the data section
        data_start = -1
        column_line = -1
        
        for i, line in enumerate(lines):
            if line.strip().startswith('Heading'):
                column_line = i
            elif line.strip().startswith('Data') or (column_line >= 0 and i == column_line + 1):
                data_start = i + 1 if line.strip().startswith('Data') else i
                break
        
        if data_start == -1 or column_line == -1:
            raise ValueError("Could not find ALE data section")
        
        # Get column headers
        headers = [h.strip() for h in lines[column_line].replace('Heading', '').strip().split('\t') if h.strip()]
        
        # Parse data rows
        events = []
        for i in range(data_start, len(lines)):
            line = lines[i].strip()
            if not line:
                continue
                
            values = line.split('\t')
            if len(values) >= len(headers):
                event = {}
                for j, header in enumerate(headers):
                    value = values[j].strip() if j < len(values) else ''
                    
                    # Handle common ALE fields
                    if 'timecode' in header.lower() or header.lower() in ['start', 'end', 'in', 'out']:
                        value = self.parse_timecode(value)
                    
                    event[header] = value
                
                # Calculate duration if we have in/out points
                if 'Start' in event and 'End' in event:
                    event['Duration'] = self.calculate_duration(event['Start'], event['End'])
                
                events.append(event)
        
        return events


class XMLParser(MediaFileParser):
    def parse(self, filepath: str) -> List[Dict]:
        """Parse XML files (Final Cut Pro, DaVinci Resolve, etc.)"""
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML file: {e}")
        
        events = []
        
        # Handle Final Cut Pro XML
        if 'fcpxml' in root.tag.lower() or root.find('.//asset') is not None:
            events = self.parse_fcpxml(root)
        # Handle DaVinci Resolve XML  
        elif root.find('.//clipitem') is not None or 'xmeml' in root.tag.lower():
            events = self.parse_resolve_xml(root)
        # Generic XML parsing
        else:
            events = self.parse_generic_xml(root)
            
        return events
    
    def parse_fcpxml(self, root) -> List[Dict]:
        """Parse Final Cut Pro XML"""
        events = []
        event_num = 1
        
        # Look for clips in various locations
        clips = root.findall('.//clip') + root.findall('.//asset-clip') + root.findall('.//ref-clip')
        
        for clip in clips:
            event = {'Event': event_num}
            
            # Get basic info
            event['Name'] = clip.get('name', '')
            event['Duration'] = clip.get('duration', '')
            event['Start'] = clip.get('start', '')
            event['Offset'] = clip.get('offset', '')
            
            # Look for timecode info
            timecode_elem = clip.find('.//timecode')
            if timecode_elem is not None:
                event['Timecode'] = timecode_elem.get('start', '')
            
            # Look for media reference
            media = clip.find('.//media')
            if media is not None:
                event['Media'] = media.get('src', '')
            
            events.append(event)
            event_num += 1
            
        return events
    
    def parse_resolve_xml(self, root) -> List[Dict]:
        """Parse DaVinci Resolve/Avid XML"""
        events = []
        event_num = 1
        
        # Look for clip items
        clipitems = root.findall('.//clipitem')
        
        for clipitem in clipitems:
            event = {'Event': event_num}
            
            # Get basic clip info
            name_elem = clipitem.find('name')
            event['Name'] = name_elem.text if name_elem is not None else ''
            
            # Get timecode info
            start_elem = clipitem.find('start')
            end_elem = clipitem.find('end')
            in_elem = clipitem.find('in')
            out_elem = clipitem.find('out')
            
            if start_elem is not None:
                event['Start'] = self.parse_timecode(start_elem.text)
            if end_elem is not None:
                event['End'] = self.parse_timecode(end_elem.text)
            if in_elem is not None:
                event['Source In'] = self.parse_timecode(in_elem.text)
            if out_elem is not None:
                event['Source Out'] = self.parse_timecode(out_elem.text)
            
            # Calculate duration
            if 'Start' in event and 'End' in event:
                event['Duration'] = self.calculate_duration(event['Start'], event['End'])
            
            # Get file reference
            file_elem = clipitem.find('.//pathurl')
            if file_elem is not None:
                event['File Path'] = file_elem.text
            
            events.append(event)
            event_num += 1
            
        return events
    
    def parse_generic_xml(self, root) -> List[Dict]:
        """Generic XML parser for unknown formats"""
        events = []
        
        # Try to find repeating elements that might be clips/events
        for elem in root.iter():
            if len(list(elem)) > 2:  # Has multiple children, might be an event
                event = {}
                for child in elem:
                    if child.text and child.text.strip():
                        key = child.tag.replace('{', '').replace('}', '').split('}')[-1]  # Remove namespace
                        event[key] = child.text.strip()
                
                if event:  # Only add if we found some data
                    events.append(event)
        
        return events


def convert_media_file_to_spreadsheet(filepath: str, output_path: str = None) -> str:
    """
    Convert a media file (EDL/ALE/XML) to Excel spreadsheet
    
    Args:
        filepath: Path to the media file
        output_path: Path for output Excel file (optional)
        
    Returns:
        Path to the created Excel file
    """
    # Detect file type and choose appropriate parser
    parser = MediaFileParser()
    file_type = parser.detect_file_type(filepath)
    
    if file_type == 'edl':
        parser = EDLParser()
    elif file_type == 'ale':
        parser = ALEParser()
    elif file_type == 'xml':
        parser = XMLParser()
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
    
    events = parser.parse(filepath)
    
    if not events:
        raise ValueError(f"No valid events found in the {file_type.upper()} file")
    
    # Create DataFrame
    df = pd.DataFrame(events)
    
    # Generate output filename if not provided
    if not output_path:
        input_file = Path(filepath)
        output_path = input_file.parent / f"{input_file.stem}_converted.xlsx"
    
    # Write to Excel with formatting
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        sheet_name = f'{file_type.upper()} Events'
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        # Get the worksheet to apply formatting
        worksheet = writer.sheets[sheet_name]
        
        # Auto-adjust column widths
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value or '')) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(length + 2, 50)
        
        # Make header row bold
        for cell in worksheet[1]:
            cell.font = cell.font.copy(bold=True)
    
    return str(output_path)


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "EDL/ALE/XML Converter"
        self.description = "Convert EDL, ALE, and XML files to Excel spreadsheets for easier reading."
        self.category = "Editorial & Turnover"
        self.icon = "list-video"
        self.window = None
        self.converter = None

    def create_window(self, parent):
        self.window = self.create_tool_window(parent, self.name, 760, 500)
        self.window.minsize(620, 440)
        page = theme.page(self.window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))
        self.converter = ConverterFrame(page)
        self.converter.pack(fill="both", expand=True)
        return self.window

class ConverterFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self.input_file = None
        self.output_file = None
        self.setup_ui()

    def setup_ui(self):
        # Main action first so it's never pushed off the bottom
        actions = theme.action_bar(self)
        convert_btn = theme.Button(actions, "Convert to Spreadsheet…", kind="primary", width=200,
                                   command=self.convert_file)
        convert_btn.pack(side="right")

        # Top controls
        source = theme.Card(self, title="Edit list",
                            subtitle="CMX 3600 EDL, Avid ALE, or Final Cut Pro / DaVinci Resolve XML")
        source.pack(fill="x")
        controls = source.body
        controls.grid_columnconfigure(0, weight=1)

        path_box = ctk.CTkFrame(controls, fg_color=C["field"], border_width=1,
                                border_color=C["border_strong"], corner_radius=8, height=34)
        path_box.grid(row=0, column=0, sticky="ew")
        path_box.pack_propagate(False)
        self.file_label = ctk.CTkLabel(path_box, text="No file selected", anchor="w", height=30)
        self.file_label.pack(fill="x", padx=10, pady=2)

        browse_btn = theme.Button(controls, "Select File…", kind="secondary", width=120, command=self.browse_file)
        browse_btn.grid(row=0, column=1, padx=(8, 0))

        # Info box
        status = theme.Card(self, title="Status")
        status.pack(fill="both", expand=True, pady=(14, 0))
        self.info_text = ctk.CTkTextbox(status.body, height=120, wrap="word")
        self.info_text.pack(fill="both", expand=True)
        self.info_text.insert("1.0", "Select an EDL, ALE, or XML file to convert.")

    def browse_file(self):
        filetypes = [
            ("Media Files", "*.edl *.ale *.xml"),
            ("EDL Files", "*.edl"),
            ("ALE Files", "*.ale"),
            ("XML Files", "*.xml"),
            ("All Files", "*.*")
        ]
        file_path = filedialog.askopenfilename(title="Select Media File", filetypes=filetypes)
        if file_path:
            self.input_file = file_path
            self.file_label.configure(text=os.path.basename(file_path))
            self.info_text.delete("1.0", "end")
            self.info_text.insert("1.0", f"Selected file: {file_path}\nReady to convert.")

    def convert_file(self):
        if not self.input_file:
            messagebox.showwarning("No File", "Please select a media file to convert.")
            return
        try:
            output_path = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
                initialfile=f"{Path(self.input_file).stem}_converted.xlsx"
            )
            if not output_path:
                return
            result_path = convert_media_file_to_spreadsheet(self.input_file, output_path)
            self.info_text.delete("1.0", "end")
            self.info_text.insert("1.0", f"Successfully converted to spreadsheet:\n{result_path}")
        except Exception as e:
            self.info_text.delete("1.0", "end")
            self.info_text.insert("1.0", f"Error: {e}")

def create_tool():
    return Tool()

def get_tool():
    return Tool()

# CLI entry point remains for direct script use
def main():
    parser = argparse.ArgumentParser(description='Convert EDL, ALE, and XML files to Excel spreadsheets')
    parser.add_argument('input_file', help='Path to the media file (EDL/ALE/XML)')
    parser.add_argument('-o', '--output', help='Output Excel file path (optional)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('-t', '--type', choices=['edl', 'ale', 'xml'], help='Force file type detection')
    
    args = parser.parse_args()
    
    try:
        output_file = convert_media_file_to_spreadsheet(args.input_file, args.output)
        
        detector = MediaFileParser()
        file_type = args.type or detector.detect_file_type(args.input_file)
        
        print(f"Successfully converted {file_type.upper()} to spreadsheet: {output_file}")
        
        if args.verbose:
            df = pd.read_excel(output_file)
            print(f"\nConverted {len(df)} events:")
            print(df.head().to_string(index=False))
            
    except Exception as e:
        print(f"Error converting file: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
