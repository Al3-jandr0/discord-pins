import os
import sys
import re
import json
import shutil
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from dotenv import dotenv_values

import discord_pins_sync

# Determinar el directorio base (funciona tanto en .py como compilado en .exe con PyInstaller)
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(APP_DIR)
ENV_PATH = os.path.join(APP_DIR, ".env")


class QueueRedirector:
    """Redirige stdout/stderr hacia una cola thread-safe para mostrar logs en vivo en la GUI."""
    def __init__(self, log_queue):
        self.log_queue = log_queue

    def write(self, text):
        if text:
            self.log_queue.put(text)

    def flush(self):
        pass


class DiscordPinsApp(tk.Tk):
    # Paleta inspirada en Discord Dark
    BG_DARK = "#1e1f22"
    BG_PANEL = "#2b2d31"
    BG_INPUT = "#383a40"
    FG_TEXT = "#f2f3f5"
    FG_MUTED = "#b5bac1"
    ACCENT_BLURPLE = "#5865f2"
    ACCENT_GREEN = "#23a55a"
    ACCENT_AMBER = "#f0b232"
    ACCENT_RED = "#f23f43"

    def __init__(self):
        super().__init__()
        self.title("Extractor de Fijados: Discord → Google Drive")
        self.geometry("1120x740")
        self.minsize(980, 640)
        self.configure(bg=self.BG_DARK)

        self.log_queue = queue.Queue()
        self.is_running = False
        self.show_token = False

        # Estado del selector de canales: {channel_id (int): tk.BooleanVar}
        self.channel_vars = {}
        self.loaded_channels = []  # lista de dicts devuelta por fetch_guild_channels

        # Variables de formulario
        self.var_token = tk.StringVar()
        self.var_guild_id = tk.StringVar()
        self.var_drive_folder = tk.StringVar()
        self.var_service_file = tk.StringVar(value="credentials.json")
        self.var_channel_ids = tk.StringVar()
        self.var_category_name = tk.StringVar()
        self.var_unpin = tk.BooleanVar(value=False)
        self.var_notify = tk.BooleanVar(value=True)
        self.var_search_channel = tk.StringVar()
        self.var_search_channel.trace_add("write", lambda *_: self._render_channel_checkboxes())

        self._build_ui()
        self._load_env_into_form()
        self._refresh_credentials_status()
        self.after(100, self._poll_log_queue)

    # ─────────────────────────────────────────────────────────────
    # CONSTRUCCIÓN DE LA INTERFAZ
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Encabezado superior
        header = tk.Frame(self, bg=self.BG_PANEL, padx=18, pady=12)
        header.pack(fill="x", side="top")

        tk.Label(
            header,
            text="📌 Extractor de Fijados: Discord → Google Drive",
            font=("Segoe UI", 15, "bold"),
            bg=self.BG_PANEL,
            fg=self.FG_TEXT,
        ).pack(side="left")

        self.lbl_status = tk.Label(
            header,
            text="● Listo",
            font=("Segoe UI", 10, "bold"),
            bg=self.BG_PANEL,
            fg=self.ACCENT_GREEN,
        )
        self.lbl_status.pack(side="right")

        # Contenedor principal dividido en 2 columnas
        main_body = tk.Frame(self, bg=self.BG_DARK, padx=14, pady=12)
        main_body.pack(fill="both", expand=True)
        main_body.columnconfigure(0, weight=5, uniform="col")
        main_body.columnconfigure(1, weight=6, uniform="col")
        main_body.rowconfigure(0, weight=1)

        # Columna Izquierda: Configuración + Selector de Canales
        left_col = tk.Frame(main_body, bg=self.BG_DARK)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_col.rowconfigure(1, weight=1)
        left_col.columnconfigure(0, weight=1)

        self._build_config_card(left_col)
        self._build_channels_card(left_col)

        # Columna Derecha: Botones de ejecución + Consola en vivo
        right_col = tk.Frame(main_body, bg=self.BG_DARK)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_col.rowconfigure(1, weight=1)
        right_col.columnconfigure(0, weight=1)

        self._build_actions_card(right_col)
        self._build_console_card(right_col)

    def _create_card(self, parent, title):
        card = tk.Frame(parent, bg=self.BG_PANEL, padx=14, pady=12, highlightthickness=1, highlightbackground="#3f4147")
        tk.Label(
            card,
            text=title,
            font=("Segoe UI", 11, "bold"),
            bg=self.BG_PANEL,
            fg=self.FG_TEXT,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        return card

    def _build_config_card(self, parent):
        card = self._create_card(parent, "⚙️ 1. Credenciales y Configuración (.env)")
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        form = tk.Frame(card, bg=self.BG_PANEL)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        # Fila 0: Discord Token
        tk.Label(form, text="Discord Bot Token:", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        token_frame = tk.Frame(form, bg=self.BG_PANEL)
        token_frame.grid(row=0, column=1, sticky="ew", pady=4)
        token_frame.columnconfigure(0, weight=1)

        self.entry_token = tk.Entry(
            token_frame, textvariable=self.var_token, show="•", bg=self.BG_INPUT, fg=self.FG_TEXT,
            insertbackground=self.FG_TEXT, relief="flat", font=("Consolas", 9)
        )
        self.entry_token.grid(row=0, column=0, sticky="ew", ipady=4, padx=(0, 4))

        self.btn_toggle_token = tk.Button(
            token_frame, text="👁", command=self._toggle_token_visibility,
            bg=self.BG_INPUT, fg=self.FG_TEXT, relief="flat", padx=8, cursor="hand2"
        )
        self.btn_toggle_token.grid(row=0, column=1)

        # Fila 1: Guild ID
        tk.Label(form, text="ID del Servidor (Guild ID):", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        tk.Entry(
            form, textvariable=self.var_guild_id, bg=self.BG_INPUT, fg=self.FG_TEXT,
            insertbackground=self.FG_TEXT, relief="flat", font=("Consolas", 9)
        ).grid(row=1, column=1, sticky="ew", pady=4, ipady=4)

        # Fila 2: Google Drive Folder ID / Link
        tk.Label(form, text="Carpeta Drive (ID o Link):", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        tk.Entry(
            form, textvariable=self.var_drive_folder, bg=self.BG_INPUT, fg=self.FG_TEXT,
            insertbackground=self.FG_TEXT, relief="flat", font=("Consolas", 9)
        ).grid(row=2, column=1, sticky="ew", pady=4, ipady=4)

        # Fila 3: Archivo credentials.json
        tk.Label(form, text="Google Service Account:", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 9)).grid(
            row=3, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        cred_frame = tk.Frame(form, bg=self.BG_PANEL)
        cred_frame.grid(row=3, column=1, sticky="ew", pady=4)
        cred_frame.columnconfigure(0, weight=1)

        self.lbl_cred_info = tk.Label(
            cred_frame, text="Verificando credentials.json...", bg=self.BG_INPUT, fg=self.FG_MUTED,
            anchor="w", padx=6, pady=4, font=("Segoe UI", 8)
        )
        self.lbl_cred_info.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        tk.Button(
            cred_frame, text="📂 Cargar JSON", command=self._select_credentials_json,
            bg="#4e5058", fg=self.FG_TEXT, relief="flat", padx=8, font=("Segoe UI", 8, "bold"), cursor="hand2"
        ).grid(row=0, column=1, padx=(0, 4))

        self.btn_copy_email = tk.Button(
            cred_frame, text="📋 Copiar Email", command=self._copy_service_email,
            bg="#4e5058", fg=self.FG_TEXT, relief="flat", padx=8, font=("Segoe UI", 8), cursor="hand2"
        )
        self.btn_copy_email.grid(row=0, column=2)

        # Opciones de comportamiento (Checkboxes)
        opts_frame = tk.Frame(card, bg=self.BG_PANEL, pady=6)
        opts_frame.pack(fill="x")

        tk.Checkbutton(
            opts_frame,
            text="🔔 Enviar aviso en el canal de Discord con el link a Google Drive (NOTIFY_IN_CHANNEL)",
            variable=self.var_notify,
            bg=self.BG_PANEL, fg=self.FG_TEXT, selectcolor=self.BG_INPUT,
            activebackground=self.BG_PANEL, activeforeground=self.FG_TEXT,
            font=("Segoe UI", 9)
        ).pack(anchor="w")

        tk.Checkbutton(
            opts_frame,
            text="🗑️ Desfijar los mensajes del canal en Discord tras respaldarlos (UNPIN_AFTER_BACKUP)",
            variable=self.var_unpin,
            bg=self.BG_PANEL, fg=self.ACCENT_AMBER, selectcolor=self.BG_INPUT,
            activebackground=self.BG_PANEL, activeforeground=self.ACCENT_AMBER,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(2, 0))

        # Botón Guardar Configuración
        tk.Button(
            card,
            text="💾 Guardar Configuración en .env",
            command=lambda: self._save_env_from_form(silent=False),
            bg=self.ACCENT_BLURPLE,
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            pady=6,
            cursor="hand2",
        ).pack(fill="x", pady=(6, 0))

    def _build_channels_card(self, parent):
        card = self._create_card(parent, "📋 2. Selección de Canales a Respaldar")
        card.grid(row=1, column=0, sticky="nsew")

        # Sub-formulario de IDs o Categoría
        top_fields = tk.Frame(card, bg=self.BG_PANEL)
        top_fields.pack(fill="x", pady=(0, 6))
        top_fields.columnconfigure(1, weight=1)

        tk.Label(top_fields, text="IDs seleccionados:", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        tk.Entry(
            top_fields, textvariable=self.var_channel_ids, bg=self.BG_INPUT, fg=self.FG_TEXT,
            insertbackground=self.FG_TEXT, relief="flat", font=("Consolas", 9)
        ).grid(row=0, column=1, sticky="ew", pady=2, ipady=3)

        tk.Label(top_fields, text="O Categoría (si IDs vacío):", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        tk.Entry(
            top_fields, textvariable=self.var_category_name, bg=self.BG_INPUT, fg=self.FG_TEXT,
            insertbackground=self.FG_TEXT, relief="flat", font=("Segoe UI", 9)
        ).grid(row=1, column=1, sticky="ew", pady=2, ipady=3)

        # Barra de herramientas del selector visual
        toolbar = tk.Frame(card, bg=self.BG_PANEL)
        toolbar.pack(fill="x", pady=(4, 6))

        self.btn_fetch_channels = tk.Button(
            toolbar,
            text="🔄 Conectar a Discord y Cargar Canales",
            command=self._on_fetch_channels_click,
            bg="#4e5058",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.btn_fetch_channels.pack(side="left")

        tk.Button(
            toolbar, text="Ninguno", command=self._deselect_all_channels,
            bg=self.BG_INPUT, fg=self.FG_MUTED, relief="flat", padx=6, pady=3, font=("Segoe UI", 8), cursor="hand2"
        ).pack(side="right", padx=(4, 0))

        tk.Button(
            toolbar, text="Todos", command=self._select_all_channels,
            bg=self.BG_INPUT, fg=self.FG_TEXT, relief="flat", padx=6, pady=3, font=("Segoe UI", 8), cursor="hand2"
        ).pack(side="right")

        # Buscador rápido de canales
        search_bar = tk.Frame(card, bg=self.BG_PANEL)
        search_bar.pack(fill="x", pady=(0, 6))
        tk.Label(search_bar, text="🔎 Filtrar:", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))
        tk.Entry(
            search_bar, textvariable=self.var_search_channel, bg=self.BG_INPUT, fg=self.FG_TEXT,
            insertbackground=self.FG_TEXT, relief="flat", font=("Segoe UI", 9)
        ).pack(side="left", fill="x", expand=True, ipady=2)

        # Área scrollable con checkboxes de canales
        list_container = tk.Frame(card, bg=self.BG_DARK, highlightthickness=1, highlightbackground="#3f4147")
        list_container.pack(fill="both", expand=True)

        self.canvas_channels = tk.Canvas(list_container, bg=self.BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.canvas_channels.yview)
        self.frame_channels_list = tk.Frame(self.canvas_channels, bg=self.BG_DARK, padx=8, pady=6)

        self.frame_channels_list.bind(
            "<Configure>",
            lambda e: self.canvas_channels.configure(scrollregion=self.canvas_channels.bbox("all"))
        )
        self.canvas_channels.create_window((0, 0), window=self.frame_channels_list, anchor="nw")
        self.canvas_channels.configure(yscrollcommand=scrollbar.set)

        self.canvas_channels.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Soporte para rueda del mouse sobre la lista de canales
        self.canvas_channels.bind_all("<MouseWheel>", self._on_mousewheel)

        tk.Label(
            self.frame_channels_list,
            text="Haz clic en '🔄 Conectar a Discord y Cargar Canales'\npara ver y marcar con casillas los canales de tu servidor.",
            bg=self.BG_DARK,
            fg=self.FG_MUTED,
            justify="left",
            font=("Segoe UI", 9),
            pady=16,
        ).pack(anchor="w")

    def _build_actions_card(self, parent):
        card = self._create_card(parent, "🚀 3. Ejecución")
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        btn_row = tk.Frame(card, bg=self.BG_PANEL)
        btn_row.pack(fill="x")
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.btn_dry_run = tk.Button(
            btn_row,
            text="🔍 Simular (Dry Run)",
            command=lambda: self._start_sync_thread(dry_run=True),
            bg="#4e5058",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            pady=10,
            cursor="hand2",
        )
        self.btn_dry_run.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_run_backup = tk.Button(
            btn_row,
            text="🚀 Ejecutar Respaldo Ahora",
            command=lambda: self._start_sync_thread(dry_run=False),
            bg=self.ACCENT_GREEN,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            pady=10,
            cursor="hand2",
        )
        self.btn_run_backup.grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def _build_console_card(self, parent):
        card = self._create_card(parent, "🖥️ Consola de Progreso en Vivo")
        card.grid(row=1, column=0, sticky="nsew")

        top_bar = tk.Frame(card, bg=self.BG_PANEL)
        top_bar.pack(fill="x", pady=(0, 6))

        tk.Label(
            top_bar,
            text="Salida en tiempo real del proceso de sincronización:",
            bg=self.BG_PANEL,
            fg=self.FG_MUTED,
            font=("Segoe UI", 8),
        ).pack(side="left")

        tk.Button(
            top_bar,
            text="🧹 Limpiar Consola",
            command=self._clear_console,
            bg=self.BG_INPUT,
            fg=self.FG_MUTED,
            relief="flat",
            padx=8,
            pady=2,
            font=("Segoe UI", 8),
            cursor="hand2",
        ).pack(side="right")

        self.txt_console = ScrolledText(
            card,
            bg="#111214",
            fg="#dbdee1",
            insertbackground="white",
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
            state="disabled",
        )
        self.txt_console.pack(fill="both", expand=True)

        # Tags de color para la consola
        self.txt_console.tag_config("success", foreground="#23a55a")
        self.txt_console.tag_config("error", foreground="#f23f43")
        self.txt_console.tag_config("warning", foreground="#f0b232")
        self.txt_console.tag_config("info", foreground="#5865f2")

    # ─────────────────────────────────────────────────────────────
    # LÓGICA DE CONFIGURACIÓN (.env y credentials.json)
    # ─────────────────────────────────────────────────────────────

    def _toggle_token_visibility(self):
        self.show_token = not self.show_token
        self.entry_token.configure(show="" if self.show_token else "•")

    def _load_env_into_form(self):
        if not os.path.exists(ENV_PATH):
            self._append_console("[INFO] No se encontró archivo .env. Completa los campos y presiona 'Guardar Configuración'.\n")
            return

        vals = dotenv_values(ENV_PATH)
        self.var_token.set(vals.get("DISCORD_TOKEN", ""))
        self.var_guild_id.set(vals.get("GUILD_ID", ""))
        self.var_drive_folder.set(vals.get("DRIVE_FOLDER_ID", ""))
        self.var_channel_ids.set(vals.get("CHANNEL_IDS", ""))
        self.var_category_name.set(vals.get("CATEGORY_NAME", "historico"))
        self.var_service_file.set(vals.get("SERVICE_ACCOUNT_FILE", "credentials.json") or "credentials.json")
        self.var_unpin.set(str(vals.get("UNPIN_AFTER_BACKUP", "False")).lower() in ("true", "1", "yes"))
        self.var_notify.set(str(vals.get("NOTIFY_IN_CHANNEL", "True")).lower() in ("true", "1", "yes"))
        self._append_console("[OK] Configuración cargada desde .env\n")

    def _save_env_from_form(self, silent=False):
        token = self.var_token.get().strip()
        guild_id = self.var_guild_id.get().strip()
        drive_folder = discord_pins_sync.extract_drive_folder_id(self.var_drive_folder.get())
        self.var_drive_folder.set(drive_folder)
        channel_ids = self.var_channel_ids.get().strip()
        category_name = self.var_category_name.get().strip()
        service_file = self.var_service_file.get().strip() or "credentials.json"
        unpin_str = "True" if self.var_unpin.get() else "False"
        notify_str = "True" if self.var_notify.get() else "False"

        env_content = (
            f"# Configuración generada desde la Interfaz Gráfica\n"
            f"DISCORD_TOKEN={token}\n"
            f"GUILD_ID={guild_id}\n"
            f"DRIVE_FOLDER_ID={drive_folder}\n"
            f"CHANNEL_IDS={channel_ids}\n"
            f"CATEGORY_NAME={category_name}\n"
            f"SERVICE_ACCOUNT_FILE={service_file}\n"
            f"UNPIN_AFTER_BACKUP={unpin_str}\n"
            f"NOTIFY_IN_CHANNEL={notify_str}\n"
            f"DRY_RUN=False\n"
        )
        try:
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.write(env_content)
            if not silent:
                self._append_console("[OK] Archivo .env guardado correctamente.\n")
                messagebox.showinfo("Configuración Guardada", "Los datos se guardaron correctamente en el archivo .env.")
            return True
        except Exception as e:
            messagebox.showerror("Error al guardar .env", str(e))
            return False

    def _refresh_credentials_status(self):
        cred_path = os.path.join(APP_DIR, self.var_service_file.get().strip() or "credentials.json")
        self.service_account_email = ""
        if os.path.exists(cred_path):
            try:
                with open(cred_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                email = data.get("client_email", "")
                self.service_account_email = email
                if email:
                    self.lbl_cred_info.configure(text=f"✔ {email}", fg=self.ACCENT_GREEN)
                else:
                    self.lbl_cred_info.configure(text="✔ credentials.json cargado", fg=self.ACCENT_GREEN)
            except Exception:
                self.lbl_cred_info.configure(text="⚠️ JSON inválido en credentials.json", fg=self.ACCENT_RED)
        else:
            self.lbl_cred_info.configure(text="❌ Falta credentials.json (usa 'Cargar JSON')", fg=self.ACCENT_RED)

    def _select_credentials_json(self):
        file_path = filedialog.askopenfilename(
            title="Seleccionar archivo JSON de Cuenta de Servicio de Google",
            filetypes=[("Archivos JSON", "*.json"), ("Todos los archivos", "*.*")],
        )
        if not file_path:
            return
        dest_path = os.path.join(APP_DIR, "credentials.json")
        try:
            if os.path.abspath(file_path) != os.path.abspath(dest_path):
                shutil.copyfile(file_path, dest_path)
            self.var_service_file.set("credentials.json")
            self._refresh_credentials_status()
            self._append_console(f"[OK] Archivo credentials.json importado correctamente.\n")
        except Exception as e:
            messagebox.showerror("Error al copiar JSON", f"No se pudo copiar el archivo:\n{e}")

    def _copy_service_email(self):
        if not getattr(self, "service_account_email", ""):
            messagebox.showwarning(
                "Sin correo de cuenta de servicio",
                "Primero carga un archivo credentials.json válido para copiar el correo de la Service Account."
            )
            return
        self.clipboard_clear()
        self.clipboard_append(self.service_account_email)
        self._append_console(f"[INFO] Email copiado al portapapeles: {self.service_account_email}\n")
        messagebox.showinfo(
            "Email Copiado",
            f"Se copió al portapapeles:\n\n{self.service_account_email}\n\n"
            "Compártele tu carpeta de Google Drive con permisos de Editor."
        )

    # ─────────────────────────────────────────────────────────────
    # SELECTOR VISUAL DE CANALES
    # ─────────────────────────────────────────────────────────────

    def _on_mousewheel(self, event):
        # Hacer scroll solo si el cursor está sobre el panel izquierdo
        widget = event.widget
        if str(widget).startswith(str(self.canvas_channels)) or str(widget).startswith(str(self.frame_channels_list)):
            self.canvas_channels.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_fetch_channels_click(self):
        if self.is_running:
            return
        token = self.var_token.get().strip()
        guild_id = self.var_guild_id.get().strip()
        if not token or not guild_id:
            messagebox.showwarning("Faltan datos", "Ingresa el Discord Bot Token y el Guild ID antes de cargar los canales.")
            return

        self.is_running = True
        self._set_status("Conectando a Discord...", self.ACCENT_AMBER)
        self.btn_fetch_channels.configure(state="disabled", text="⏳ Cargando canales...")
        self._append_console("\n[INFO] Conectando a Discord para obtener la lista de canales del servidor...\n")

        def _worker():
            try:
                guild_name, channels = discord_pins_sync.fetch_guild_channels(token, int(guild_id))
                self.after(0, lambda: self._on_channels_loaded(guild_name, channels, None))
            except Exception as e:
                self.after(0, lambda err=e: self._on_channels_loaded(None, [], err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_channels_loaded(self, guild_name, channels, error):
        self.is_running = False
        self.btn_fetch_channels.configure(state="normal", text="🔄 Conectar a Discord y Cargar Canales")

        if error:
            self._set_status("Error al cargar canales", self.ACCENT_RED)
            self._append_console(f"[ERROR] No se pudieron cargar los canales: {error}\n")
            messagebox.showerror("Error al conectar con Discord", str(error))
            return

        self._set_status(f"Servidor: {guild_name}", self.ACCENT_GREEN)
        self._append_console(f"[+] Servidor '{guild_name}' conectado: {len(channels)} canales de texto encontrados.\n")

        self.loaded_channels = channels
        current_selected = set()
        for raw_id in self.var_channel_ids.get().split(","):
            raw_id = raw_id.strip()
            if raw_id.isdigit():
                current_selected.add(int(raw_id))

        self.channel_vars.clear()
        for ch in channels:
            var = tk.BooleanVar(value=(ch["id"] in current_selected))
            var.trace_add("write", lambda *_: self._sync_channel_ids_from_checkboxes())
            self.channel_vars[ch["id"]] = var

        self._render_channel_checkboxes()

    def _render_channel_checkboxes(self):
        for child in self.frame_channels_list.winfo_children():
            child.destroy()

        if not self.loaded_channels:
            tk.Label(
                self.frame_channels_list,
                text="No se encontraron canales para mostrar.",
                bg=self.BG_DARK,
                fg=self.FG_MUTED,
                font=("Segoe UI", 9),
            ).pack(anchor="w")
            return

        query = self.var_search_channel.get().strip().lower()
        current_category = None

        for ch in self.loaded_channels:
            if query and query not in ch["name"].lower() and query not in ch["category"].lower():
                continue

            if ch["category"] != current_category:
                current_category = ch["category"]
                tk.Label(
                    self.frame_channels_list,
                    text=f"▾ {current_category.upper()}",
                    bg=self.BG_DARK,
                    fg=self.ACCENT_BLURPLE,
                    font=("Segoe UI", 8, "bold"),
                    pady=4,
                ).pack(anchor="w", pady=(6, 0))

            var = self.channel_vars[ch["id"]]
            perm_tag = "" if ch["can_read"] else " [🔒 Sin permiso de lectura]"
            fg_color = self.FG_TEXT if ch["can_read"] else self.FG_MUTED

            cb = tk.Checkbutton(
                self.frame_channels_list,
                text=f"#{ch['name']}  ({ch['id']}){perm_tag}",
                variable=var,
                bg=self.BG_DARK,
                fg=fg_color,
                selectcolor=self.BG_INPUT,
                activebackground=self.BG_DARK,
                activeforeground=fg_color,
                font=("Segoe UI", 9),
                anchor="w",
            )
            cb.pack(fill="x", padx=(8, 0))

    def _sync_channel_ids_from_checkboxes(self):
        selected_ids = [str(cid) for cid, var in self.channel_vars.items() if var.get()]
        self.var_channel_ids.set(",".join(selected_ids))

    def _select_all_channels(self):
        query = self.var_search_channel.get().strip().lower()
        for ch in self.loaded_channels:
            if not query or query in ch["name"].lower() or query in ch["category"].lower():
                if ch["can_read"] and ch["id"] in self.channel_vars:
                    self.channel_vars[ch["id"]].set(True)

    def _deselect_all_channels(self):
        for var in self.channel_vars.values():
            var.set(False)

    # ─────────────────────────────────────────────────────────────
    # EJECUCIÓN DEL RESPALDO Y CONSOLA EN VIVO
    # ─────────────────────────────────────────────────────────────

    def _start_sync_thread(self, dry_run=False):
        if self.is_running:
            messagebox.showinfo("Proceso en curso", "Ya hay una operación ejecutándose. Espera a que finalice.")
            return

        # Confirmación de seguridad si UNPIN_AFTER_BACKUP está activo en ejecución real
        if not dry_run and self.var_unpin.get():
            confirm = messagebox.askyesno(
                "Confirmar Desfijado Automático",
                "Tienes activada la opción 'Desfijar los mensajes del canal tras respaldarlos' (UNPIN_AFTER_BACKUP=True).\n\n"
                "Una vez subidos a Google Drive, los mensajes se desfijarán del canal de Discord.\n\n"
                "¿Deseas continuar?"
            )
            if not confirm:
                return

        if not self._save_env_from_form(silent=True):
            return

        self.is_running = True
        mode_label = "Simulación (Dry Run)" if dry_run else "Respaldo Real"
        self._set_status(f"Ejecutando {mode_label}...", self.ACCENT_AMBER)
        self.btn_dry_run.configure(state="disabled")
        self.btn_run_backup.configure(state="disabled")
        self.btn_fetch_channels.configure(state="disabled")

        self._append_console("\n" + "═" * 62 + "\n")
        self._append_console(f"▶ INICIANDO: {mode_label.upper()}\n")
        self._append_console("═" * 62 + "\n")

        def _worker():
            old_stdout, old_stderr = sys.stdout, sys.stderr
            redirector = QueueRedirector(self.log_queue)
            sys.stdout = redirector
            sys.stderr = redirector
            try:
                discord_pins_sync.main(dry_run_override=dry_run)
            except Exception as e:
                print(f"\n[ERROR] Fallo inesperado: {e}")
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                self.after(0, self._on_sync_finished)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_sync_finished(self):
        self.is_running = False
        self._set_status("● Listo", self.ACCENT_GREEN)
        self.btn_dry_run.configure(state="normal")
        self.btn_run_backup.configure(state="normal")
        self.btn_fetch_channels.configure(state="normal")
        self._append_console("═" * 62 + "\n")

    def _poll_log_queue(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self._append_console(msg)
            except queue.Empty:
                break
        self.after(80, self._poll_log_queue)

    def _append_console(self, text):
        self.txt_console.configure(state="normal")
        for line in text.splitlines(keepends=True):
            tag = None
            if "[ERROR" in line or "[!]" in line or "[-]" in line or "❌" in line:
                tag = "error"
            elif "[+]" in line or "[OK]" in line or "exitosa" in line or "éxito" in line:
                tag = "success"
            elif "Advertencia" in line or "⚠️" in line or "UNPIN_AFTER_BACKUP" in line:
                tag = "warning"
            elif "[DRY RUN]" in line or "[INFO]" in line or "🔍" in line:
                tag = "info"

            if tag:
                self.txt_console.insert("end", line, tag)
            else:
                self.txt_console.insert("end", line)

        self.txt_console.see("end")
        self.txt_console.configure(state="disabled")

    def _clear_console(self):
        self.txt_console.configure(state="normal")
        self.txt_console.delete("1.0", "end")
        self.txt_console.configure(state="disabled")

    def _set_status(self, text, color):
        self.lbl_status.configure(text=text, fg=color)


if __name__ == "__main__":
    app = DiscordPinsApp()
    app.mainloop()
