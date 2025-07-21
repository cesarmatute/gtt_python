import tkinter as tk
import os
from tkinter import font as tkfont
from tkinter import ttk, messagebox, filedialog
from typing import List, Dict, Any, Optional
import json
from datetime import datetime, timedelta, timezone
import requests
import io
import shutil
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import winsound

import win32event # pyright: ignore[reportMissingModuleSource]
import win32api # pyright: ignore[reportMissingModuleSource]
from winerror import ERROR_ALREADY_EXISTS # pyright: ignore[reportMissingModuleSource]

import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud.firestore_v1 import ArrayUnion

# --- Constants ---
CONFIG_FILE = 'config.json'
SECRETS_DIR = 'secrets'
USERS_COLLECTION = 'users'
ROLE_PARENT = 'Parent'
ROLE_KID = 'Kid'

import cloudinary
import cloudinary.uploader
import cloudinary.api
from PIL import Image, ImageTk
import threading
import pystray
from PIL import Image as PILImage
from tkinter import simpledialog
import logging

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GameSentryApp(tk.Tk):
    """
    Main application class for Game Sentry.
    This class sets up the main window.
    """
    def __init__(self):
        super().__init__()

        # --- Single Instance Check ---
        mutex_name = "GameSentryMutex"
        self.mutex = win32event.CreateMutex(None, 1, mutex_name)
        if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
            messagebox.showerror("Already Running", "An instance of Game Sentry is already running.")
            self.destroy()
            return

        self.tray_icon = None
        self.tray_thread = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_minimize)

        self.title("Game Sentry")

        # --- Center Window on Screen ---
        window_width = 950
        window_height = 600
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        position_x = int((screen_width / 2) - (window_width / 2))
        position_y = int((screen_height / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{position_x}+{position_y}")

        # --- Theme and Color Management ---
        self.themes = {
            "light": {
                "primary": "#4A90E2", "accent": "#50E3C2", "background": "#F9FAFB",
                "text": "#2D3748", "widget_bg": "#FFFFFF", "widget_fg": "#2D3748",
                "list_header_bg": "#E2E8F0", "warning": "#FFC107"
            },
            "dark": {
                "primary": "#2D3748", "accent": "#6B46C1", "background": "#1A202C",
                "text": "#F9FAFB", "widget_bg": "#2D3748", "widget_fg": "#F9FAFB",
                "list_header_bg": "#4A5568", "warning": "#FFC107"
            }
        }

        # Tray setting (must be before load_config)
        self.close_to_tray_enabled = tk.BooleanVar(value=True)
        self.sound_notifications_enabled = tk.BooleanVar(value=True)
        self._load_tray_setting()

        # --- View State (must be before load_config) ---
        self.current_view = ROLE_PARENT
        self.current_kid_user: Optional[Dict[str, Any]] = None
        self.last_selected_kid_id: Optional[str] = None  # Remember last selected kid
        self.last_view: Optional[str] = None  # Remember last view (parent or kid)
        self.top_bar_buttons: List[RoundedButton] = []
        self.kid_view_avatar_image = None
        self.kid_view_placeholder_image = None

        self.current_theme = self.load_config()
        self.theme_colors = {}
        self.button_colors = {}
        
        # Track daily limit notifications to avoid spam
        self.daily_limit_notifications = set()
        # Track break periods for kids
        self.kid_rest_periods = {}  # {user_id: {'start_time': datetime, 'duration_minutes': int}}
        # Track play time limit notifications to avoid spam
        self.block_limit_notifications = set()
        # Track whether a kid has confirmed having lunch today
        self.lunch_confirmed_today = {} # {user_id: date}
 
        # --- Initialize Firebase ---
        self.db = initialize_firebase()
        if not self.db:
            messagebox.showerror("Firebase Error", "Could not initialize Firebase. The application will close.")
            self.destroy()
            return
        
        # --- Apply Theme and Styles ---
        # This must be done after Firebase init but before creating widgets
        # so that all components are created with the correct theme.
        self.apply_theme(self.current_theme)

        # --- Initialize to last view and user ---
        self._initialize_to_last_view()

        # --- Create necessary directories ---
        # Ensure the 'avatars' directory exists for storing user profile images.
        self.avatars_dir = os.path.join(os.path.dirname(__file__), 'avatars')
        os.makedirs(self.avatars_dir, exist_ok=True)

        # --- Load Logo Image ---
        # Store the PhotoImage as an instance attribute to prevent it from being
        # garbage-collected, which would cause the image to disappear.
        self.logo_image = None
        try:
            logo_path = "" # Initialize to prevent reference before assignment in except block
            # Construct a reliable path to the image file relative to the script
            logo_path = os.path.join(os.path.dirname(__file__), 'pictures', 'gtt_logo.png')
            # Load the original image and then subsample it to a smaller size.
            # The numbers (5, 5) mean we take every 5th pixel, making it smaller.
            original_image = tk.PhotoImage(file=logo_path)
            self.logo_image = original_image.subsample(10)
        except tk.TclError:
            print(f"Warning: Could not find or open logo file at '{logo_path}'. Falling back to text logo.")

        # --- Top Bar Frame ---
        self.top_bar_frame = ttk.Frame(self, padding=(5, 10), style='Primary.TFrame')
        self.top_bar_frame.pack(side=tk.TOP, fill=tk.X)

        # --- Divider ---
        self.divider = ttk.Separator(self, orient='horizontal', style='Accent.TSeparator')
        self.divider.pack(fill='x')

        # --- Main Content Area (Lower Section) ---
        self.main_content_frame = ttk.Frame(self, padding="10", style='Background.TFrame')
        self.main_content_frame.pack(fill=tk.BOTH, expand=True)

        self.session_log_frame = None  # Initialize to None

        self._update_ui_for_view() # Initial UI build

        # For parent log widgets cleanup
        self._parent_log_widgets = []

        # Add to __init__:
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.AVATAR_DIR = os.path.join(self.BASE_DIR, "pictures/avatars")
        self.LOGO_PATH = os.path.join(self.BASE_DIR, "pictures/gtt_logo.png")
        self.ICON_DIR = os.path.join(self.BASE_DIR, "secrets/ACLib")
        self.SOUNDS_DIR = os.path.join(self.BASE_DIR, "sounds")
        
        # Ensure directories exist
        os.makedirs(self.AVATAR_DIR, exist_ok=True)
        os.makedirs(self.ICON_DIR, exist_ok=True)
        
        # Now load images using these paths
        try:
            original_image = tk.PhotoImage(file=self.LOGO_PATH)
            self.logo_image = original_image.subsample(10)
        except tk.TclError:
            logger.warning(f"Could not find logo at {self.LOGO_PATH}")
            
        self._initialize_tray_icon()

    def _clear_widgets(self, frame):
        """Destroys all child widgets of a given frame."""
        for widget in frame.winfo_children():
            widget.destroy()

    def _update_ui_for_view(self):
        logger.debug(f"Updating UI for view: {self.current_view}")
        """Clears and rebuilds the UI based on the current view (Parent or Kid)."""
        # Always clear all children of main_content_frame before rebuilding UI for any view
        for child in self.main_content_frame.winfo_children():
            child.destroy()
        self._clear_widgets(self.top_bar_frame)
        self._clear_widgets(self.main_content_frame)
        self.top_bar_buttons.clear()

        # --- Rebuild Top Bar ---
        # Logo and Title are common to both views
        logo_label = ttk.Label(self.top_bar_frame, style='Primary.TLabel')
        if self.logo_image:
            logo_label.configure(image=self.logo_image)
        else:
            logo_label.configure(text="🎮⏱️", font=("", 27, "bold"))
        logo_label.pack(side=tk.LEFT, padx=(5, 10))

        app_title_label = ttk.Label(
            self.top_bar_frame, text="Game Sentry", font=("", 27, "bold"), style='Primary.TLabel'
        )
        app_title_label.pack(side=tk.LEFT)

        # --- View-Specific Buttons ---
        if self.current_view == ROLE_PARENT:
            # Buttons are packed from right to left for correct alignment.
            # Order: "Switch to Kid View", "Manage Users", "Help", "Settings"
            settings_button = RoundedButton(self.top_bar_frame, "Settings", self.open_settings, self.button_colors)
            settings_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(settings_button)

            help_button = RoundedButton(self.top_bar_frame, "Help", self.open_help, self.button_colors)
            help_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(help_button)

            manage_users_button = RoundedButton(self.top_bar_frame, "Manage Users", self.manage_users, self.button_colors)
            manage_users_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(manage_users_button)

            switch_view_button = RoundedButton(self.top_bar_frame, "Switch to Kid View", self.switch_to_kid_view, self.button_colors)
            switch_view_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(switch_view_button)

        elif self.current_view == ROLE_KID:
            # Order: [Edit Profile] [Switch to Parent View] [Switch Profile] (all right-aligned)
            edit_profile_button = RoundedButton(self.top_bar_frame, "Edit Profile", self.edit_profile, self.button_colors)
            edit_profile_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(edit_profile_button)

            switch_view_button = RoundedButton(self.top_bar_frame, "Switch to Parent View", self.switch_to_parent_view, self.button_colors)
            switch_view_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(switch_view_button)

            switch_profile_button = RoundedButton(self.top_bar_frame, "Switch Profile", self._switch_kid_profile, self.button_colors)
            switch_profile_button.pack(side=tk.RIGHT, padx=5)
            self.top_bar_buttons.append(switch_profile_button)

        # --- Rebuild Main Content ---
        if self.current_view == ROLE_PARENT:
            # Properly manage session_log_frame lifecycle
            if self.session_log_frame is not None and self.session_log_frame.winfo_exists():
                self.session_log_frame.destroy()
            
            self.session_log_frame = ttk.Frame(self.main_content_frame)
            self.session_log_frame.pack(fill=tk.BOTH, expand=True)
            
            # --- Kid Filter Combobox ---
            users = list_users(self.db) if self.db else []
            kid_users = [user for user in users if user.get('role') == ROLE_KID]
            kid_names = [user.get('username', 'Kid') for user in kid_users]
            kid_id_map = {user.get('username', 'Kid'): user.get('id') for user in kid_users}
            filter_frame = ttk.Frame(self.session_log_frame)
            filter_frame.pack(pady=(10, 0))
            ttk.Label(filter_frame, text="Filter by Kid:").pack(side=tk.LEFT)
            self.filter_var = tk.StringVar(value="All")

            filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, values=["All"] + kid_names, state="readonly", width=20)
            filter_combo.pack(side=tk.LEFT, padx=5)
            filter_combo.bind("<<ComboboxSelected>>", lambda e: self.show_logs_for_kid())

            # --- Date Filter Calendar ---
            # Remove the calendar and clear button, just show today's date as a label
            date_filter_frame = ttk.Frame(self.main_content_frame)
            date_filter_frame.pack(pady=(0, 0))
            today_str = datetime.now().strftime('%Y-%m-%d')
            self.date_filter_var = tk.StringVar(value=today_str)
            ttk.Label(date_filter_frame, text=f"Day: {today_str}").pack(side=tk.LEFT, padx=5)
            # Always filter logs to today
            get_selected_date = lambda: self.date_filter_var.get()

            # --- All Kids' Session Logs Table (with filter) ---
            self.show_logs_for_kid()
        elif self.current_view == ROLE_KID and self.current_kid_user:
            # Properly manage session_log_frame lifecycle for kid view
            if self.session_log_frame is not None and self.session_log_frame.winfo_exists():
                self.session_log_frame.destroy()
            
            self.session_log_frame = ttk.Frame(self.main_content_frame)
            self.session_log_frame.pack(fill=tk.BOTH, expand=True)
            
            # Now build the kid view UI
            # --- Use a grid layout: labels above their sections, aligned at the top ---
            grid_frame = ttk.Frame(self.session_log_frame)
            grid_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            style = ttk.Style()
            style.configure('UserInfo.TLabelframe', background="#23272b", borderwidth=2)
            style.configure('UserInfo.TLabelframe.Label', background="#23272b", foreground="#fff", font=("Helvetica", 18, "bold"))
            style.configure('UserInfo.TFrame', background="#23272b")
            left_pane = ttk.Labelframe(grid_frame, padding="10", style='UserInfo.TLabelframe')
            left_pane.grid(row=1, column=0, sticky="nws", padx=(0, 20), pady=(0, 0))
            left_pane.update_idletasks()
            left_pane.config(width=330)

            right_pane = ttk.Frame(grid_frame, padding="10 0 0 8", style='TFrame')  # Left and top padding
            right_pane.grid(row=1, column=1, sticky="nsew", pady=(0, 0))
            grid_frame.grid_columnconfigure(0, weight=0)
            grid_frame.grid_columnconfigure(1, weight=1)
            grid_frame.grid_rowconfigure(1, weight=1)

            # --- Populate Left Pane ---
            avatar_label = ttk.Label(left_pane, background="#23272b", foreground="#fff")
            avatar_label.pack(pady=(0, 10))

            avatar_url = self.current_kid_user.get('avatar_url')

            if avatar_url:
                try:
                    img = load_image_from_url(avatar_url, (150, 150))
                    if img:
                        self.kid_view_avatar_image = ImageTk.PhotoImage(img)
                        avatar_label.config(image=self.kid_view_avatar_image, text="", compound='none')
                    else:
                        self._set_avatar_placeholder(avatar_label, "Avatar\nError")
                except Exception as e:
                    print(f"Error loading kid view avatar from URL: {e}")
                    self._set_avatar_placeholder(avatar_label, "Avatar\nError")
            else:
                self._set_avatar_placeholder(avatar_label, "No\nAvatar")

            username = self.current_kid_user.get('username', 'Kid')
            ttk.Label(left_pane, text=username, font=("Helvetica", 20, "bold"), anchor=tk.CENTER, background="#23272b", foreground="#fff").pack(pady=10, fill=tk.X)

            # --- Daily Time Remaining ---
            user_id = self.current_kid_user.get('id')
            if user_id:
                daily_remaining = self._calculate_daily_remaining(user_id)
                daily_remaining_text = self._format_time_remaining(daily_remaining)
                daily_color = "#28a745" if daily_remaining > 3600 else "#ffc107" if daily_remaining > 1800 else "#dc3545"
                daily_label = ttk.Label(left_pane, text=f"Daily Time Left: {daily_remaining_text}", 
                                      font=("Helvetica", 14, "bold"), anchor=tk.CENTER, 
                                      background="#23272b", foreground=daily_color)
                daily_label.pack(pady=(0, 8), fill=tk.X)
                
                # Store label for updating
                self.daily_remaining_label = daily_label

            # --- Timer and Start/Stop Button ---
            self.kid_timer_label = ttk.Label(left_pane, text="00:00:00", font=("Helvetica", 16, "bold"), background="#23272b", foreground="#fff")
            self.kid_timer_label.pack(pady=(0, 10))
            self.kid_timer_running = False
            self.kid_timer_start_time = None
            self.kid_timer_elapsed = 0
            self.kid_timer_job = None
            self.kid_session_tree = None  # Reference to the session log Treeview

            # Load session logs from Firestore
            if self.db is not None:
                self.kid_session_log = get_session_logs_for_user(self.db, self.current_kid_user.get('id'))
            else:
                self.kid_session_log = []

            def start_stop_timer():
                if not self.kid_timer_running:
                    # --- LUNCH & TEETH ROUTINE ---
                    if self._is_lunch_routine_required():
                        return # Stop if routine is required and not completed
                    
                    # --- ENFORCE ALLOWED PLAY HOURS ---
                    user = self.current_kid_user
                    if user:
                        allowed_start = user.get('allowed_start_time', '15:00')
                        allowed_end = user.get('allowed_end_time', '18:00')
                        try:
                            now = datetime.now().time()
                            allowed_start_time = datetime.strptime(allowed_start, '%H:%M').time()
                            allowed_end_time = datetime.strptime(allowed_end, '%H:%M').time()
                            if allowed_start_time <= allowed_end_time:
                                in_range = allowed_start_time <= now <= allowed_end_time
                            else:
                                # Overnight range (e.g., 22:00-06:00)
                                in_range = now >= allowed_start_time or now <= allowed_end_time
                            if not in_range:
                                messagebox.showwarning("Not Allowed", f"You can only play between {allowed_start} and {allowed_end}.")
                                return
                        except Exception:
                            messagebox.showwarning("Not Allowed", f"Allowed play hours are not set correctly.")
                            return
                    
                    # --- START SESSION NOTIFICATIONS (in a separate thread) ---
                    username = self.current_kid_user.get('username', 'Kid')
                    sounds_enabled = self.sound_notifications_enabled.get()
                    threading.Thread(
                        target=self._send_start_session_notifications_threaded,
                        args=(username, sounds_enabled),
                        daemon=True
                    ).start()
                    logger.debug("Started notification thread for session start.")
                    
                    self.kid_timer_running = True
                    self.kid_timer_start_time = datetime.now()
                    self.kid_timer_button.text = "Stop"
                    self.kid_timer_button.colors = self.timer_button_colors_stop
                    self.kid_timer_button._draw(self.kid_timer_button.colors["bg"], self.kid_timer_button.colors["text"])
                    self._update_kid_timer()
                else:
                    self.kid_timer_running = False
                    if self.kid_timer_job:
                        self.after_cancel(self.kid_timer_job)
                        self.kid_timer_job = None
                    end_time = datetime.now()
                    start_time = self.kid_timer_start_time
                    session_entry = None
                    if start_time is not None:
                        duration = (end_time - start_time).total_seconds()
                        session_entry = {
                            'start': start_time.strftime('%Y-%m-%d %H:%M:%S'),
                            'stop': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                            'duration': str(self._format_duration(duration))
                        }
                        # Add to Firestore
                        if self.current_kid_user is not None:
                            add_session_log_to_user(self.db, self.current_kid_user.get('id'), session_entry)
                            
                            # --- SEND STOP NOTIFICATIONS (in a separate thread) ---
                            username = self.current_kid_user.get('username', 'Kid')
                            sounds_enabled = self.sound_notifications_enabled.get()
                            threading.Thread(
                                target=self._send_stop_session_notifications_threaded,
                                args=(username, str(self._format_duration(duration)), sounds_enabled),
                                daemon=True
                            ).start()
                            logger.debug("Started notification thread for session stop.")
                            
                            # Check daily limit after adding session
                            user_id = self.current_kid_user.get('id')
                            if user_id:
                                username = self.current_kid_user.get('username', 'Kid')
                                self._check_daily_limit(user_id, username)
                    # Remove previous log table and button frames
                    if hasattr(self, '_parent_log_widgets'):
                        for w in self._parent_log_widgets:
                            try:
                                w.destroy()
                            except Exception:
                                pass
                    # Update local log and refresh (always do this after session ends)
                    if session_entry is not None:
                        self.kid_session_log.append(session_entry)
                    self._refresh_kid_session_log(right_pane)
                    # Update daily time remaining after session ends
                    self._update_daily_time_display()
                    self.kid_timer_label.config(text="00:00:00")
                    self.kid_timer_button.text = "Start"
                    self.kid_timer_button.colors = self.timer_button_colors_start
                    self.kid_timer_button._draw(self.kid_timer_button.colors["bg"], self.kid_timer_button.colors["text"])
                    self.kid_timer_start_time = None

            # Use a round button for Start/Stop
            self.timer_button_colors_start = {
                "bg": "#28a745",  # green
                "text": "#fff",
                "hover_bg": "#218838",
                "press_bg": "#1e7e34",
                "parent_bg": "#23272b"
            }
            self.timer_button_colors_stop = {
                "bg": "#dc3545",  # red
                "text": "#fff",
                "hover_bg": "#c82333",
                "press_bg": "#bd2130",
                "parent_bg": "#23272b"
            }
            self.kid_timer_button = RoundedButton(left_pane, "Start", start_stop_timer, self.timer_button_colors_start, radius=20, height=40)
            self.kid_timer_button.pack(pady=(0, 10))

            def _update_kid_timer():
                if self.kid_timer_running and self.kid_timer_start_time is not None:
                    elapsed = (datetime.now() - self.kid_timer_start_time).total_seconds()
                    self.kid_timer_label.config(text=self._format_duration(elapsed))
                    # Update daily time remaining display
                    self._update_daily_time_display()
                    self.kid_timer_job = self.after(1000, _update_kid_timer)
            self._update_kid_timer = _update_kid_timer

            def _format_duration(seconds):
                seconds = int(seconds)
                h = seconds // 3600
                m = (seconds % 3600) // 60
                s = seconds % 60
                return f"{h:02}:{m:02}:{s:02}"
            self._format_duration = _format_duration

            def _refresh_kid_session_log(parent):
                if self.kid_session_tree is not None:
                    self.kid_session_tree.destroy()
                    self.kid_session_tree = None
                tree = ttk.Treeview(parent, columns=("start", "stop", "duration"), show="headings", height=8)
                tree.heading("start", text="Start Time")
                tree.heading("stop", text="Stop Time")
                tree.heading("duration", text="Duration")
                # Sort sessions by start time descending
                def parse_start(entry):
                    try:
                        return datetime.strptime(entry["start"], '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        return datetime.min
                for entry in sorted(self.kid_session_log, key=parse_start, reverse=True):
                    tree.insert("", "end", values=(entry["start"], entry["stop"], entry["duration"]))
                tree.pack(anchor=tk.NW, fill=tk.X, pady=(10, 0))
                self.kid_session_tree = tree
            self._refresh_kid_session_log = _refresh_kid_session_log

            # --- Populate Right Pane ---
            self._refresh_kid_session_log(right_pane)

            # --- Buttons Frame (now above the table) ---
            btn_frame = ttk.Frame(self.session_log_frame)
            btn_frame.pack(pady=(10, 0))

            # Add Entry button
            def add_entry():
                if not self.db:
                    messagebox.showerror("Database Error", "Database connection is not available.")
                    return
                selected_kid_name = self.filter_var.get() if hasattr(self, 'filter_var') else "All"
                if selected_kid_name == "All" or not selected_kid_name:
                    messagebox.showwarning("Select Kid", "Please select a specific kid to add an entry.")
                    return
                users = list_users(self.db) if self.db else []
                user = next((u for u in users if u.get('role') == ROLE_KID and u.get('username', 'Kid') == selected_kid_name), None)
                if not user:
                    messagebox.showerror("User Not Found", f"Could not find user '{selected_kid_name}'.")
                    return
                ManualEntryDialog(self, user, self.db, self.show_logs_for_kid)

            # Delete All Entries button
            def delete_all_entries():
                if not self.db:
                    messagebox.showerror("Database Error", "Database connection is not available.")
                    return
                selected_kid_name = self.filter_var.get() if hasattr(self, 'filter_var') else "All"
                if selected_kid_name == "All" or not selected_kid_name:
                    messagebox.showwarning("Select Kid", "Please select a specific kid to delete all entries.")
                    return
                users = list_users(self.db) if self.db else []
                user = next((u for u in users if u.get('role') == ROLE_KID and u.get('username', 'Kid') == selected_kid_name), None)
                if not user:
                    messagebox.showerror("User Not Found", f"Could not find user '{selected_kid_name}'.")
                    return
                user_id = user.get('id')
                if not messagebox.askyesno("Confirm Delete All", f"Are you sure you want to delete ALL session entries for '{selected_kid_name}'?"):
                    return
                user_doc = self.db.collection(USERS_COLLECTION).document(user_id)
                user_doc.update({'sessions': []})
                self.show_logs_for_kid()

            ttk.Button(btn_frame, text="Add Entry", command=add_entry).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="Delete All Entries", command=delete_all_entries).pack(side=tk.LEFT, padx=5)

            # Add Refresh button
            ttk.Button(btn_frame, text="Refresh", command=self.show_logs_for_kid).pack(side=tk.LEFT, padx=5)

    def _send_start_session_notifications_threaded(self, username: str, sounds_enabled: bool):
        """Sends session start notifications in a separate thread to avoid UI lag."""
        logger.debug(f"Executing notification thread for session start for user: {username}")
        self._show_tray_notification(
            f"Session Started",
            f"User: {username}",
            sound_name='start.wav',
            sounds_enabled=sounds_enabled
        )
        self.send_session_started_email(username)

    def _send_stop_session_notifications_threaded(self, username: str, duration: str, sounds_enabled: bool):
        """Sends session stop notifications in a separate thread to avoid UI lag."""
        logger.debug(f"Executing notification thread for session stop for user: {username}")
        self._show_tray_notification(
            f"Session Stopped",
            f"User: {username}\nDuration: {duration}",
            sound_name='stop.wav',
            sounds_enabled=sounds_enabled
        )
        self.send_session_stopped_email(username, duration)

    def _is_lunch_routine_required(self) -> bool:
        """
        Checks if the lunch and teeth brushing routine should be enforced.
        Returns True if the routine is required and prevents the timer from starting, False otherwise.
        """
        user = self.current_kid_user
        if not user or not user.get('enforce_lunch_routine', False):
            return False

        try:
            now = datetime.now().time()
            lunch_start = datetime.strptime(user.get('lunch_start_time', '12:00'), '%H:%M').time()
            lunch_end = datetime.strptime(user.get('lunch_end_time', '13:00'), '%H:%M').time()
        except ValueError:
            return False # Invalid time format, skip routine

        # Only run the routine during lunch hours
        if not (lunch_start <= now <= lunch_end):
            return False

        user_id = user.get('id')
        today = datetime.now().date()
        
        # Check if lunch has already been confirmed today
        if self.lunch_confirmed_today.get(user_id) == today:
            # Lunch confirmed, now check for teeth brushing
            if not messagebox.askyesno("Brush Your Teeth!", "Have you brushed your teeth?"):
                messagebox.showinfo("Reminder", "Please brush your teeth before playing.")
                return True # Prevent timer start
            else:
                # Teeth brushed, can play
                return False

        # Lunch not yet confirmed today, ask about it
        if not messagebox.askyesno("Lunch Time!", "Have you had lunch yet?"):
            # If "No", they can play.
            return False
        else:
            # If "Yes", mark lunch as confirmed for today
            self.lunch_confirmed_today[user_id] = today
            # Now, ask about teeth brushing immediately
            if not messagebox.askyesno("Brush Your Teeth!", "Great! Now, have you brushed your teeth?"):
                messagebox.showinfo("Reminder", "Please brush your teeth before playing.")
                return True # Prevent timer start
            else:
                # Teeth brushed, can play
                return False
                
    def _set_avatar_placeholder(self, label, text):
        """Sets a placeholder image and text on an avatar label."""
        # Create a blank image to enforce size on the ttk.Label, if not already created.
        if not self.kid_view_placeholder_image:
            self.kid_view_placeholder_image = tk.PhotoImage(width=150, height=150)
        label.config(image=self.kid_view_placeholder_image, text=text, compound=tk.CENTER)

    def load_config(self) -> str:
        """Loads configuration from config.json, returns the theme name."""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                
                # Load tray setting if present
                if 'close_to_tray' in config:
                    self.close_to_tray_enabled.set(config['close_to_tray'])
                
                # Load sound notifications setting if present
                if 'sound_notifications' in config:
                    self.sound_notifications_enabled.set(config['sound_notifications'])

                # Load last selected kid if present
                if 'last_selected_kid_id' in config:
                    self.last_selected_kid_id = config['last_selected_kid_id']
                
                # Load last view if present
                if 'last_view' in config:
                    self.last_view = config['last_view']
                
                # Load email settings if present
                if 'email_enabled' not in config:
                    config['email_enabled'] = False
                if 'email_address' not in config:
                    config['email_address'] = ""
                if 'email_password' not in config:
                    config['email_password'] = ""
                if 'email_recipients' not in config:
                    config['email_recipients'] = []
                return config.get('theme', 'light')
        except (FileNotFoundError, json.JSONDecodeError):
            return 'light' # Default theme

    def _load_tray_setting(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                if 'close_to_tray' in config:
                    self.close_to_tray_enabled.set(config['close_to_tray'])
                if 'sound_notifications' in config:
                    self.sound_notifications_enabled.set(config['sound_notifications'])
        except Exception:
            pass

    def save_config(self):
        """Saves the current configuration to config.json."""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            config = {}
        
        config.update({
            'theme': self.current_theme, 
            'close_to_tray': self.close_to_tray_enabled.get(),
            'sound_notifications': self.sound_notifications_enabled.get(),
            'last_selected_kid_id': self.last_selected_kid_id,
            'last_view': self.last_view
        })
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)

    def save_email_config(self, email_enabled: bool, email_address: str, email_password: str, email_recipients: List[str]):
        """Saves email configuration to config.json."""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            config = {}
        
        config.update({
            'email_enabled': email_enabled,
            'email_address': email_address,
            'email_password': email_password,
            'email_recipients': email_recipients
        })
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)

    def get_email_config(self) -> Dict[str, Any]:
        """Gets email configuration from config.json."""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                return {
                    'email_enabled': config.get('email_enabled', False),
                    'email_address': config.get('email_address', ''),
                    'email_password': config.get('email_password', ''),
                    'email_recipients': config.get('email_recipients', [])
                }
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'email_enabled': False,
                'email_address': '',
                'email_password': '',
                'email_recipients': []
            }

    def send_email_notification(self, subject: str, message: str, html_message: Optional[str] = None):
        """Sends email notification using Gmail SMTP."""
        email_config = self.get_email_config()
        
        if not email_config['email_enabled'] or not email_config['email_address'] or not email_config['email_password']:
            return False
        
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = email_config['email_address']
            msg['Subject'] = f"Game Sentry - {subject}"
            
            # Add recipients
            recipients = email_config['email_recipients']
            if not recipients:
                return False
            msg['To'] = ', '.join(recipients)
            
            # Add text and HTML parts
            text_part = MIMEText(message, 'plain')
            msg.attach(text_part)
            
            if html_message:
                html_part = MIMEText(html_message, 'html')
                msg.attach(html_part)
            
            # Send email via Gmail SMTP
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(email_config['email_address'], email_config['email_password'])
            
            text = msg.as_string()
            server.sendmail(email_config['email_address'], recipients, text)
            server.quit()
            
            print(f"Email notification sent: {subject}")
            return True
            
        except Exception as e:
            print(f"Error sending email notification: {e}")
            return False

    def send_daily_limit_email(self, username: str, daily_limit: int, current_usage: int):
        """Sends a formatted email notification for daily limit reached."""
        subject = "Daily Gaming Limit Reached"
        
        # Plain text message
        message = f"""
Game Sentry Alert

{username} has reached their daily gaming limit!

Daily Limit: {daily_limit} minutes
Current Usage: {current_usage} minutes
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

This is an automated notification from Game Sentry.
        """.strip()
        
        # HTML message
        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #dc3545; color: white; padding: 15px; border-radius: 5px; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-top: 10px; }}
        .limit {{ color: #dc3545; font-weight: bold; }}
        .footer {{ margin-top: 20px; font-size: 12px; color: #6c757d; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>🎮 Game Sentry Alert</h2>
    </div>
    <div class="content">
        <h3>{username} has reached their daily gaming limit!</h3>
        <p><strong>Daily Limit:</strong> <span class="limit">{daily_limit} minutes</span></p>
        <p><strong>Current Usage:</strong> <span class="limit">{current_usage} minutes</span></p>
        <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <div class="footer">
        <p>This is an automated notification from Game Sentry.</p>
    </div>
</body>
</html>
        """
        
        return self.send_email_notification(subject, message, html_message)

    def send_session_started_email(self, username: str):
        """Sends a formatted email notification for session start."""
        subject = "Gaming Session Started"
        
        # Plain text message
        message = f"""
Game Sentry Alert

{username} has started a new gaming session.

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

This is an automated notification from Game Sentry.
        """.strip()
        
        # HTML message
        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #28a745; color: white; padding: 15px; border-radius: 5px; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-top: 10px; }}
        .footer {{ margin-top: 20px; font-size: 12px; color: #6c757d; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>🎮 Game Sentry Alert</h2>
    </div>
    <div class="content">
        <h3>{username} has started a new gaming session.</h3>
        <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <div class="footer">
        <p>This is an automated notification from Game Sentry.</p>
    </div>
</body>
</html>
        """
        
        return self.send_email_notification(subject, message, html_message)

    def send_session_stopped_email(self, username: str, duration: str):
        """Sends a formatted email notification for session stop."""
        subject = "Gaming Session Stopped"
        
        # Plain text message
        message = f"""
Game Sentry Alert

{username} has stopped their gaming session.

Duration: {duration}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

This is an automated notification from Game Sentry.
        """.strip()
        
        # HTML message
        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #ffc107; color: #212529; padding: 15px; border-radius: 5px; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-top: 10px; }}
        .footer {{ margin-top: 20px; font-size: 12px; color: #6c757d; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>🎮 Game Sentry Alert</h2>
    </div>
    <div class="content">
        <h3>{username} has stopped their gaming session.</h3>
        <p><strong>Duration:</strong> {duration}</p>
        <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <div class="footer">
        <p>This is an automated notification from Game Sentry.</p>
    </div>
</body>
</html>
        """
        
        return self.send_email_notification(subject, message, html_message)

    def apply_theme(self, theme_name: str):
        """Applies the specified theme to the entire application."""
        self.current_theme = theme_name
        self.theme_colors = self.themes[theme_name]

        # Update button colors dictionary with more vibrant colors
        if theme_name == "light":
            self.button_colors = {
                "bg": "#2563EB",  # More vibrant blue
                "text": "#FFFFFF",
                "hover_bg": "#1D4ED8",  # Darker blue on hover
                "press_bg": "#1E40AF",  # Even darker on press
                "parent_bg": self.theme_colors["primary"]
            }
        else:  # dark theme
            self.button_colors = {
                "bg": "#7C3AED",  # Vibrant purple
                "text": "#FFFFFF",
                "hover_bg": "#6D28D9",  # Darker purple on hover
                "press_bg": "#5B21B6",  # Even darker on press
                "parent_bg": self.theme_colors["primary"]
            }

        # Configure main window background
        self.configure(bg=self.theme_colors["background"])

        # Configure ttk styles
        style = ttk.Style(self)
        style.theme_use('clam') # Use a theme that allows more customization
        style.configure('TFrame', background=self.theme_colors["background"])
        style.configure('Primary.TFrame', background=self.theme_colors["primary"])
        style.configure('Background.TFrame', background=self.theme_colors["background"])
        style.configure('TLabel', background=self.theme_colors["background"], foreground=self.theme_colors["text"])
        style.configure('Primary.TLabel', background=self.theme_colors["primary"], foreground=self.theme_colors["text"])
        style.configure('Accent.TSeparator', background=self.theme_colors["accent"])
        style.configure('Treeview', background=self.theme_colors["widget_bg"], foreground=self.theme_colors["widget_fg"], fieldbackground=self.theme_colors["widget_bg"], borderwidth=0)
        style.configure('Treeview.Heading', background=self.theme_colors["list_header_bg"], foreground=self.theme_colors["text"], font=('Helvetica', 10, 'bold'))
        style.map('Treeview.Heading', background=[('active', self.theme_colors["primary"])])
        style.configure('TCombobox', fieldbackground=self.theme_colors["widget_bg"], background=self.theme_colors["widget_bg"], foreground=self.theme_colors["widget_fg"], arrowcolor=self.theme_colors["text"], selectbackground=self.theme_colors["widget_bg"], selectforeground=self.theme_colors["widget_fg"])
        style.configure('TSpinbox', fieldbackground=self.theme_colors["widget_bg"], background=self.theme_colors["widget_bg"], foreground=self.theme_colors["widget_fg"], arrowcolor=self.theme_colors["text"])
        style.configure('TEntry', fieldbackground=self.theme_colors["widget_bg"], foreground=self.theme_colors["widget_fg"], insertcolor=self.theme_colors["text"])
        style.configure('TLabelframe', background=self.theme_colors["background"], foreground=self.theme_colors["text"])
        style.configure('TLabelframe.Label', background=self.theme_colors["background"], foreground=self.theme_colors["text"])
        style.configure('TPanedwindow', background=self.theme_colors['background'])
        style.configure('TPanedwindow.Sash', background=self.theme_colors['accent'])
        style.configure('Custom.TCombobox',
            fieldbackground=self.theme_colors["widget_bg"],
            background=self.theme_colors["widget_bg"],
            foreground=self.theme_colors["widget_fg"],
            selectbackground=self.theme_colors["primary"],
            selectforeground=self.theme_colors["text"],
            postbackground="#FFFFFF"  # Set dropdown menu background to white
        )

        # Redraw top bar buttons with new theme colors
        for button in self.top_bar_buttons:
            button.colors = self.button_colors
            button.configure(bg=self.button_colors["parent_bg"])
            button._on_leave(None) # Redraw with default state

    def change_theme(self, theme_name: str):
        """Changes the theme, applies it, and saves the configuration."""
        self.apply_theme(theme_name)
        self.save_config()

    # --- View Switching and Button Commands ---

    def switch_to_parent_view(self):
        """Switches the view back to the parent dashboard."""
        self.current_view = ROLE_PARENT
        self.current_kid_user = None
        self.last_view = ROLE_PARENT  # Remember we were in parent view
        self._update_ui_for_view()
        # Save config immediately after setting the values
        self.save_config()
        # Force refresh of session logs after switching to parent view
        self.show_logs_for_kid()

    def switch_to_kid_view(self):
        """Switches to kid view, automatically loading the last selected kid if available."""
        if self.last_selected_kid_id and self.db:
            # Try to load the last selected kid
            last_kid = get_user(self.db, self.last_selected_kid_id)
            if last_kid:
                self.current_view = ROLE_KID
                self.current_kid_user = last_kid
                self.last_view = ROLE_KID  # <-- Add this line
                self.save_config()         # <-- And this line
                self._update_ui_for_view()
                return
        
        # If no last kid or last kid not found, show selection dialog
        KidSelectionDialog(self, self.db, self._on_kid_selected)

    def _on_kid_selected(self, kid_user: Dict[str, Any]):
        """Callback function for when a kid is selected from the dialog."""
        if kid_user:
            self.current_view = ROLE_KID
            self.current_kid_user = kid_user
            self.last_selected_kid_id = kid_user.get('id')  # Remember this kid
            self.last_view = ROLE_KID  # Remember we were in kid view
            self._update_ui_for_view()
            # Save config immediately after setting the values
            self.save_config()

    def edit_profile(self):
        """Opens the user management window to edit the current kid's profile."""
        if self.current_kid_user and self.db:
            user_manager = UserManagementWindow(self, self.db)
            # Find the user in the treeview and trigger the edit function
            for item in user_manager.tree.get_children():
                user_id = user_manager.tree.item(item)['values'][2]
                if user_id == self.current_kid_user.get('id'):
                    user_manager.tree.selection_set(item)
                    user_manager.edit_selected_user()
                    break

    def manage_users(self):
        """Opens the user management screen."""
        if self.db:
            UserManagementWindow(self, self.db)
        else:
            messagebox.showerror("Database Error", "The database connection is not available.")

    def open_help(self):
        """Opens a help dialog or screen."""
        print("Button 'Help' clicked.")

    def open_settings(self):
        """Opens the application settings screen."""
        SettingsWindow(self)

    def _initialize_tray_icon(self):
        """Initializes and runs the system tray icon in a separate thread."""
        def on_activate(icon):
            self.after(0, self._restore_window)

        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'pictures', 'gtt_logo.png')
            image = PILImage.open(icon_path)
            image = image.resize((64, 64))
        except Exception:
            image = PILImage.new('RGB', (64, 64), color='gray')

        menu = pystray.Menu(
            pystray.MenuItem('Show', on_activate, default=True),
            pystray.MenuItem('Quit', self._quit_from_tray)
        )
        self.tray_icon = pystray.Icon("Game Sentry", image, "Game Sentry", menu)
        
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _on_minimize(self, event):
        # Minimize to tray when window is minimized
        if self.state() == 'iconic' and self.close_to_tray_enabled.get():
            self.withdraw()

    def _on_close(self):
        if self.close_to_tray_enabled.get():
            self.withdraw()
        else:
            self.destroy()

    def _restore_window(self):
        self.deiconify()
        self.after(0, self.lift)

    def _quit_from_tray(self, icon, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()

    def destroy(self):
        # Release the mutex when the application closes
        if hasattr(self, 'mutex'):
            win32api.CloseHandle(self.mutex)
        super().destroy()

    def _switch_kid_profile(self):
        """Opens the KidSelectionDialog to switch to another kid profile in Kid view."""
        KidSelectionDialog(self, self.db, self._on_kid_selected)

    def _initialize_to_last_view(self):
        """Initialize the app to the last view (parent or kid) if available."""
        if self.last_view == ROLE_KID and self.last_selected_kid_id and self.db:
            # Try to load the last selected kid
            last_kid = get_user(self.db, self.last_selected_kid_id)
            if last_kid:
                self.current_view = ROLE_KID
                self.current_kid_user = last_kid
                return
        
        # Default to parent view if no last view, last kid not found, or last view was parent
        self.current_view = ROLE_PARENT
        self.current_kid_user = None

    def _calculate_daily_remaining(self, user_id: str) -> int:
        """Calculate remaining daily time in seconds for a user."""
        if not self.db:
            return 0
        
        try:
            user_data = get_user(self.db, user_id)
            if not user_data:
                return 0
            
            daily_limit = user_data.get('max_daily_minutes')
            if daily_limit is None:
                return 0
            
            # Convert to seconds
            daily_limit_seconds = daily_limit * 60
            
            current_usage_seconds = self._calculate_daily_usage(user_id)
            
            remaining = daily_limit_seconds - current_usage_seconds
            return max(0, remaining)  # Don't return negative values
            
        except Exception as e:
            print(f"Error calculating daily remaining for user {user_id}: {e}")
            return 0

    def _format_time_remaining(self, seconds: int) -> str:
        """Format remaining time showing minutes and seconds."""
        if seconds <= 0:
            return "No time left"
        elif seconds < 3600:
            # Less than 1 hour: show minutes and seconds
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            return f"{minutes}m {remaining_seconds:02d}s"
        else:
            # 1 hour or more: show hours, minutes, and seconds
            hours = seconds // 3600
            remaining_seconds = seconds % 3600
            minutes = remaining_seconds // 60
            final_seconds = remaining_seconds % 60
            return f"{hours}h {minutes:02d}m {final_seconds:02d}s"

    def _update_daily_time_display(self):
        """Updates the daily remaining time display."""
        if not hasattr(self, 'current_kid_user') or not self.current_kid_user:
            return
        
        user_id = self.current_kid_user.get('id')
        if not user_id:
            return
        
        try:
            daily_remaining = self._calculate_daily_remaining(user_id)
            
            # Update daily remaining
            if hasattr(self, 'daily_remaining_label'):
                daily_remaining_text = self._format_time_remaining(daily_remaining)
                daily_color = "#28a745" if daily_remaining > 3600 else "#ffc107" if daily_remaining > 1800 else "#dc3545"
                self.daily_remaining_label.config(text=f"Daily Time Left: {daily_remaining_text}", foreground=daily_color)
                
        except Exception as e:
            print(f"Error updating daily time display: {e}")

    def _calculate_daily_usage(self, user_id: str) -> int:
        """Calculate total daily usage in seconds for a user."""
        if not self.db:
            return 0
        
        try:
            sessions = get_session_logs_for_user(self.db, user_id)
            today = datetime.now().date()
            total_seconds = 0
            
            for session in sessions:
                try:
                    # Parse session start time
                    start_time = datetime.strptime(session.get('start', ''), '%Y-%m-%d %H:%M:%S')
                    if start_time.date() == today:
                        # Parse duration (format: HH:MM:SS)
                        duration_str = session.get('duration', '00:00:00')
                        time_parts = duration_str.split(':')
                        if len(time_parts) == 3:
                            hours = int(time_parts[0])
                            minutes = int(time_parts[1])
                            seconds = int(time_parts[2])
                            session_seconds = hours * 3600 + minutes * 60 + seconds
                            total_seconds += session_seconds
                except (ValueError, IndexError):
                    continue
            
            # Add current session time if timer is running
            if hasattr(self, 'kid_timer_running') and self.kid_timer_running and self.kid_timer_start_time:
                current_user_id = self.current_kid_user.get('id') if self.current_kid_user else None
                if current_user_id == user_id:
                    elapsed_seconds = int((datetime.now() - self.kid_timer_start_time).total_seconds())
                    total_seconds += elapsed_seconds
            
            return total_seconds
        except Exception as e:
            print(f"Error calculating daily usage for user {user_id}: {e}")
            return 0

    def _calculate_block_usage(self, user_id: str) -> int:
        """Calculate current block usage in seconds for a user (resets after break period)."""
        if not self.db:
            return 0
        
        try:
            user_data = get_user(self.db, user_id)
            if not user_data:
                return 0
            
            # Check if user is in break period
            if self._is_user_in_rest(user_id):
                return 0  # Block resets after break period
            
            sessions = get_session_logs_for_user(self.db, user_id)
            today = datetime.now().date()
            total_seconds = 0
            
            # Find the start of current block (after last rest period)
            last_rest_end = self._get_last_rest_end_time(user_id)
            
            for session in sessions:
                try:
                    # Parse session start time
                    start_time = datetime.strptime(session.get('start', ''), '%Y-%m-%d %H:%M:%S')
                    if start_time.date() == today and start_time >= last_rest_end:
                        # Parse duration (format: HH:MM:SS)
                        duration_str = session.get('duration', '00:00:00')
                        time_parts = duration_str.split(':')
                        if len(time_parts) == 3:
                            hours = int(time_parts[0])
                            minutes = int(time_parts[1])
                            seconds = int(time_parts[2])
                            session_seconds = hours * 3600 + minutes * 60 + seconds
                            total_seconds += session_seconds
                except (ValueError, IndexError):
                    continue
            
            # Add current session time if timer is running
            if hasattr(self, 'kid_timer_running') and self.kid_timer_running and self.kid_timer_start_time:
                current_user_id = self.current_kid_user.get('id') if self.current_kid_user else None
                if current_user_id == user_id:
                    elapsed_seconds = int((datetime.now() - self.kid_timer_start_time).total_seconds())
                    total_seconds += elapsed_seconds
            
            return total_seconds
        except Exception as e:
            print(f"Error calculating block usage for user {user_id}: {e}")
            return 0

    def _get_last_rest_end_time(self, user_id: str) -> datetime:
        """Get the end time of the last rest period for a user."""
        if user_id in self.kid_rest_periods:
            rest_data = self.kid_rest_periods[user_id]
            start_time = rest_data['start_time']
            duration_minutes = rest_data['duration_minutes']
            end_time = start_time + timedelta(minutes=duration_minutes)
            return end_time
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)  # Start of today

    def _is_user_in_rest(self, user_id: str) -> bool:
        """Check if a user is currently in a rest period."""
        if user_id not in self.kid_rest_periods:
            return False
        
        rest_data = self.kid_rest_periods[user_id]
        start_time = rest_data['start_time']
        duration_minutes = rest_data['duration_minutes']
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        return datetime.now() < end_time

    def _get_rest_remaining_seconds(self, user_id: str) -> int:
        """Get remaining rest time in seconds for a user."""
        if not self._is_user_in_rest(user_id):
            return 0
        
        rest_data = self.kid_rest_periods[user_id]
        start_time = rest_data['start_time']
        duration_minutes = rest_data['duration_minutes']
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        remaining = (end_time - datetime.now()).total_seconds()
        return max(0, int(remaining))

    def _start_rest_period(self, user_id: str, duration_minutes: int = 60):
        """Start a break period for a user."""
        self.kid_rest_periods[user_id] = {
            'start_time': datetime.now(),
            'duration_minutes': duration_minutes
        }
        print(f"Started {duration_minutes}-minute break period for user {user_id}")

    def _check_block_limit(self, user_id: str, username: str):
        """Check if user has reached play time limit and start break period if needed."""
        if not self.db:
            return
        
        try:
            user_data = get_user(self.db, user_id)
            if not user_data:
                return
            
            block_limit = user_data.get('max_session_minutes')  # Using play time limit as block limit
            enforce_rest = user_data.get('enforce_rest', True)  # Default to True
            
            if block_limit is None or not enforce_rest:
                return
            
            current_block_usage = self._calculate_block_usage(user_id)
            block_limit_seconds = block_limit * 60
            
            # Create a unique key for today's block notification
            today_key = f"block_{user_id}_{datetime.now().date()}"
            
            # Check if we've already notified for this user today
            if today_key in self.block_limit_notifications:
                return
            
            # Check if play time limit is reached
            if current_block_usage >= block_limit_seconds:
                # Start break period (default 60 minutes)
                rest_duration = user_data.get('rest_duration_minutes', 60)
                self._start_rest_period(user_id, rest_duration)
                
                # Show tray notification
                sounds_enabled = self.sound_notifications_enabled.get()
                self._show_tray_notification(
                    f"Play Time Limit Reached!",
                    f"{username} has reached their play time limit of {block_limit} minutes.\n"
                    f"Break time started: {rest_duration} minutes",
                    sound_name='warning.wav',
                    sounds_enabled=sounds_enabled
                )
                
                # Send email notification
                self.send_block_limit_email(username, block_limit, current_block_usage // 60, rest_duration)
                
                self.block_limit_notifications.add(today_key)
                
        except Exception as e:
            print(f"Error checking block limit for user {user_id}: {e}")

    def _check_rest_period_end(self, user_id: str, username: str):
        """Check if break period has ended and notify."""
        if not self._is_user_in_rest(user_id):
            return
        
        rest_remaining = self._get_rest_remaining_seconds(user_id)
        if rest_remaining <= 0:
            # Break period ended
            if user_id in self.kid_rest_periods:
                del self.kid_rest_periods[user_id]
            
            # Show tray notification
            self._show_tray_notification(
                f"Break Time Ended!",
                f"{username} can now start gaming again.\n"
                f"Play time limit has been reset."
            )
            
            # Send email notification
            self.send_rest_ended_email(username)

    def _check_daily_limit(self, user_id: str, username: str):
        """Check if user has reached daily limit and show notification if needed."""
        if not self.db:
            return
        
        try:
            user_data = get_user(self.db, user_id)
            if not user_data:
                return
            
            daily_limit = user_data.get('max_daily_minutes')
            if daily_limit is None:
                return
            
            current_usage = self._calculate_daily_usage(user_id)
            
            # Create a unique key for today's notification
            today_key = f"{user_id}_{datetime.now().date()}"
            
            # Check if we've already notified for this user today
            if today_key in self.daily_limit_notifications:
                return
            
            # Check if daily limit is reached or exceeded
            if current_usage >= daily_limit:
                # Show tray notification
                sounds_enabled = self.sound_notifications_enabled.get()
                self._show_tray_notification(
                    f"Daily Limit Reached!",
                    f"{username} has reached their daily limit of {daily_limit} minutes.\n"
                    f"Current usage: {current_usage} minutes",
                    sound_name='over.wav',
                    sounds_enabled=sounds_enabled
                )
                
                # Send email notification
                safe_username = username or "Unknown User"
                self.send_daily_limit_email(safe_username, daily_limit, current_usage)
                
                self.daily_limit_notifications.add(today_key)
                
                # Clean up old notifications (older than 1 day)
                self._cleanup_old_notifications()
        
        except Exception as e:
            print(f"Error checking daily limit for user {user_id}: {e}")

    def _cleanup_old_notifications(self):
        """Remove notifications older than 1 day."""
        today = datetime.now().date()
        to_remove = set()
        
        for notification_key in self.daily_limit_notifications:
            try:
                # Extract date from key (format: user_id_YYYY-MM-DD)
                date_str = notification_key.split('_', 1)[1]
                notification_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                
                if (today - notification_date).days > 1:
                    to_remove.add(notification_key)
            except (ValueError, IndexError):
                to_remove.add(notification_key)
        
        self.daily_limit_notifications -= to_remove

    def _show_tray_notification(self, title: str, message: str, sound_name: Optional[str] = None, sounds_enabled: bool = True):
        """Show a notification in the system tray with an optional custom sound."""
        if self.tray_icon:
            try:
                logger.debug(f"Notification sound setting is currently: {self.sound_notifications_enabled.get()}")
                # Play sound only if enabled
                if sounds_enabled:
                    sound_path = None
                    if sound_name:
                        sound_path = os.path.join(self.SOUNDS_DIR, sound_name)
                        logger.debug(f"Attempting to play sound from: {sound_path}")
                        if not os.path.exists(sound_path):
                            logger.warning(f"Sound file not found at: {sound_path}")
                            sound_path = None # Fallback to default

                    # Play a custom sound if provided and exists, otherwise a system sound
                    if sound_path:
                        logger.debug("Playing custom sound.")
                        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    else:
                        logger.debug("Playing default system sound.")
                        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
                
                logger.debug(f"Notifying with title: '{title}' and message: '{message}'")
                self.tray_icon.notify(title, message)
                logger.debug("Notification sent successfully.")
            except Exception as e:
                logger.error(f"Error in _show_tray_notification: {e}", exc_info=True)
                # Fallback: try to show a simple notification without sound
                try:
                    self.tray_icon.notify(title, message[:100])  # Truncate if too long
                except Exception:
                    print("Could not show tray notification")

    def send_block_limit_email(self, username: str, block_limit: int, block_usage: int, rest_duration: int):
        """Send an email notification for play time limit reached."""
        # TODO: Implement email content and sending logic
        pass

    def send_rest_ended_email(self, username: str):
        """Send an email notification when break period ends."""
        # TODO: Implement email content and sending logic
        pass

    def show_logs_for_kid(self, *args):
        logger.debug("Showing logs for kid")
        # Destroy the old logs_table_frame if it exists
        if hasattr(self, 'logs_table_frame') and self.logs_table_frame.winfo_exists():
            self.logs_table_frame.destroy()
        # Now create a new logs_table_frame
        self.logs_table_frame = ttk.Frame(self.session_log_frame)
        self.logs_table_frame.pack(fill=tk.BOTH, expand=True)

        # --- Buttons Frame ---
        btn_frame = ttk.Frame(self.logs_table_frame)
        btn_frame.pack(pady=(10, 0))

        # --- Define local functions for button commands ---
        def add_entry():
            if not self.db:
                messagebox.showerror("Database Error", "Database connection is not available.")
                return
            selected_kid_name = self.filter_var.get() if hasattr(self, 'filter_var') else "All"
            if selected_kid_name == "All" or not selected_kid_name:
                messagebox.showwarning("Select Kid", "Please select a specific kid to add an entry.")
                return
            users = list_users(self.db) if self.db else []
            user = next((u for u in users if u.get('role') == ROLE_KID and u.get('username', 'Kid') == selected_kid_name), None)
            if not user:
                messagebox.showerror("User Not Found", f"Could not find user '{selected_kid_name}'.")
                return
            ManualEntryDialog(self, user, self.db, self.show_logs_for_kid)

        def delete_all_entries():
            if not self.db:
                messagebox.showerror("Database Error", "Database connection is not available.")
                return
            selected_kid_name = self.filter_var.get() if hasattr(self, 'filter_var') else "All"
            if selected_kid_name == "All" or not selected_kid_name:
                messagebox.showwarning("Select Kid", "Please select a specific kid to delete all entries.")
                return
            users = list_users(self.db) if self.db else []
            user = next((u for u in users if u.get('role') == ROLE_KID and u.get('username', 'Kid') == selected_kid_name), None)
            if not user:
                messagebox.showerror("User Not Found", f"Could not find user '{selected_kid_name}'.")
                return
            user_id = user.get('id')
            if not messagebox.askyesno("Confirm Delete All", f"Are you sure you want to delete ALL session entries for '{selected_kid_name}'?"):
                return
            user_doc = self.db.collection(USERS_COLLECTION).document(user_id)
            user_doc.update({'sessions': []})
            self.show_logs_for_kid()

        ttk.Button(btn_frame, text="Add Entry", command=add_entry).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Delete All Entries", command=delete_all_entries).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=self.show_logs_for_kid).pack(side=tk.LEFT, padx=5)

        # Table Frame
        table_frame = ttk.Frame(self.logs_table_frame)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        tree = ttk.Treeview(table_frame, columns=("username", "start", "stop", "duration"), show="headings", height=12)
        tree.heading("username", text="Username")
        tree.heading("start", text="Start Time")
        tree.heading("stop", text="Stop Time")
        tree.heading("duration", text="Duration")
        tree.pack(fill=tk.BOTH, expand=True)

        # --- Fetch and insert session logs ---
        users = list_users(self.db) if self.db else []
        selected_kid = self.filter_var.get().strip().lower() if hasattr(self, 'filter_var') else "all"
        selected_day = self.date_filter_var.get() if hasattr(self, 'date_filter_var') else ""
        selected_date = None
        if selected_day:
            try:
                selected_date = datetime.strptime(selected_day, '%Y-%m-%d').date()
            except Exception:
                selected_date = None

        all_kids_logs = []
        for user in users:
            if user.get('role') == ROLE_KID:
                username = user.get('username', 'Kid').strip().lower()
                if selected_kid != "all" and username != selected_kid:
                    continue
                sessions = get_session_logs_for_user(self.db, user.get('id'))
                for idx, session in enumerate(sessions):
                    start = session.get('start', '')
                    try:
                        session_dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                        session_date = session_dt.date()
                        if selected_date and session_date != selected_date:
                            continue
                        entry = {
                            'username': user.get('username', 'Kid'),
                            'start': start,
                            'stop': session.get('stop', ''),
                            'duration': session.get('duration', ''),
                            'user_id': user.get('id'),
                            'session_idx': idx,
                            'session': session
                        }
                        all_kids_logs.append(entry)
                    except Exception:
                        continue

        # Sort logs by start time descending
        all_kids_logs.sort(key=lambda x: x['start'], reverse=True)

        # Insert into table
        for entry in all_kids_logs:
            tree.insert("", "end", values=(entry["username"], entry["start"], entry["stop"], entry["duration"]))

        # Show a message if no logs
        if not all_kids_logs:
            no_logs_label = ttk.Label(table_frame, text="No session logs found for the selected date.")
            no_logs_label.pack(pady=10)

    def _actual_show_logs(self, *args):
        """Actual implementation of session log display"""
        try:
            print("[DEBUG] Loading session logs...")
            selected_day = self.date_filter_var.get() if hasattr(self, 'date_filter_var') else ""
            print(f"[DEBUG] Selected date: {selected_day}")
            # Clear previous widgets
            if hasattr(self, '_parent_log_widgets'):
                for w in self._parent_log_widgets:
                    try:
                        w.destroy()
                    except Exception:
                        pass
            self._parent_log_widgets = []
            self.main_content_frame.update_idletasks()
            # Parse selected date
            selected_date = None
            if selected_day:
                try:
                    selected_date = datetime.strptime(selected_day, '%Y-%m-%d').date()
                except Exception as e:
                    print(f"Invalid date filter: {e}")
            # Fetch users and filter logs
            users = list_users(self.db) if self.db else []
            selected_kid = self.filter_var.get().strip().lower() if hasattr(self, 'filter_var') else "all"
            all_kids_logs = []
            for user in users:
                if user.get('role') == ROLE_KID:
                    username = user.get('username', 'Kid').strip().lower()
                    if selected_kid != "all" and username != selected_kid:
                        continue
                    # Get fresh session logs
                    sessions = get_session_logs_for_user(self.db, user.get('id'))
                    for idx, session in enumerate(sessions):
                        start = session.get('start', '')
                        try:
                            session_dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                            session_date = session_dt.date()
                            # Apply date filter
                            if selected_date and session_date != selected_date:
                                continue
                            # Create complete entry
                            entry = {
                                'username': user.get('username', 'Kid'),
                                'start': start,
                                'stop': session.get('stop', ''),
                                'duration': session.get('duration', ''),
                                'user_id': user.get('id'),
                                'session_idx': idx,
                                'session': session
                            }
                            all_kids_logs.append(entry)
                        except ValueError as e:
                            print(f"Error parsing session date: {e}")
                            continue
            # Sort logs by start time descending
            all_kids_logs.sort(key=lambda x: x['start'], reverse=True)
            # --- Session Log Frame (dedicated for table and controls) ---
            self.session_log_frame = ttk.Frame(self.main_content_frame)
            self.session_log_frame.pack(fill=tk.BOTH, expand=True)
            self.main_content_frame.update_idletasks()  # Force UI redraw
            # --- Buttons Frame (now above the table) ---
            btn_frame = ttk.Frame(self.session_log_frame)
            btn_frame.pack(pady=(10, 0))
            self._parent_log_widgets.append(btn_frame)
            # Only keep delete/edit buttons
            def delete_selected_log():
                if not self.db:
                    return
                selected = tree.selection()
                if not selected:
                    messagebox.showwarning("No selection", "Please select a session log entry to delete.")
                    return
                iid = selected[0]
                user_id = iid.split(":")[0]
                session_idx = int(iid.split(":")[1])
                if not messagebox.askyesno("Confirm Delete", "Are you sure you want to delete this session log entry?"):
                    return
                user_doc = self.db.collection(USERS_COLLECTION).document(user_id)
                user_data = user_doc.get().to_dict() if user_doc else None
                sessions = user_data.get('sessions', []) if user_data else []
                if 0 <= session_idx < len(sessions):
                    del sessions[session_idx]
                    user_doc.update({'sessions': sessions})
                self.show_logs_for_kid()
            def edit_selected_log():
                if not self.db:
                    return
                selected = tree.selection()
                if not selected:
                    messagebox.showwarning("No selection", "Please select a session log entry to edit.")
                    return
                iid = selected[0]
                user_id = iid.split(":")[0]
                session_idx = int(iid.split(":")[1])
                user_doc = self.db.collection(USERS_COLLECTION).document(user_id)
                user_data = user_doc.get().to_dict() if user_doc else None
                sessions = user_data.get('sessions', []) if user_data else []
                if 0 <= session_idx < len(sessions):
                    session = sessions[session_idx]
                    new_start = simpledialog.askstring("Edit Start Time", "Start Time (YYYY-MM-DD HH:MM:SS):", initialvalue=session.get('start', ''))
                    if new_start is None:
                        return
                    new_stop = simpledialog.askstring("Edit Stop Time", "Stop Time (YYYY-MM-DD HH:MM:SS):", initialvalue=session.get('stop', ''))
                    if new_stop is None:
                        return
                    new_duration = simpledialog.askstring("Edit Duration", "Duration (HH:MM:SS):", initialvalue=session.get('duration', ''))
                    if new_duration is None:
                        return
                    # --- Validation ---
                    try:
                        start_dt = datetime.strptime(new_start, '%Y-%m-%d %H:%M:%S')
                        stop_dt = datetime.strptime(new_stop, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        messagebox.showerror("Invalid Time", "Start and Stop times must be in format YYYY-MM-DD HH:MM:SS.")
                        return
                    if start_dt > stop_dt:
                        messagebox.showerror("Invalid Time Range", "Start time cannot be after stop time.")
                        return
                    # Check for overlap with other sessions
                    for idx, other in enumerate(sessions):
                        if idx == session_idx:
                            continue
                        try:
                            other_start = datetime.strptime(other.get('start', ''), '%Y-%m-%d %H:%M:%S')
                            other_stop = datetime.strptime(other.get('stop', ''), '%Y-%m-%d %H:%M:%S')
                        except Exception:
                            continue
                        # Overlap if: (start < other_stop) and (stop > other_start)
                        if (start_dt < other_stop and stop_dt > other_start):
                            messagebox.showerror("Time Overlap", f"The new time range overlaps with another session (from {other.get('start','')} to {other.get('stop','')}).")
                            return
                    sessions[session_idx] = {'start': new_start, 'stop': new_stop, 'duration': new_duration}
                    # Save updated sessions list to Firestore
                    user_doc.update({'sessions': sessions})
                self.show_logs_for_kid()
            # --- Table Frame ---
            table_frame = ttk.Frame(self.session_log_frame)
            table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
            self._parent_log_widgets.append(table_frame)
            tree = ttk.Treeview(table_frame, columns=("username", "start", "stop", "duration"), show="headings", height=12)
            tree.heading("username", text="Username")
            tree.heading("start", text="Start Time")
            tree.heading("stop", text="Stop Time")
            tree.heading("duration", text="Duration")
            for entry in all_kids_logs:
                tree.insert("", "end", values=(entry["username"], entry["start"], entry["stop"], entry["duration"]), iid=f'{entry["user_id"]}:{entry["session_idx"]}')
            tree.pack(fill=tk.BOTH, expand=True)
            if not all_kids_logs:
                no_logs_label = ttk.Label(table_frame, text="No session logs found for the selected date.")
                no_logs_label.pack(pady=10)
                self._parent_log_widgets.append(no_logs_label)
            ttk.Button(btn_frame, text="Delete Selected", command=delete_selected_log).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="Edit Selected", command=edit_selected_log).pack(side=tk.LEFT, padx=5)
        except Exception as e:
            print(f"Error in _actual_show_logs: {e}")
            messagebox.showerror("Error", f"Could not load session logs: {str(e)}")

class KidSelectionDialog(tk.Toplevel):
    """A dialog window to select a kid user to view their dashboard, with avatars and a modern look."""
    def __init__(self, parent: 'GameSentryApp', db: Any, callback):
        super().__init__(parent)
        self.parent_app = parent
        self.db = db
        self.callback = callback
        self.selected_index = None
        self.card_widgets = []

        self.title("Select Kid Profile")
        self.transient(parent)
        self.grab_set()
        self.configure(bg=self.parent_app.theme_colors['background'])

        # --- Center Window ---
        window_width = 400
        window_height = 500
        position_x = int((parent.winfo_x() + parent.winfo_width() / 2) - (window_width / 2))
        position_y = int((parent.winfo_y() + parent.winfo_height() / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{position_x}+{position_y}")

        # --- Main Frame ---
        main_frame = ttk.Frame(self, padding=15, style='TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Select a profile to load:", font=("Helvetica", 13, "bold"), anchor="w").pack(pady=(0, 10), anchor="w")

        # --- Scrollable Area for Kid Cards ---
        canvas = tk.Canvas(main_frame, bg=self.parent_app.theme_colors['background'], highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, style='TFrame')
        scrollable_frame_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        scrollable_frame.bind("<Configure>", on_frame_configure)

        def resize_canvas(event):
            canvas.itemconfig(scrollable_frame_id, width=event.width)
        canvas.bind("<Configure>", resize_canvas)

        # --- Load Users ---
        self.users = [user for user in list_users(self.db) if user.get('role') == ROLE_KID]
        self.avatar_images = []  # Prevent garbage collection

        card_bg = self.parent_app.theme_colors['widget_bg']
        card_fg = self.parent_app.theme_colors['widget_fg']
        card_highlight = self.parent_app.theme_colors['primary']
        card_selected = self.parent_app.theme_colors['accent']

        def on_card_click(idx, event=None):
            self._select_card(idx)
            # Automatically select the profile when clicked
            self._on_select()

        def on_card_double_click(idx, event=None):
            # Double-click also works for consistency
            self._select_card(idx)
            self._on_select()

        def on_card_enter(idx, event=None):
            if self.selected_index != idx:
                self.card_widgets[idx].configure(bg=card_highlight)

        def on_card_leave(idx, event=None):
            if self.selected_index != idx:
                self.card_widgets[idx].configure(bg=card_bg)

        for idx, user in enumerate(self.users):
            frame = tk.Frame(scrollable_frame, bg=card_bg, bd=2, relief="ridge", highlightthickness=0)
            frame.pack(fill=tk.X, pady=6, padx=2)
            frame.bind("<Button-1>", lambda e, i=idx: on_card_click(i))
            frame.bind("<Double-Button-1>", lambda e, i=idx: on_card_double_click(i))
            frame.bind("<Enter>", lambda e, i=idx: on_card_enter(i))
            frame.bind("<Leave>", lambda e, i=idx: on_card_leave(i))

            # Avatar
            avatar_url = user.get('avatar_url')
            avatar_img = None
            if avatar_url:
                try:
                    img = load_image_from_url(avatar_url, (64, 64))
                    if img:
                        avatar_img = ImageTk.PhotoImage(img)
                except Exception:
                    avatar_img = None
            if not avatar_img:
                # Placeholder
                img = Image.new('RGB', (64, 64), color='#bbb')
                avatar_img = ImageTk.PhotoImage(img)
            self.avatar_images.append(avatar_img)
            avatar_label = tk.Label(frame, image=avatar_img, bg=card_bg)
            avatar_label.pack(side=tk.LEFT, padx=10, pady=5)
            avatar_label.bind("<Button-1>", lambda e, i=idx: on_card_click(i))
            avatar_label.bind("<Double-Button-1>", lambda e, i=idx: on_card_double_click(i))
            avatar_label.bind("<Enter>", lambda e, i=idx: on_card_enter(i))
            avatar_label.bind("<Leave>", lambda e, i=idx: on_card_leave(i))

            # Username
            username = user.get('username', 'Unknown')
            name_label = tk.Label(frame, text=username, font=("Helvetica", 15, "bold"), bg=card_bg, fg=card_fg)
            name_label.pack(side=tk.LEFT, padx=10)
            name_label.bind("<Button-1>", lambda e, i=idx: on_card_click(i))
            name_label.bind("<Double-Button-1>", lambda e, i=idx: on_card_double_click(i))
            name_label.bind("<Enter>", lambda e, i=idx: on_card_enter(i))
            name_label.bind("<Leave>", lambda e, i=idx: on_card_leave(i))

            self.card_widgets.append(frame)

        # No need for Select button since single-click automatically selects

    def _select_card(self, idx):
        card_bg = self.parent_app.theme_colors['widget_bg']
        card_selected = self.parent_app.theme_colors['accent']
        for i, frame in enumerate(self.card_widgets):
            if i == idx:
                frame.configure(bg=card_selected)
            else:
                frame.configure(bg=card_bg)
        self.selected_index = idx

    def _on_select(self, event=None):
        if self.selected_index is not None:
            selected_user = self.users[self.selected_index]
            self.callback(selected_user)
            self.destroy()

    def _matches_date_filter(self, session, selected_date):
        if selected_date is None:
            return True
        try:
            session_date = datetime.strptime(session['start'], '%Y-%m-%d %H:%M:%S').date()
            return session_date == selected_date
        except ValueError:
            return False

class UserManagementWindow(tk.Toplevel):
    """
    A Toplevel window for adding, viewing, and deleting users in Firestore.
    """
    def __init__(self, parent, db: Any):
        super().__init__(parent)
        self.parent_app = parent
        self.db = db
        self.editing_user_id: Optional[str] = None
        self.avatars_dir = parent.avatars_dir

        self.title("User Management")
        self.transient(parent) # Keep this window on top of the main app
        self.configure(bg=self.parent_app.theme_colors['background'])
        self.grab_set() # Modal behavior

        # --- Center Window on Screen ---
        window_width = 950
        window_height = 600
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        position_x = int((screen_width / 2) - (window_width / 2))
        position_y = int((screen_height / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{position_x}+{position_y}")

        # --- Main Layout: Paned Window for resizable columns ---
        paned_window = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Left Pane: User List ---
        left_pane = ttk.Frame(paned_window, padding="10", style='TFrame')
        paned_window.add(left_pane, weight=2)

        # --- Right Pane: Add User Form ---
        right_pane = ttk.Frame(paned_window, padding="10", style='TFrame')
        paned_window.add(right_pane, weight=2)

        # --- Widgets for Left Pane (User List) ---
        ttk.Label(left_pane, text="All Users", font=("Helvetica", 14, "bold")).pack(anchor=tk.W, pady=(0, 10))

        columns = ('username', 'role', 'id')
        # The 'id' column is defined but not displayed, keeping it available for internal use.
        self.tree = ttk.Treeview(left_pane, columns=columns, displaycolumns=('username', 'role'), show='headings')
        self.tree.heading('username', text='Username')
        self.tree.heading('role', text='Role')
        # No need to configure the heading or width of a hidden column.
        self.tree.pack(fill=tk.BOTH, expand=True)

        list_actions_frame = ttk.Frame(left_pane, style='TFrame')
        list_actions_frame.pack(fill=tk.X, pady=(10, 0))
        
        # Use standard buttons for this secondary window for simplicity, or RoundedButton for consistency.
        # For consistency, we will use a slightly modified color set for these buttons.
        self.secondary_button_colors = self.parent_app.button_colors.copy()
        self.secondary_button_colors["parent_bg"] = self.parent_app.theme_colors["background"]

        RoundedButton(list_actions_frame, "Refresh List", self.refresh_user_list, self.secondary_button_colors, height=35).pack(side=tk.LEFT)
        RoundedButton(list_actions_frame, "Delete Selected", self.delete_user_gui, self.secondary_button_colors, height=35).pack(side=tk.LEFT, padx=5)
        RoundedButton(list_actions_frame, "Edit Selected", self.edit_selected_user, self.secondary_button_colors, height=35).pack(side=tk.LEFT)

        # --- Widgets for Right Pane (Add User Form) ---
        self.right_pane_title_label = ttk.Label(right_pane, text="Add New User", font=("Helvetica", 14, "bold"))
        self.right_pane_title_label.pack(anchor=tk.W, pady=(0, 20))

        form_frame = ttk.Frame(right_pane, style='TFrame')
        form_frame.pack(fill=tk.X)

        ttk.Label(form_frame, text="Username:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.username_entry = ttk.Entry(form_frame, width=30)
        self.username_entry.grid(row=0, column=1, sticky=tk.EW, pady=5, columnspan=3)
        
        # Make the column with the entry expandable
        form_frame.grid_columnconfigure(1, weight=1)
        form_frame.grid_columnconfigure(2, weight=1)
        form_frame.grid_columnconfigure(3, weight=1)


        ttk.Label(form_frame, text="Role:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.role_var = tk.StringVar()
        self.role_combobox = ttk.Combobox(form_frame, textvariable=self.role_var, values=[ROLE_KID, ROLE_PARENT], state="readonly")
        self.role_combobox.grid(row=1, column=1, sticky=tk.EW, pady=5, columnspan=3)
        self.role_combobox.set(ROLE_KID) # Default to 'kid'
        self.role_combobox.bind("<<ComboboxSelected>>", self._on_role_change)

        # --- Avatar Selection ---
        # NOTE: This feature requires the 'Pillow' library.
        # Install it using: pip install Pillow
        ttk.Label(form_frame, text="Avatar:").grid(row=2, column=0, sticky=tk.W, pady=5)
        avatar_frame = ttk.Frame(form_frame, style='TFrame')
        avatar_frame.grid(row=2, column=1, columnspan=3, sticky=tk.W, pady=5)

        self.avatar_preview_label = ttk.Label(avatar_frame)
        self.avatar_preview_label.pack(side=tk.LEFT, padx=(0, 10))
        self.avatar_photo_image = None # To prevent garbage collection
        self.selected_avatar_path = None
        self._load_avatar_preview() # Load default avatar preview
        
        RoundedButton(
            avatar_frame, "Select...", self._select_avatar, 
            self.secondary_button_colors, height=35, radius=15
        ).pack(side=tk.LEFT)

        RoundedButton(
            avatar_frame, "Upload...", self._upload_avatar,
            self.secondary_button_colors, height=35, radius=15
        ).pack(side=tk.LEFT, padx=5)

        # --- Kid-Specific Settings Frame ---
        self.kid_settings_frame = ttk.LabelFrame(right_pane, text="Kid-Specific Settings", padding="10")
        self.kid_settings_frame.pack(fill=tk.X, pady=(20, 0))

        # D.O.B.
        ttk.Label(self.kid_settings_frame, text="Date of Birth:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.dob_day_var = tk.StringVar(value="1")
        ttk.Spinbox(self.kid_settings_frame, from_=1, to=31, textvariable=self.dob_day_var, width=5).grid(row=0, column=1, pady=5)
        
        months = [datetime(2000, m, 1).strftime('%B') for m in range(1, 13)]
        self.dob_month_var = tk.StringVar(value=months[0])
        ttk.Combobox(self.kid_settings_frame, textvariable=self.dob_month_var, values=months, state="readonly", width=12).grid(row=0, column=2, pady=5)

        self.dob_year_var = tk.StringVar(value=str(datetime.now().year - 10))
        ttk.Spinbox(self.kid_settings_frame, from_=1980, to=datetime.now().year, textvariable=self.dob_year_var, width=7).grid(row=0, column=3, pady=5)

        # Time Limits
        ttk.Label(self.kid_settings_frame, text="Play Time Limit (min):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.max_session_var = tk.StringVar(value="120")
        ttk.Spinbox(self.kid_settings_frame, from_=0, to=300, increment=15, textvariable=self.max_session_var, width=5).grid(row=1, column=1, sticky=tk.W, pady=5)

        ttk.Label(self.kid_settings_frame, text="Max Daily Time (min):").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.max_daily_var = tk.StringVar(value="210")
        ttk.Spinbox(self.kid_settings_frame, from_=0, to=600, increment=30, textvariable=self.max_daily_var, width=5).grid(row=2, column=1, sticky=tk.W, pady=5)

        ttk.Label(self.kid_settings_frame, text="Rest Time (min):").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.rest_time_var = tk.StringVar(value="60")
        ttk.Spinbox(self.kid_settings_frame, from_=0, to=60, increment=5, textvariable=self.rest_time_var, width=5).grid(row=3, column=1, sticky=tk.W, pady=5)

        # Lunch Time
        ttk.Label(self.kid_settings_frame, text="Lunch Start (HH:MM):").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.lunch_start_entry = ttk.Entry(self.kid_settings_frame, width=8)
        self.lunch_start_entry.insert(0, "12:00")
        self.lunch_start_entry.grid(row=4, column=1, sticky=tk.W, pady=5)

        ttk.Label(self.kid_settings_frame, text="Lunch End (HH:MM):").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.lunch_end_entry = ttk.Entry(self.kid_settings_frame, width=8)
        self.lunch_end_entry.insert(0, "13:00")
        self.lunch_end_entry.grid(row=5, column=1, sticky=tk.W, pady=5)

        # Allowed Play Hours
        ttk.Label(self.kid_settings_frame, text="Allowed Start (HH:MM):").grid(row=6, column=0, sticky=tk.W, pady=5)
        self.allowed_start_entry = ttk.Entry(self.kid_settings_frame, width=8)
        self.allowed_start_entry.insert(0, "15:00")
        self.allowed_start_entry.grid(row=6, column=1, sticky=tk.W, pady=5)

        ttk.Label(self.kid_settings_frame, text="Allowed End (HH:MM):").grid(row=7, column=0, sticky=tk.W, pady=5)
        self.allowed_end_entry = ttk.Entry(self.kid_settings_frame, width=8)
        self.allowed_end_entry.insert(0, "18:00")
        self.allowed_end_entry.grid(row=7, column=1, sticky=tk.W, pady=5)

        # Enforce Lunch Routine
        self.enforce_lunch_routine_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.kid_settings_frame,
            text="Enforce Lunch & Teeth Routine",
            variable=self.enforce_lunch_routine_var
        ).grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=10)

        self.action_buttons_frame = ttk.Frame(right_pane, style='TFrame')
        self.action_buttons_frame.pack(side=tk.BOTTOM, pady=(20, 0), fill=tk.X)

        self.add_user_button = RoundedButton(self.action_buttons_frame, "Add User", self.add_user_gui, self.secondary_button_colors, height=35)
        self.add_user_button.pack(side=tk.RIGHT)

        # Buttons for edit mode (initially hidden)
        self.save_changes_button = RoundedButton(self.action_buttons_frame, "Save Changes", self.save_changes, self.secondary_button_colors, height=35)
        self.cancel_edit_button = RoundedButton(self.action_buttons_frame, "Cancel", self.cancel_edit, self.secondary_button_colors, height=35)

        # --- Initial Load ---
        self._on_role_change() # Set initial visibility
        self.refresh_user_list()

    def _clear_form(self):
        """Resets all form fields to their default state."""
        self.username_entry.delete(0, tk.END)
        self.role_combobox.set(ROLE_KID)
        self.selected_avatar_path = None
        self._load_avatar_preview() # Load default

        # Reset kid settings
        self.dob_day_var.set("1")
        self.dob_month_var.set(datetime(2000, 1, 1).strftime('%B'))
        self.dob_year_var.set(str(datetime.now().year - 10))
        self.max_session_var.set("120")
        self.max_daily_var.set("210")
        self.rest_time_var.set("60")
        self.lunch_start_entry.delete(0, tk.END)
        self.lunch_start_entry.insert(0, "12:00")
        self.lunch_end_entry.delete(0, tk.END)
        self.lunch_end_entry.insert(0, "13:00")
        self.allowed_start_entry.delete(0, tk.END)
        self.allowed_start_entry.insert(0, "15:00")
        self.allowed_end_entry.delete(0, tk.END)
        self.allowed_end_entry.insert(0, "18:00")
        self.enforce_lunch_routine_var.set(True)

        self._on_role_change()

    def cancel_edit(self):
        """Cancels the edit operation and resets the form."""
        self.editing_user_id = None
        self.right_pane_title_label.config(text="Add New User")
        self._clear_form()

        # Switch buttons back to "Add" mode
        self.save_changes_button.pack_forget()
        self.cancel_edit_button.pack_forget()
        self.add_user_button.pack(side=tk.RIGHT)

    def edit_selected_user(self):
        """Populates the form with the data of the selected user for editing."""
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("Selection Error", "Please select a user to edit.", parent=self)
            return

        item = selected_items[0]
        user_id = self.tree.item(item)['values'][2]
        user_data = get_user(self.db, user_id)

        if not user_data:
            messagebox.showerror("Error", "Could not retrieve user data.", parent=self)
            return

        self.cancel_edit() # Clear form before populating

        self.editing_user_id = user_id

        # Populate form
        self.right_pane_title_label.config(text=f"Edit User: {user_data.get('username', '')}")
        self.username_entry.insert(0, user_data.get('username', ''))
        self.role_var.set(user_data.get('role', ROLE_KID))

        # Reset selected path for edits and load avatar preview from URL
        self.selected_avatar_path = None
        avatar_url = user_data.get('avatar_url')
        self._load_avatar_preview() # Reset to default first
        if avatar_url:
            try:
                # Load image from URL and update preview
                img = load_image_from_url(avatar_url)
                if img:
                    self._load_avatar_preview_from_image(img)
                else:
                    print(f"Failed to load avatar from URL for preview: {avatar_url}")
            except Exception as e:
                print(f"Error loading avatar preview from URL: {e}")

        if self.role_var.get() == ROLE_KID:
            dob_str = user_data.get("dob")
            if dob_str:
                try:
                    dob = datetime.fromisoformat(dob_str.replace("Z", "+00:00")).date()
                    self.dob_day_var.set(str(dob.day))
                    self.dob_month_var.set(dob.strftime('%B'))
                    self.dob_year_var.set(str(dob.year))
                except ValueError:
                    print(f"Could not parse DOB '{dob_str}' for user {user_id}")

            self.max_session_var.set(str(user_data.get("max_session_minutes", "120")))
            self.max_daily_var.set(str(user_data.get("max_daily_minutes", "210")))
            self.rest_time_var.set(str(user_data.get("rest_minutes", "60")))
            self.lunch_start_entry.delete(0, tk.END)
            self.lunch_start_entry.insert(0, user_data.get("lunch_start_time", "12:00"))
            self.lunch_end_entry.delete(0, tk.END)
            self.lunch_end_entry.insert(0, user_data.get("lunch_end_time", "13:00"))
            self.allowed_start_entry.delete(0, tk.END)
            self.allowed_start_entry.insert(0, user_data.get("allowed_start_time", "15:00"))
            self.allowed_end_entry.delete(0, tk.END)
            self.allowed_end_entry.insert(0, user_data.get("allowed_end_time", "18:00"))
            self.enforce_lunch_routine_var.set(user_data.get("enforce_lunch_routine", True))

        self._on_role_change()

        # Switch buttons to "Edit" mode
        self.add_user_button.pack_forget()
        # Pack from right to left to get the desired order: [Save Changes] [Cancel]
        self.cancel_edit_button.pack(side=tk.RIGHT)
        self.save_changes_button.pack(side=tk.RIGHT, padx=(0, 5))

    def _on_role_change(self, event=None):
        """Shows or hides the kid-specific settings based on the selected role."""
        if self.role_var.get() == ROLE_KID:
            # Using pack() without 'after' is more robust if other widgets change.
            self.kid_settings_frame.pack(fill=tk.X, pady=(20, 0))
        else:
            self.kid_settings_frame.pack_forget()

    def _load_avatar_preview(self, path=None):
        """Loads and displays a preview of the avatar image."""
        # This variable will hold the path to the image file that is currently displayed.
        # It's not necessarily the one that will be saved. self.selected_avatar_path is for that.
        current_preview_path = path

        if path is None:
            # On first load, use a default avatar if it exists.
            current_preview_path = os.path.join(os.path.dirname(__file__), 'pictures', 'default_avatar.png')

        if not current_preview_path or not os.path.exists(current_preview_path):
            # If no file, show a placeholder.
            self.avatar_preview_label.config(image='', text="No Avatar", width=12, relief="solid", borderwidth=1)
            self.avatar_photo_image = None # Clear the reference
            return

        try:
            img = Image.open(current_preview_path)
            img.thumbnail((100, 100))  # Resize for preview
            self.avatar_photo_image = ImageTk.PhotoImage(img)
            self.avatar_preview_label.config(image=self.avatar_photo_image, text="", relief="flat")
        except Exception as e:
            print(f"Error loading avatar preview: {e}")
            self.avatar_preview_label.config(image='', text="Load Error", width=12, relief="solid", borderwidth=1)
            self.avatar_photo_image = None # Clear the reference

    def _load_avatar_preview_from_image(self, img: Image.Image):
        """Loads and displays a preview from a PIL Image object."""
        try:
            img.thumbnail((100, 100))  # Resize for preview
            self.avatar_photo_image = ImageTk.PhotoImage(img)
            self.avatar_preview_label.config(image=self.avatar_photo_image, text="", relief="flat")
        except Exception as e:
            print(f"Error loading avatar preview from image object: {e}")
            self.avatar_preview_label.config(image='', text="Load Error", width=12, relief="solid", borderwidth=1)
            self.avatar_photo_image = None # Clear the reference


    def _select_avatar(self):
        """Opens a window to select a pre-defined avatar."""
        AvatarSelectionWindow(self, self._on_avatar_selected, self.secondary_button_colors)

    def _on_avatar_selected(self, avatar_path: Optional[str]):
        """
        Callback function for when an avatar is selected from the selection window.
        
        Args:
            avatar_path: The full path to the selected avatar image, or None.
        """
        if avatar_path:
            self.selected_avatar_path = avatar_path
            self._load_avatar_preview(avatar_path)

    def _upload_avatar(self):
        """Opens a file dialog to let the user upload their own avatar."""
        filepath = filedialog.askopenfilename(
            title="Select a Custom Avatar",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif"), ("All files", "*.*")],
            parent=self
        )
        if filepath:
            self._on_avatar_selected(filepath)

    def refresh_user_list(self):
        """Clears and re-populates the user list from Firestore."""
        self.cancel_edit() # Reset form to "add" mode
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        users = list_users(self.db)
        # Sort so that parents come first, then kids, both alphabetically by username
        def role_order(role):
            return 0 if role == 'Parent' else 1
        users.sort(key=lambda u: (role_order(u.get('role', '')), u.get('username', '').lower()))
        for user in users:
            self.tree.insert('', tk.END, values=(user.get('username', 'N/A'), user.get('role', 'N/A'), user.get('id', 'N/A')))

    def add_user_gui(self):
        """Handles the 'Add User' button click."""
        username = self.username_entry.get().strip()
        role = self.role_var.get()

        if not username or not role:
            messagebox.showwarning("Input Error", "Please provide both a username and a role.", parent=self)
            return

        # --- Check for unique username ---
        if is_username_taken(self.db, username):
            messagebox.showerror("Input Error", f"The username '{username}' is already taken. Please choose another.", parent=self)
            return

        user_data = {"username": username, "role": role}

        # Handle avatar file copy and naming
        if self.selected_avatar_path is not None:
            try:
                avatar_url = upload_image_to_cloudinary(self.selected_avatar_path)
                if not avatar_url:
                    # upload_image_to_cloudinary shows its own messagebox, just return
                    return
                user_data['avatar_url'] = avatar_url
            except Exception as e:
                messagebox.showerror("Avatar Error", f"Could not process the avatar image: {e}", parent=self)
                return # Stop user creation if avatar fails to save

        self._gather_and_save_user(user_data)

    def save_changes(self):
        """Handles the 'Save Changes' button click."""
        if not self.editing_user_id:
            messagebox.showerror("Error", "No user is being edited. Please cancel and select a user.", parent=self)
            return

        username = self.username_entry.get().strip()
        role = self.role_var.get()

        if not username or not role:
            messagebox.showwarning("Input Error", "Username and role cannot be empty.", parent=self)
            return

        # --- Check for unique username, excluding the current user ---
        if is_username_taken(self.db, username, self.editing_user_id):
            messagebox.showerror("Input Error", f"The username '{username}' is already taken. Please choose another.", parent=self)
            return

        user_data = {"username": username, "role": role}

        # Handle avatar update
        if self.selected_avatar_path:
            # A new avatar has been selected, so we need to replace the old one.
            try:
                # First, delete the old avatar from Cloudinary
                old_user_data = get_user(self.db, self.editing_user_id)
                if old_user_data and old_user_data.get('avatar_url'):
                    old_avatar_url = old_user_data['avatar_url']
                    if not delete_image_from_cloudinary(old_avatar_url):
                        print(f"Warning: could not delete old avatar from Cloudinary: {old_avatar_url}")

                # Now, upload the new one to Cloudinary
                new_avatar_url = upload_image_to_cloudinary(self.selected_avatar_path)
                if not new_avatar_url:
                     # Error is shown by the upload function
                     return
                user_data['avatar_url'] = new_avatar_url
            except Exception as e:
                messagebox.showerror("Avatar Error", f"Could not update the avatar image: {e}", parent=self)
                return

        self._gather_and_save_user(user_data, is_update=True)

    def _gather_and_save_user(self, user_data: Dict[str, Any], is_update: bool = False):
        """Gathers common form data and calls the appropriate DB function."""
        role = user_data.get('role')
        if role == ROLE_KID:
            try:
                day = int(self.dob_day_var.get())
                month_str = self.dob_month_var.get()
                month = datetime.strptime(month_str, "%B").month
                year = int(self.dob_year_var.get())
                dob = datetime(year, month, day)

                kid_settings = {
                    "dob": dob.isoformat(),
                    "max_session_minutes": int(self.max_session_var.get()),
                    "max_daily_minutes": int(self.max_daily_var.get()),
                    "rest_minutes": int(self.rest_time_var.get()),
                    "lunch_start_time": self.lunch_start_entry.get(),
                    "lunch_end_time": self.lunch_end_entry.get(),
                    "allowed_start_time": self.allowed_start_entry.get(),
                    "allowed_end_time": self.allowed_end_entry.get(),
                    "enforce_lunch_routine": self.enforce_lunch_routine_var.get()
                }
                user_data.update(kid_settings)
            except ValueError:
                messagebox.showerror("Input Error", "Invalid Date of Birth or numeric value. Please check your inputs.", parent=self)
                return

        if is_update:
            if self.editing_user_id is not None and update_user(self.db, self.editing_user_id, user_data):
                messagebox.showinfo("Success", f"User '{user_data['username']}' updated successfully.", parent=self)
                self.refresh_user_list() # This also resets the form
            else:
                messagebox.showerror("Database Error", f"Failed to update user '{user_data['username']}'.", parent=self)
        else:
            if add_user(self.db, user_data):
                messagebox.showinfo("Success", f"User '{user_data['username']}' added successfully.", parent=self)
                self._clear_form()
                self.refresh_user_list()
            else:
                messagebox.showerror("Database Error", f"Failed to add user '{user_data['username']}'.", parent=self)


    def delete_user_gui(self):
        """Handles the 'Delete Selected' button click."""
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("Selection Error", "Please select a user from the list to delete.", parent=self)
            return

        item = selected_items[0]
        username = self.tree.item(item)['values'][0]
        user_id = self.tree.item(item)['values'][2]

        if messagebox.askyesno("Confirm Deletion", f"Are you sure you want to delete user '{username}'?", parent=self):
            # Get user data to find the avatar URL before deleting from DB
            user_data = get_user(self.db, user_id)

            if delete_user(self.db, user_id):
                messagebox.showinfo("Success", f"User '{username}' was deleted.", parent=self)
                # If deletion from DB was successful, delete the avatar from Cloudinary
                if user_data and user_data.get('avatar_url'):
                    if not delete_image_from_cloudinary(user_data['avatar_url']):
                        print(f"Warning: could not delete avatar from Cloudinary: {user_data['avatar_url']}")
                self.refresh_user_list()
            else:
                messagebox.showerror("Database Error", f"Failed to delete user '{username}'.", parent=self)

class SettingsWindow(tk.Toplevel):
    """A Toplevel window for managing application settings like themes and email notifications."""
    def __init__(self, parent: 'GameSentryApp'):
        super().__init__(parent)
        self.parent_app = parent

        self.title("Settings")
        self.transient(parent)
        self.grab_set()
        self.configure(bg=self.parent_app.theme_colors['background'])

        # --- Center Window ---
        window_width = 450
        window_height = 600
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        position_x = int((screen_width / 2) - (window_width / 2))
        position_y = int((screen_height / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{position_x}+{position_y}")
        self.resizable(False, False)

        # --- Main Frame ---
        main_frame = ttk.Frame(self, padding=20, style='TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Appearance Section ---
        ttk.Label(main_frame, text="Appearance", font=("Helvetica", 14, "bold")).pack(pady=(0, 15), anchor=tk.W)
        theme_frame = ttk.Frame(main_frame, style='TFrame')
        theme_frame.pack(fill=tk.X, pady=5, anchor=tk.W)
        ttk.Label(theme_frame, text="Theme:").pack(side=tk.LEFT, padx=(0, 10))
        self.theme_var = tk.StringVar(value=self.parent_app.current_theme)
        light_rb = ttk.Radiobutton(theme_frame, text="Light", variable=self.theme_var, value="light", command=lambda: self.parent_app.change_theme("light"))
        light_rb.pack(side=tk.LEFT)
        dark_rb = ttk.Radiobutton(theme_frame, text="Dark", variable=self.theme_var, value="dark", command=lambda: self.parent_app.change_theme("dark"))
        dark_rb.pack(side=tk.LEFT, padx=10)

        # --- General Section ---
        ttk.Separator(main_frame, orient='horizontal').pack(fill=tk.X, pady=20)
        ttk.Label(main_frame, text="General", font=("Helvetica", 14, "bold")).pack(pady=(0, 15), anchor=tk.W)
        tray_frame = ttk.Frame(main_frame, style='TFrame')
        tray_frame.pack(fill=tk.X, pady=10, anchor=tk.W)
        tray_cb = ttk.Checkbutton(tray_frame, text="Close to tray (instead of exiting)", variable=self.parent_app.close_to_tray_enabled, command=self.parent_app.save_config)
        tray_cb.pack(side=tk.LEFT)

        # --- Notifications Section ---
        ttk.Separator(main_frame, orient='horizontal').pack(fill=tk.X, pady=20)
        ttk.Label(main_frame, text="Notifications", font=("Helvetica", 14, "bold")).pack(pady=(0, 15), anchor=tk.W)

        # --- Sound Notifications Option ---
        sound_frame = ttk.Frame(main_frame, style='TFrame')
        sound_frame.pack(fill=tk.X, pady=5, anchor=tk.W)
        sound_cb = ttk.Checkbutton(sound_frame, text="Enable notification sounds", variable=self.parent_app.sound_notifications_enabled, command=self.parent_app.save_config)
        sound_cb.pack(side=tk.LEFT)

        # Load current email config
        email_config = self.parent_app.get_email_config()
        
        # Enable Email Notifications
        email_enable_frame = ttk.Frame(main_frame, style='TFrame')
        email_enable_frame.pack(fill=tk.X, pady=5)
        self.email_enabled_var = tk.BooleanVar(value=email_config['email_enabled'])
        email_cb = ttk.Checkbutton(email_enable_frame, text="Enable email notifications", variable=self.email_enabled_var)
        email_cb.pack(side=tk.LEFT)

        # Gmail Settings
        ttk.Label(main_frame, text="Gmail Settings", font=("Helvetica", 12, "bold")).pack(anchor=tk.W, pady=(15, 5))

        # Email Address
        email_addr_frame = ttk.Frame(main_frame, style='TFrame')
        email_addr_frame.pack(fill=tk.X, pady=5)
        ttk.Label(email_addr_frame, text="Gmail Address:").pack(side=tk.LEFT, padx=(0, 10))
        self.email_address_var = tk.StringVar(value=email_config['email_address'])
        self.email_address_entry = ttk.Entry(email_addr_frame, textvariable=self.email_address_var, width=30)
        self.email_address_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # App Password
        password_frame = ttk.Frame(main_frame, style='TFrame')
        password_frame.pack(fill=tk.X, pady=5)
        ttk.Label(password_frame, text="App Password:").pack(side=tk.LEFT, padx=(0, 10))
        self.email_password_var = tk.StringVar(value=email_config['email_password'])
        self.email_password_entry = ttk.Entry(password_frame, textvariable=self.email_password_var, show="*", width=30)
        self.email_password_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Recipients
        recipients_frame = ttk.Frame(main_frame, style='TFrame')
        recipients_frame.pack(fill=tk.X, pady=5)
        ttk.Label(recipients_frame, text="Recipients:").pack(side=tk.LEFT, padx=(0, 10))
        self.email_recipients_var = tk.StringVar(value=", ".join(email_config['email_recipients']))
        self.email_recipients_entry = ttk.Entry(recipients_frame, textvariable=self.email_recipients_var, width=30)
        self.email_recipients_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Help text
        help_frame = ttk.Frame(main_frame, style='TFrame')
        help_frame.pack(fill=tk.X, pady=10)
        help_text = """To use Gmail notifications:
1. Enable 2-factor authentication on your Gmail account
2. Generate an App Password (Google Account → Security → App Passwords)
3. Use the App Password (not your regular password)
4. Add recipient email addresses separated by commas"""
        help_label = ttk.Label(help_frame, text=help_text, font=("Helvetica", 9), foreground="gray")
        help_label.pack(anchor=tk.W)

        # --- Save and Close Buttons ---
        button_frame = ttk.Frame(main_frame, style='TFrame')
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))
        
        RoundedButton(button_frame, "Save Email Settings", self._save_email_settings, self.parent_app.button_colors, height=35).pack(side=tk.RIGHT, padx=(5, 0))
        RoundedButton(button_frame, "Close", self.destroy, self.parent_app.button_colors, height=35).pack(side=tk.RIGHT)

    def _save_email_settings(self):
        """Saves email configuration."""
        try:
            email_enabled = self.email_enabled_var.get()
            email_address = self.email_address_var.get().strip()
            email_password = self.email_password_var.get().strip()
            recipients_str = self.email_recipients_var.get().strip()
            
            # Parse recipients
            recipients = [r.strip() for r in recipients_str.split(',') if r.strip()]
            
            # Validate
            if email_enabled:
                if not email_address:
                    messagebox.showerror("Error", "Please enter a Gmail address.", parent=self)
                    return
                if not email_password:
                    messagebox.showerror("Error", "Please enter an App Password.", parent=self)
                    return
                if not recipients:
                    messagebox.showerror("Error", "Please enter at least one recipient email address.", parent=self)
                    return
            
            # Save configuration
            self.parent_app.save_email_config(email_enabled, email_address, email_password, recipients)
            messagebox.showinfo("Success", "Email settings saved successfully!", parent=self)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save email settings: {e}", parent=self)

class AvatarSelectionWindow(tk.Toplevel):
    """
    A Toplevel window to display and select from a list of pre-defined avatars.
    """
    def __init__(self, parent, callback, colors):
        super().__init__(parent)
        self.callback = callback
        self.parent_app = parent.parent_app if isinstance(parent, UserManagementWindow) else parent

        self.title("Select an Avatar")
        self.transient(parent)
        self.grab_set()
        self.configure(bg=self.parent_app.theme_colors['background'])

        # --- Center Window on Screen ---
        window_width = 550
        window_height = 450
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        position_x = int((screen_width / 2) - (window_width / 2))
        position_y = int((screen_height / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{position_x}+{position_y}")

        # --- Path to source avatars ---
        self.source_avatars_dir = os.path.join(os.path.dirname(__file__), 'pictures', 'avatars')
        if not os.path.isdir(self.source_avatars_dir):
            messagebox.showerror("Error", f"Avatar directory not found:\n{self.source_avatars_dir}", parent=self)
            self.destroy()
            return

        # --- Main Frame ---
        main_frame = ttk.Frame(self, padding=10, style='TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Scrollable Area ---
        canvas = tk.Canvas(main_frame, bg=self.parent_app.theme_colors['background'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview, style='Vertical.TScrollbar')
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Load and Display Avatars ---
        self.photo_images = [] # IMPORTANT: To prevent garbage collection
        valid_extensions = ('.png', '.jpg', '.jpeg', '.gif')
        
        row, col = 0, 0
        max_cols = 4 # 4 avatars per row

        for filename in sorted(os.listdir(self.source_avatars_dir)):
            if filename.lower().endswith(valid_extensions):
                full_path = os.path.join(self.source_avatars_dir, filename)
                try:
                    img = Image.open(full_path)
                    img.thumbnail((100, 100))
                    photo = ImageTk.PhotoImage(img)
                    self.photo_images.append(photo)

                    btn = ttk.Button(scrollable_frame, image=photo, command=lambda p=full_path: self._on_select(p))
                    btn.grid(row=row, column=col, padx=5, pady=5)

                    col = (col + 1) % max_cols
                    if col == 0:
                        row += 1
                except Exception as e:
                    print(f"Could not load avatar {filename}: {e}")

    def _on_select(self, path: str):
        """Calls the callback with the selected path and closes the window."""
        self.callback(path)
        self.destroy()


def add_user(db: Any, user_data: Dict[str, Any]) -> Optional[str]:
    """Adds a new user to the 'users' collection in Firestore."""
    if not user_data.get("username") or not user_data.get("role"):
        print(f"Error: User data must include a username and role.")
        return None
    try:
        user_data['created_at'] = datetime.now(timezone.utc).isoformat()
        doc_ref = db.collection(USERS_COLLECTION).add(user_data)[1]
        print(f"Successfully added user '{user_data['username']}' with ID: {doc_ref.id}")
        return doc_ref.id
    except Exception as e:
        print(f"An error occurred while adding user: {e}")
        return None

def update_user(db: Any, user_id: str, user_data: Dict[str, Any]) -> bool:
    """Updates an existing user's data in Firestore."""
    try:
        user_data['updated_at'] = datetime.now(timezone.utc).isoformat()
        db.collection(USERS_COLLECTION).document(user_id).update(user_data)
        print(f"Successfully updated user with ID: {user_id}")
        return True
    except Exception as e:
        print(f"An error occurred while updating user {user_id}: {e}")
        return False

def get_user(db: Any, user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single user's data from Firestore by their ID."""
    if db is None:
        return None
    try:
        doc = db.collection(USERS_COLLECTION).document(user_id).get()
        if doc.exists:
            user_data = doc.to_dict()
            user_data['id'] = doc.id
            return user_data
        else:
            print(f"No user found with ID: {user_id}")
            return None
    except Exception as e:
        print(f"An error occurred while getting user {user_id}: {e}")
        return None

def is_username_taken(db: Any, username: str, user_id_to_exclude: Optional[str] = None) -> bool:
    """Checks if a username is already taken, optionally excluding a specific user ID."""
    query = db.collection(USERS_COLLECTION).where('username', '==', username).limit(1)
    docs = query.stream()
    return any(doc.id != user_id_to_exclude for doc in docs)

def list_users(db: Any) -> List[Dict[str, Any]]:
    """Retrieves all users from the 'users' collection, ordered by username."""
    try:
        docs = db.collection(USERS_COLLECTION).order_by('username').stream()
        users = []
        for doc in docs:
            user_data = doc.to_dict()
            user_data['id'] = doc.id
            users.append(user_data)
        return users
    except Exception as e:
        print(f"An error occurred while listing users: {e}")
        messagebox.showerror("Firestore Error", f"Could not fetch users: {e}\n\nFirestore might require a new index. Check the console output for a URL to create it.")
        return []

def delete_user(db: Any, user_id: str) -> bool:
    """Deletes a user from the 'users' collection by their ID."""
    try:
        db.collection(USERS_COLLECTION).document(user_id).delete()
        print(f"Successfully deleted user with ID: {user_id}")
        return True
    except Exception as e:
        print(f"An error occurred while deleting user: {e}")
        return False

def add_session_log_to_user(db, user_id, session_log):
    """Appends a session log entry to the user's 'sessions' array in Firestore."""
    if db is None:
        return False
    try:
        user_ref = db.collection(USERS_COLLECTION).document(user_id)
        user_ref.update({
            'sessions': ArrayUnion([session_log])
        })
        return True
    except Exception as e:
        print(f"Error adding session log to user {user_id}: {e}")
        return False

def get_session_logs_for_user(db, user_id):
    """Fetches the session logs array for a user from Firestore."""
    try:
        if db is None:
            return []
        user_ref = db.collection(USERS_COLLECTION).document(user_id)
        doc = user_ref.get()
        if doc.exists:
            data = doc.to_dict()
            return data.get('sessions', [])
        return []
    except Exception as e:
        print(f"Error fetching session logs for user {user_id}: {e}")
        return []

# --- Firebase and Cloudinary Functions ---

def initialize_firebase():
    """Initializes the Firebase Admin SDK and configures Cloudinary."""
    script_dir = os.path.dirname(__file__)
    cred_path = os.path.join(script_dir, SECRETS_DIR, 'game-sentry-qcayd-firebase-adminsdk-fbsvc-da160f8409.json')

    try:
        # Firebase Initialization
        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)  # Removed storageBucket config
        print("Firebase App initialized successfully.")

        # Cloudinary Configuration
        # IMPORTANT: For production, use environment variables instead of hardcoding.
        cloudinary.config(
          cloud_name = "dmfkl10kr",
          api_key = "995235619211233",
          api_secret = "hObePoXLKg27BfsOWp7WfJiqx4A",
        )
        print("Cloudinary configured successfully.")
        
        return firestore.client()
    except FileNotFoundError as e:
        print(f"Error: The credential file was not found at '{cred_path}'")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during initialization: {e}")
        return None


def upload_image_to_cloudinary(image_path):
    """Resizes an image to 128x128, uploads it to Cloudinary, and returns the URL."""
    try:
        with Image.open(image_path) as img:
            # Resize the image to a fixed 128x128 size.
            # Using LANCZOS for high-quality downsampling.
            try:
                # For Pillow 9.1.0+ (Resampling is a submodule)
                resample_filter = Image.Resampling.LANCZOS
            except AttributeError:
                # For older Pillow versions
                resample_filter = Image.LANCZOS # type: ignore
            img_resized = img.resize((128, 128), resample=resample_filter)

            # Save the resized image to an in-memory buffer to avoid creating temp files.
            buffer = io.BytesIO()
            # Preserve original format if it's a common web format, otherwise default to PNG.
            img_format = img.format if img.format in ['JPEG', 'PNG', 'GIF'] else 'PNG'
            img_resized.save(buffer, format=img_format)
            buffer.seek(0)  # Rewind the buffer to the beginning

            # Upload the buffer's content to Cloudinary
            upload_result = cloudinary.uploader.upload(buffer, folder="game_sentry_avatars/")
            return upload_result.get("secure_url")
    except Exception as e:
        print(f"Error processing or uploading image to Cloudinary: {e}")
        messagebox.showerror("Cloudinary Error", f"Failed to upload image: {e}")
        return None

def delete_image_from_cloudinary(url: str) -> bool:
    """Deletes an image from Cloudinary using its URL."""
    if not url:
        return False
    try:
        # Extract public_id from URL. Example: .../upload/v12345/folder/image.jpg
        # The public_id is 'folder/image'
        parts = url.split('/')
        # Find the 'upload' part, the public_id starts after the version number
        upload_index = parts.index('upload')
        public_id_with_ext = '/'.join(parts[upload_index+2:])
        public_id, _ = os.path.splitext(public_id_with_ext)
        
        result = cloudinary.uploader.destroy(f"game_sentry_avatars/{public_id}")
        if result.get("result") == "ok":
            print(f"Successfully deleted {public_id} from Cloudinary.")
            return True
        else:
            print(f"Failed to delete {public_id} from Cloudinary. Result: {result}")
            return False
    except (ValueError, IndexError) as e:
        print(f"Could not parse public_id from Cloudinary URL '{url}': {e}")
        return False
    except Exception as e:
        print(f"Error deleting from Cloudinary: {e}")
        return False

def load_image_from_url(url: str, thumbnail_size: Optional[tuple] = None) -> Optional[Image.Image]:
    """Downloads an image from a URL and returns it as a PIL Image object."""
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status() # Raise an exception for bad status codes
        img = Image.open(io.BytesIO(response.content))
        if thumbnail_size:
            img.thumbnail(thumbnail_size)
        return img
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image from {url}: {e}")
        return None
    except Exception as e:
        print(f"Error processing image from {url}: {e}")
        return None

class RoundedButton(tk.Canvas):
    """
    A custom rounded button widget created using a Canvas.
    This is necessary because standard ttk widgets do not easily support
    border-radius styling.
    """
    def __init__(self, parent, text, command, colors, radius=20, height=40):
        # --- Font Configuration ---
        # Using a font tuple is a standard and reliable way to specify fonts.
        self.button_font = ("Helvetica", 11, "bold")
        font_metrics = tkfont.Font(font=self.button_font)
        text_width = font_metrics.measure(text)
        width = text_width + 40  # Add padding

        super().__init__(parent, width=width, height=height, bg=colors["parent_bg"], highlightthickness=0)

        self.command = command
        self.colors = colors
        self.radius = radius
        self.height = height
        self.width = width
        self.text = text

        self.tag_rect = "button_rect"
        self.tag_text = "button_text"

        self._draw(self.colors["bg"], self.colors["text"])

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, bg_color, text_color):
        """Draws the button shape and text."""
        self.delete("all")
        x1, y1, x2, y2 = 0, 0, self.width, self.height
        r = self.radius

        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r,
            x2, y2, x2 - r, y2,
            x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r,
            x1, y1, x1 + r, y1
        ]
        self.create_polygon(points, fill=bg_color, smooth=True, splinesteps=12, tags=self.tag_rect)
        self.create_text(self.width / 2, self.height / 2, text=self.text, fill=text_color, font=self.button_font, tags=self.tag_text)

    def _on_enter(self, event):
        self._draw(self.colors["hover_bg"], self.colors["text"])

    def _on_leave(self, event):
        self._draw(self.colors["bg"], self.colors["text"])

    def _on_press(self, event):
        self._draw(self.colors["press_bg"], self.colors["text"])

    def _on_release(self, event):
        self._draw(self.colors["hover_bg"], self.colors["text"])
        if self.command:
            self.command()

class ManualEntryDialog(tk.Toplevel):
    def __init__(self, parent, user, db, on_success):
        super().__init__(parent)
        self.title("Add Session Entry")
        self.resizable(False, False)
        self.user = user
        self.db = db
        self.on_success = on_success
        self.grab_set()
        self.configure(bg=parent.theme_colors['background'])

        # Date
        ttk.Label(self, text="Date (YYYY-MM-DD):").pack(pady=(10, 0), anchor="w", padx=20)
        self.date_var = tk.StringVar(value=datetime.now().strftime('%Y-%m-%d'))
        self.date_entry = ttk.Entry(self, textvariable=self.date_var, width=15)
        self.date_entry.pack(padx=20, anchor="w")

        # Start Time
        ttk.Label(self, text="Start Time (HH:MM):").pack(pady=(10, 0), anchor="w", padx=20)
        self.start_var = tk.StringVar(value=datetime.now().strftime('%H:00'))
        self.start_entry = ttk.Entry(self, textvariable=self.start_var, width=10)
        self.start_entry.pack(padx=20, anchor="w")

        # Stop Time
        ttk.Label(self, text="Stop Time (HH:MM):").pack(pady=(10, 0), anchor="w", padx=20)
        self.stop_var = tk.StringVar(value=(datetime.now() + timedelta(hours=1)).strftime('%H:00'))
        self.stop_entry = ttk.Entry(self, textvariable=self.stop_var, width=10)
        self.stop_entry.pack(padx=20, anchor="w")

        # Duration
        self.duration_label = ttk.Label(self, text="Duration: 01:00:00")
        self.duration_label.pack(pady=(10, 0), anchor="w", padx=20)

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=15, anchor='center', fill='x')
        ttk.Button(btn_frame, text="Save", command=self.save).pack(side=tk.LEFT, padx=20, ipadx=10, ipady=2)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=20, ipadx=10, ipady=2)

        # Update duration on change
        self.date_var.trace_add('write', lambda *a: self.update_duration())
        self.start_var.trace_add('write', lambda *a: self.update_duration())
        self.stop_var.trace_add('write', lambda *a: self.update_duration())
        self.update_duration()

        # Center the window on the parent
        self.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        win_w = self.winfo_width()
        win_h = self.winfo_height()
        x = parent_x + (parent_w // 2) - (win_w // 2)
        y = parent_y + (parent_h // 2) - (win_h // 2)
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def update_duration(self):
        try:
            date = self.date_var.get()
            start = self.start_var.get()
            stop = self.stop_var.get()
            start_dt = datetime.strptime(f"{date} {start}", '%Y-%m-%d %H:%M')
            stop_dt = datetime.strptime(f"{date} {stop}", '%Y-%m-%d %H:%M')
            if stop_dt < start_dt:
                stop_dt += timedelta(days=1)  # Allow overnight
            duration = stop_dt - start_dt
            h, rem = divmod(duration.seconds, 3600)
            m, s = divmod(rem, 60)
            self.duration_label.config(text=f"Duration: {h:02}:{m:02}:{s:02}")
        except Exception:
            self.duration_label.config(text="Duration: --:--:--")

    def save(self):
        date = self.date_var.get()
        start = self.start_var.get()
        stop = self.stop_var.get()
        try:
            start_dt = datetime.strptime(f"{date} {start}", '%Y-%m-%d %H:%M')
            stop_dt = datetime.strptime(f"{date} {stop}", '%Y-%m-%d %H:%M')
            if stop_dt < start_dt:
                stop_dt += timedelta(days=1)  # Allow overnight
            duration = stop_dt - start_dt
            duration_str = str(duration)
            if duration.days > 0:
                # Format as HH:MM:SS for >24h
                total_seconds = duration.total_seconds()
                h = int(total_seconds // 3600)
                m = int((total_seconds % 3600) // 60)
                s = int(total_seconds % 60)
                duration_str = f"{h:02}:{m:02}:{s:02}"
            else:
                h, rem = divmod(duration.seconds, 3600)
                m, s = divmod(rem, 60)
                duration_str = f"{h:02}:{m:02}:{s:02}"
        except Exception:
            messagebox.showerror("Invalid Input", "Please enter valid date and time values.", parent=self)
            return
        if start_dt > stop_dt:
            messagebox.showerror("Invalid Time Range", "Start time cannot be after stop time.", parent=self)
            return
        # Overlap check
        sessions = get_session_logs_for_user(self.db, self.user['id'])
        for other in sessions:
            try:
                other_start = datetime.strptime(other.get('start', ''), '%Y-%m-%d %H:%M:%S')
                other_stop = datetime.strptime(other.get('stop', ''), '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            if (start_dt < other_stop and stop_dt > other_start):
                messagebox.showerror("Time Overlap", f"The new time range overlaps with another session (from {other.get('start','')} to {other.get('stop','')}).", parent=self)
                return
        # Add entry
        user_doc = self.db.collection(USERS_COLLECTION).document(self.user['id'])
        sessions.append({'start': start_dt.strftime('%Y-%m-%d %H:%M:%S'), 'stop': stop_dt.strftime('%Y-%m-%d %H:%M:%S'), 'duration': duration_str})
        user_doc.update({'sessions': sessions})
        self.on_success()
        self.destroy()

if __name__ == "__main__":
    app = GameSentryApp()
    app.mainloop()
