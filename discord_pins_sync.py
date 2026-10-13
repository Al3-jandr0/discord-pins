import os
import io
import re
import json
import asyncio
import requests
import discord
from datetime import datetime, timezone
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# Cargar variables de entorno
load_dotenv()

# Obtener y validar variables de entorno
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_STR = os.getenv("GUILD_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
CATEGORY_NAME = os.getenv("CATEGORY_NAME", "").strip()
CHANNEL_IDS_STR = os.getenv("CHANNEL_IDS", "").strip()
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "credentials.json")
DRY_RUN = os.getenv("DRY_RUN", "False").lower() in ("true", "1", "yes")
UNPIN_AFTER_BACKUP = os.getenv("UNPIN_AFTER_BACKUP", "False").lower() in ("true", "1", "yes")
NOTIFY_IN_CHANNEL = os.getenv("NOTIFY_IN_CHANNEL", "True").lower() in ("true", "1", "yes")

if not DISCORD_TOKEN:
    raise ValueError("Error: DISCORD_TOKEN no está definido en el archivo .env")
if not GUILD_ID_STR:
    raise ValueError("Error: GUILD_ID no está definido en el archivo .env")
if not DRIVE_FOLDER_ID:
    raise ValueError("Error: DRIVE_FOLDER_ID no está definido en el archivo .env")

try:
    GUILD_ID = int(GUILD_ID_STR)
except ValueError:
    raise ValueError("Error: GUILD_ID debe ser un número entero válido")

# Parsear lista de IDs de canales (si se especificó)
CHANNEL_IDS = []
if CHANNEL_IDS_STR:
    for cid in CHANNEL_IDS_STR.split(","):
        cid = cid.strip()
        if cid:
            try:
                CHANNEL_IDS.append(int(cid))
            except ValueError:
                print(f"Advertencia: '{cid}' no es un ID de canal válido y será ignorado.")


# --- FUNCIONES AUXILIARES DE GOOGLE DRIVE ---

def get_drive_service(credentials_path):
    """Autentica con la Cuenta de Servicio y devuelve el cliente de Google Drive."""
    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            f"No se encontró el archivo de credenciales de Google Drive: {credentials_path}. "
            "Por favor, colócalo en el directorio del proyecto."
        )
    scopes = ['https://www.googleapis.com/auth/drive']
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=scopes
    )
    return build('drive', 'v3', credentials=credentials)


def find_folder(service, folder_name, parent_id):
    """Busca una carpeta por nombre dentro de una carpeta padre en Drive (soporta Shared Drives)."""
    escaped_name = folder_name.replace("'", "\\'")
    query = (
        f"name = '{escaped_name}' and "
        f"'{parent_id}' in parents and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )
    results = service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    items = results.get('files', [])
    if items:
        return items[0]['id']
    return None


def find_or_create_folder(service, folder_name, parent_id):
    """Busca o crea una carpeta en Google Drive. Retorna (folder_id, was_created)."""
    folder_id = find_folder(service, folder_name, parent_id)
    if folder_id:
        return folder_id, False
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    file = service.files().create(
        body=file_metadata,
        fields='id',
        supportsAllDrives=True
    ).execute()
    return file.get('id'), True


def get_folder_link(folder_id):
    """Genera el link de Google Drive para una carpeta dado su ID."""
    return f"https://drive.google.com/drive/folders/{folder_id}"


def find_file(service, file_name, folder_id):
    """Busca un archivo por nombre dentro de una carpeta en Drive (soporta Shared Drives)."""
    escaped_name = file_name.replace("'", "\\'")
    query = (
        f"name = '{escaped_name}' and "
        f"'{folder_id}' in parents and "
        "mimeType != 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )
    results = service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    items = results.get('files', [])
    if items:
        return items[0]['id']
    return None


def upload_file(service, local_path, file_name, folder_id, mime_type=None):
    """Sube un archivo local a una carpeta de Google Drive (soporta Shared Drives)."""
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink',
        supportsAllDrives=True
    ).execute()
    return file.get('id'), file.get('webViewLink')


def update_file(service, file_id, local_path, mime_type=None):
    """Actualiza el contenido de un archivo existente en Google Drive (soporta Shared Drives)."""
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    file = service.files().update(
        fileId=file_id,
        media_body=media,
        fields='id, webViewLink',
        supportsAllDrives=True
    ).execute()
    return file.get('id'), file.get('webViewLink')


def download_file_content(service, file_id):
    """Descarga el contenido de un archivo de texto de Google Drive como string."""
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        _, done = downloader.next_chunk()
    return fh.getvalue().decode('utf-8')


# --- OTRAS FUNCIONES AUXILIARES ---

def sanitize_filename(filename):
    """Sanitiza el nombre del archivo reemplazando caracteres inválidos."""
    name, ext = os.path.splitext(filename)
    name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
    return f"{name}{ext}"


def is_synced(history, guild_id, channel_id, message_id):
    """Verifica si un mensaje ya ha sido sincronizado."""
    g_id = str(guild_id)
    c_id = str(channel_id)
    m_id = str(message_id)
    return m_id in history.get("guilds", {}).get(g_id, {}).get("channels", {}).get(c_id, [])


def mark_as_synced(history, guild_id, channel_id, message_id):
    """Registra un mensaje como sincronizado en el historial local."""
    g_id = str(guild_id)
    c_id = str(channel_id)
    m_id = str(message_id)
    if "guilds" not in history:
        history["guilds"] = {}
    if g_id not in history["guilds"]:
        history["guilds"][g_id] = {"channels": {}}
    if "channels" not in history["guilds"][g_id]:
        history["guilds"][g_id]["channels"] = {}
    if c_id not in history["guilds"][g_id]["channels"]:
        history["guilds"][g_id]["channels"][c_id] = []

    if m_id not in history["guilds"][g_id]["channels"][c_id]:
        history["guilds"][g_id]["channels"][c_id].append(m_id)


# --- CLIENTE DE DISCORD ---

class DiscordPinsBackupClient(discord.Client):
    def __init__(self, *args, **kwargs):
        self.guild_id = kwargs.pop('guild_id')
        self.category_name = kwargs.pop('category_name', None)
        self.channel_ids = kwargs.pop('channel_ids', [])
        self.drive_folder_id = kwargs.pop('drive_folder_id')
        self.drive_service = kwargs.pop('drive_service')
        self.dry_run = kwargs.pop('dry_run', False)
        self.unpin_after_backup = kwargs.pop('unpin_after_backup', False)
        self.notify_in_channel = kwargs.pop('notify_in_channel', True)
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print(f"Conectado a Discord como {self.user}")
        try:
            await self.run_backup()
        except Exception as e:
            print(f"\n[ERROR CRÍTICO] Ocurrió un fallo durante la ejecución: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print("\nCerrando sesión de Discord...")
            await self.close()

    async def run_backup(self):
        guild = self.get_guild(self.guild_id)
        if not guild:
            print(f"Error: No se pudo encontrar el servidor (Guild) con ID: {self.guild_id}")
            return

        print(f"Servidor detectado: {guild.name}")

        # --- MEJORA 1: Filtrar canales por IDs específicos, categoría, o todos ---
        channels = []
        if self.channel_ids:
            print(f"Modo: Canales específicos por ID ({len(self.channel_ids)} canales configurados).")
            for ch_id in self.channel_ids:
                ch = guild.get_channel(ch_id)
                if ch and isinstance(ch, discord.TextChannel):
                    channels.append(ch)
                else:
                    print(f"  Advertencia: No se encontró el canal con ID {ch_id} o no es un canal de texto.")
        elif self.category_name:
            category = None
            for cat in guild.categories:
                if cat.name.lower() == self.category_name.lower():
                    category = cat
                    break
            if not category:
                print(f"Advertencia: No se encontró la categoría '{self.category_name}'. Sincronizando todos los canales de texto.")
                channels = guild.text_channels
            else:
                print(f"Modo: Categoría '{category.name}'.")
                channels = list(category.text_channels)
        else:
            print("Modo: Todos los canales de texto del servidor.")
            channels = guild.text_channels

        channels = [c for c in channels if isinstance(c, discord.TextChannel)]

        if not channels:
            print("No se encontraron canales de texto para procesar.")
            return

        print(f"Total de canales de texto a procesar: {len(channels)}")

        # Cargar historial de sincronización
        history_path = 'sync_history.json'
        history = {}
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except Exception as e:
                print(f"Error al leer {history_path}. Se creará un historial nuevo. Detalle: {e}")

        temp_dir = 'temp_downloads'
        os.makedirs(temp_dir, exist_ok=True)

        # --- MEJORA 2: Carpeta de fecha en Drive (se crea solo si hay mensajes nuevos) ---
        run_date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        print(f"\nFecha de ejecución: {run_date_str}")
        date_folder_id = None

        for channel in channels:
            # Validar permisos mínimos
            permissions = channel.permissions_for(guild.me)
            if not permissions.read_messages or not permissions.read_message_history:
                print(f"\n[-] Sin permisos para leer el canal: #{channel.name}. Saltando...")
                continue

            print(f"\n[+] Procesando canal: #{channel.name} (ID: {channel.id})")

            try:
                pins = [message async for message in channel.pins()]
            except Exception as e:
                print(f"    Error al obtener mensajes fijados: {e}")
                continue

            if not pins:
                print(f"    El canal #{channel.name} no tiene mensajes fijados.")
                continue

            # Procesar del más antiguo al más nuevo (orden cronológico)
            pins.reverse()

            # Filtrar mensajes no sincronizados
            unsynced_pins = [
                pin for pin in pins
                if not is_synced(history, self.guild_id, channel.id, pin.id)
            ]

            if not unsynced_pins:
                print(f"    Todos los mensajes fijados ({len(pins)}) en #{channel.name} ya están respaldados.")
                continue

            print(f"    Se detectaron {len(unsynced_pins)} mensajes fijados nuevos para respaldar.")

            if self.dry_run:
                print(f"    [DRY RUN] Los siguientes mensajes serían respaldados en #{channel.name}:")
                for p in unsynced_pins:
                    snippet = p.content[:60].replace('\n', ' ')
                    print(f"      - ID {p.id} | @{p.author} | {p.created_at.strftime('%Y-%m-%d')} | \"{snippet}\" | Adjuntos: {len(p.attachments)}")
                if self.unpin_after_backup:
                    print(f"    [DRY RUN] Se desfijarían {len(unsynced_pins)} mensajes de #{channel.name}.")
                if self.notify_in_channel:
                    print(f"    [DRY RUN] Se enviaría un mensaje de notificación a #{channel.name}.")
                continue

            # Crear u obtener carpeta de fecha solo cuando realmente hay mensajes para subir
            if not date_folder_id:
                date_folder_id, created_date = find_or_create_folder(self.drive_service, run_date_str, self.drive_folder_id)
                if created_date:
                    print(f"    Creada carpeta de fecha en Drive: '{run_date_str}' (ID: {date_folder_id})")
                else:
                    print(f"    Carpeta de fecha existente: '{run_date_str}' (ID: {date_folder_id})")

            # Buscar o crear carpeta del canal dentro de la carpeta de fecha
            channel_folder_id, created = find_or_create_folder(self.drive_service, channel.name, date_folder_id)
            if created:
                print(f"    Creada carpeta en Drive: '{run_date_str}/{channel.name}' (ID: {channel_folder_id})")
            else:
                print(f"    Carpeta existente en Drive: '{run_date_str}/{channel.name}' (ID: {channel_folder_id})")

            channel_folder_link = get_folder_link(channel_folder_id)
            new_markdown_entries = []
            backed_up_pins = []

            for pin in unsynced_pins:
                print(f"    Respaldando mensaje {pin.id} de @{pin.author} ({pin.created_at.strftime('%Y-%m-%d %H:%M')} UTC)")

                attachments_list = []
                for att in pin.attachments:
                    sanitized_name = sanitize_filename(att.filename)
                    drive_filename = f"{pin.id}_{sanitized_name}"
                    local_path = os.path.join(temp_dir, drive_filename)

                    print(f"      Descargando adjunto: {att.filename}...")
                    try:
                        resp = requests.get(att.url, stream=True, timeout=60)
                        if resp.status_code == 200:
                            with open(local_path, 'wb') as f:
                                for chunk in resp.iter_content(chunk_size=8192):
                                    f.write(chunk)

                            print(f"      Subiendo a Google Drive...")
                            drive_file_id, drive_link = upload_file(
                                self.drive_service, local_path, drive_filename, channel_folder_id, att.content_type
                            )
                            print(f"      Subido con éxito. ID: {drive_file_id}")
                            attachments_list.append((att.filename, drive_link))
                        else:
                            print(f"      [!] Error al descargar adjunto {att.filename}: HTTP {resp.status_code}")
                    except Exception as e:
                        print(f"      [!] Error al descargar/subir adjunto {att.filename}: {e}")
                    finally:
                        if os.path.exists(local_path):
                            try:
                                os.remove(local_path)
                            except Exception as ex:
                                print(f"      Error al eliminar archivo temporal {local_path}: {ex}")

                # Formatear contenido del mensaje
                content = pin.content or ""

                if pin.embeds:
                    embed_texts = []
                    for embed in pin.embeds:
                        parts = []
                        if embed.title:
                            parts.append(f"**[Embed: {embed.title}]**")
                        if embed.description:
                            parts.append(embed.description)
                        for field in embed.fields:
                            parts.append(f"**{field.name}**: {field.value}")
                        if parts:
                            embed_texts.append("\n".join(parts))
                    if embed_texts:
                        if content:
                            content += "\n\n"
                        content += "*Contenido del Embed:*\n" + "\n\n".join(embed_texts)

                if not content and not attachments_list:
                    content = "*(Mensaje sin texto o solo multimedia)*"

                date_str = pin.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')
                attachments_md = ""
                if attachments_list:
                    attachments_md = "\n**Archivos Adjuntos:**\n" + "\n".join(
                        [f"- [{name}]({link})" for name, link in attachments_list]
                    )

                entry = (
                    f"## Mensaje de @{pin.author} - {date_str}\n"
                    f"**Autor:** {pin.author} ({pin.author.id})\n"
                    f"**ID del Mensaje:** {pin.id}\n"
                    f"**Fecha (UTC):** {date_str}\n\n"
                    f"### Contenido:\n{content}\n"
                    f"{attachments_md}\n\n"
                    f"---\n\n"
                )
                new_markdown_entries.append(entry)
                backed_up_pins.append(pin)
                mark_as_synced(history, self.guild_id, channel.id, pin.id)
                await asyncio.sleep(0.5)

            # Subir resumen Markdown
            summary_filename = "mensajes_fijados.md"
            summary_file_id = find_file(self.drive_service, summary_filename, channel_folder_id)

            existing_content = ""
            if summary_file_id:
                print("    Descargando resumen actual desde Google Drive...")
                try:
                    existing_content = download_file_content(self.drive_service, summary_file_id)
                except Exception as e:
                    print(f"    [!] Error al descargar resumen actual: {e}. Se reescribirá el archivo.")

            if not existing_content:
                existing_content = (
                    f"# Mensajes Fijados de #{channel.name}\n\n"
                    f"Sincronizado desde Discord a Google Drive el {run_date_str}.\n\n"
                    f"---\n\n"
                )

            updated_content = existing_content + "".join(new_markdown_entries)
            local_summary_path = os.path.join(temp_dir, f"summary_{channel.id}.md")

            try:
                with open(local_summary_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)

                if summary_file_id:
                    print("    Actualizando archivo resumen en Google Drive...")
                    update_file(self.drive_service, summary_file_id, local_summary_path, "text/markdown")
                else:
                    print("    Creando nuevo archivo resumen en Google Drive...")
                    upload_file(self.drive_service, local_summary_path, summary_filename, channel_folder_id, "text/markdown")
            except Exception as e:
                print(f"    [!] Error al guardar o subir el resumen: {e}")
            finally:
                if os.path.exists(local_summary_path):
                    try:
                        os.remove(local_summary_path)
                    except:
                        pass

            # Guardar historial
            try:
                with open(history_path, 'w', encoding='utf-8') as f:
                    json.dump(history, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"    [!] Error al guardar el historial en {history_path}: {e}")

            # --- MEJORA 1: Desfijado automático tras el backup ---
            if self.unpin_after_backup and backed_up_pins:
                unpin_perms = channel.permissions_for(guild.me)
                if unpin_perms.manage_messages:
                    print(f"    Desfijando {len(backed_up_pins)} mensajes en #{channel.name}...")
                    for pin in backed_up_pins:
                        try:
                            await pin.unpin()
                            print(f"      Desfijado mensaje {pin.id}")
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            print(f"      [!] Error al desfijaar mensaje {pin.id}: {e}")
                else:
                    print(f"    [!] El bot no tiene permiso 'Manage Messages' en #{channel.name}. No se pudo desfijaar.")

            # --- MEJORA 3: Notificación en el canal de Discord ---
            if self.notify_in_channel and backed_up_pins:
                notify_perms = channel.permissions_for(guild.me)
                if notify_perms.send_messages:
                    count = len(backed_up_pins)
                    action_text = "desfijados y resguardados" if self.unpin_after_backup else "resguardados"
                    notify_msg = (
                        f"📦 **{count} mensaje(s) fijado(s) {action_text} exitosamente.**\n"
                        f"📂 Carpeta en Google Drive: {channel_folder_link}"
                    )
                    try:
                        await channel.send(notify_msg)
                        print(f"    Notificación enviada en #{channel.name}.")
                    except Exception as e:
                        print(f"    [!] Error al enviar notificación en #{channel.name}: {e}")
                else:
                    print(f"    [!] El bot no tiene permiso 'Send Messages' en #{channel.name}. No se pudo notificar.")

            print(f"    Canal #{channel.name} completado con éxito.")
            print(f"    Link de Drive: {channel_folder_link}")

        # Limpiar carpeta temporal
        try:
            os.rmdir(temp_dir)
        except:
            pass

        print("\n[+] Sincronización finalizada correctamente.")


# --- FLUJO PRINCIPAL ---

def main():
    print("Iniciando bot extractor de mensajes fijados de Discord...")

    # Mostrar modo de operación
    if CHANNEL_IDS:
        print(f"Modo: Canales específicos → IDs: {CHANNEL_IDS}")
    elif CATEGORY_NAME:
        print(f"Modo: Categoría → '{CATEGORY_NAME}'")
    else:
        print("Modo: Todos los canales del servidor.")

    if UNPIN_AFTER_BACKUP:
        print("⚠️  UNPIN_AFTER_BACKUP=True → Los mensajes fijados serán desfijados tras el backup.")
    if DRY_RUN:
        print("🔍 DRY_RUN=True → Simulación activada. No se subirá nada ni se desfijará nada.")

    # 1. Autenticación con Google Drive
    try:
        drive_service = get_drive_service(SERVICE_ACCOUNT_FILE)
        print("Autenticación con Google Drive exitosa.")
    except Exception as e:
        print(f"\n[ERROR DE CONFIGURACIÓN] No se pudo inicializar Google Drive API: {e}")
        print("Asegúrate de tener el archivo JSON de tu Cuenta de Servicio y configura SERVICE_ACCOUNT_FILE.")
        return

    # 2. Configurar Intents de Discord
    intents = discord.Intents.default()
    intents.guilds = True
    intents.message_content = True
    intents.members = True

    # 3. Inicializar e iniciar el cliente
    client = DiscordPinsBackupClient(
        intents=intents,
        guild_id=GUILD_ID,
        category_name=CATEGORY_NAME,
        channel_ids=CHANNEL_IDS,
        drive_folder_id=DRIVE_FOLDER_ID,
        drive_service=drive_service,
        dry_run=DRY_RUN,
        unpin_after_backup=UNPIN_AFTER_BACKUP,
        notify_in_channel=NOTIFY_IN_CHANNEL,
    )

    try:
        client.run(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        print("\n[ERROR DE CONFIGURACIÓN] El token de Discord provisto no es válido.")
        print("Por favor, verifica la variable DISCORD_TOKEN en tu archivo .env.")
    except Exception as e:
        print(f"\n[ERROR] No se pudo conectar a Discord: {e}")


if __name__ == "__main__":
    main()
