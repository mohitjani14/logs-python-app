import os
import tempfile
import shutil
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from xml.etree import ElementTree as ET
from flask import Flask, request, jsonify, send_file
import paramiko
from dateutil import parser as dateparser

# ==========================================================
# CONFIG
# ==========================================================
CONFIG_PATH = "config.xml"
CREDENTIALS_PATH = "credentials.xml"
TEMP_DIR = "temp_downloads"
LOG_DIR = "logs"
MAX_ZIP_MB = 20  # Zip logs larger than this (in MB)

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ==========================================================
# FLASK + LOGGING SETUP
# ==========================================================
app = Flask(__name__)

# App log
LOG_FILE = os.path.join(LOG_DIR, "app.log")
handler = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=5)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = app.logger

# Activity log
ACTIVITY_FILE = os.path.join(LOG_DIR, "activity.log")
activity_handler = RotatingFileHandler(ACTIVITY_FILE, maxBytes=10_000_000, backupCount=5)
activity_formatter = logging.Formatter("%(asctime)s | %(message)s")
activity_handler.setFormatter(activity_formatter)
activity_logger = logging.getLogger("activity")
activity_logger.setLevel(logging.INFO)
activity_logger.addHandler(activity_handler)

# ==========================================================
# XML PARSERS
# ==========================================================
def parse_config():
    """Parse config.xml for project and module info."""
    tree = ET.parse(CONFIG_PATH)
    root = tree.getroot()
    projects = {}
    for p in root.findall("project"):
        pname = p.get("name")
        modules = {}
        for m in p.findall("module"):
            mname = m.get("name")
            modules[mname] = {
                "server": m.get("server"),
                "user": m.get("user"),
                "path": m.get("path"),
                "base": m.get("base"),
                "pattern": m.get("pattern")
            }
        projects[pname] = modules
    return projects


def parse_credentials():
    """Parse credentials.xml for passwords."""
    tree = ET.parse(CREDENTIALS_PATH)
    root = tree.getroot()
    creds = {}
    for p in root.findall("project"):
        pname = p.get("name")
        server_elem = p.find("server")
        if server_elem is not None:
            creds[pname] = {
                "host": server_elem.get("host"),
                "user": server_elem.get("user"),
                "password": server_elem.get("password")
            }
    return creds

# ==========================================================
# SFTP HELPERS
# ==========================================================
def sftp_connect(host, username, password, timeout=30):
    """Establish SFTP connection with password auth."""
    try:
        transport = paramiko.Transport((host, 22))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        return sftp, transport
    except Exception as e:
        log.exception("SFTP connection failed: %s", e)
        activity_logger.error(f"SFTP connection failed for {username}@{host}: {e}")
        raise


def sftp_find_latest_log(sftp, dirpath, base, date_str=None):
    """Find latest or date-specific log file on remote SFTP server."""
    files = sftp.listdir(dirpath)
    if not files:
        return None

    def safe_parse_date(date_str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        try:
            d = dateparser.parse(date_str)
            if d:
                return d.date()
        except Exception:
            pass
        raise ValueError("Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.")

    if date_str:
        d = safe_parse_date(date_str)
        date_part = d.strftime("%d-%m-%Y")
        candidates = [f for f in files if f.startswith(f"{base}-{date_part}.log")]
    else:
        candidates = [f for f in files if f.startswith(base) and f.endswith(".log")]

    if not candidates:
        return None

    candidates.sort(key=lambda f: sftp.stat(os.path.join(dirpath, f)).st_mtime, reverse=True)
    return os.path.join(dirpath, candidates[0])

# ==========================================================
# ROUTES
# ==========================================================
@app.route("/projects", methods=["GET"])
def list_projects():
    projects = parse_config()
    return jsonify({"projects": list(projects.keys())})


@app.route("/modules/<project>", methods=["GET"])
def list_modules(project):
    projects = parse_config()
    if project not in projects:
        return jsonify({"modules": []}), 404
    return jsonify({"modules": list(projects[project].keys())})


@app.route("/download", methods=["GET"])
def download_log():
    project = request.args.get("project")
    module = request.args.get("module")
    date = request.args.get("date")
    client_ip = request.remote_addr

    if not project or not module:
        return jsonify({"error": "project and module required"}), 400

    config = parse_config()
    creds = parse_credentials()

    if project not in config or project not in creds:
        return jsonify({"error": "project not found in config or credentials"}), 404

    mod_cfg = config[project].get(module)
    cred = creds[project]

    if not mod_cfg:
        return jsonify({"error": "module not found"}), 404

    host = mod_cfg.get("server") or cred.get("host")
    username = mod_cfg.get("user") or cred.get("user")
    password = cred.get("password")
    dirpath = mod_cfg.get("path")
    base = mod_cfg.get("base")

    log.info("Download request: %s/%s from %s", project, module, host)
    activity_logger.info(f"Download request from {client_ip} for {project}/{module} on {host}")

    try:
        sftp, transport = sftp_connect(host, username, password)
        remote_file = sftp_find_latest_log(sftp, dirpath, base, date)
    except ValueError as e:
        activity_logger.error(f"Invalid date format from {client_ip}: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        activity_logger.error(f"SFTP error for {client_ip} [{project}/{module}]: {e}")
        log.exception("Error during SFTP operation")
        return jsonify({"error": f"error accessing remote logs: {e}"}), 500
    finally:
        try:
            sftp.close()
            transport.close()
        except Exception:
            pass

    if not remote_file:
        activity_logger.warning(f"No log found for {project}/{module} requested by {client_ip}")
        return jsonify({"error": "no matching log file found"}), 404

    filename = os.path.basename(remote_file)
    local_temp = os.path.join(TEMP_DIR, f"{datetime.utcnow().timestamp()}_{filename}")

    try:
        sftp, transport = sftp_connect(host, username, password)
        sftp.get(remote_file, local_temp)
        activity_logger.info(f"Downloaded {filename} for {project}/{module} by {client_ip}")
    except Exception as e:
        log.exception("SFTP download failed")
        activity_logger.error(f"Download failed for {project}/{module} by {client_ip}: {e}")
        return jsonify({"error": f"failed to download log: {e}"}), 500
    finally:
        try:
            sftp.close()
            transport.close()
        except Exception:
            pass

    if os.path.getsize(local_temp) > MAX_ZIP_MB * 1024 * 1024:
        zip_path = shutil.make_archive(local_temp, 'zip',
                                       root_dir=os.path.dirname(local_temp),
                                       base_dir=os.path.basename(local_temp))
        os.remove(local_temp)
        to_send = zip_path
    else:
        to_send = local_temp

    try:
        activity_logger.info(f"Served {os.path.basename(to_send)} to {client_ip} for {project}/{module}")
        return send_file(to_send, as_attachment=True)
    finally:
        try:
            if os.path.exists(to_send):
                os.remove(to_send)
        except Exception:
            pass


# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    log.info("Starting Central Log Downloader (password-based SFTP)...")
    activity_logger.info("Server started and ready to serve requests")
    app.run(host="0.0.0.0", port=5000)
