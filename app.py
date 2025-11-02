import os
import tempfile
import subprocess
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
XML_PATH = "config.xml"
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa")  # change if needed
TEMP_DIR = "temp_downloads"
LOG_DIR = "logs"
MAX_ZIP_MB = 20  # Zip logs larger than this (in MB)

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ==========================================================
# FLASK + LOGGING SETUP
# ==========================================================
app = Flask(__name__)

# Rotating log file: keeps console clean & saves logs persistently
LOG_FILE = os.path.join(LOG_DIR, "app.log")
handler = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=5)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)

app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

log = app.logger


# ==========================================================
# UTILS
# ==========================================================
def parse_xml():
    """Parse config.xml to extract projects and modules."""
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    projects = {}
    for p in root.findall("project"):
        pname = p.get("name")
        modules = {}
        for m in p.findall("module"):
            mname = m.get("name")
            modules[mname] = {
                "path": m.get("path"),
                "base": m.get("base"),
                "server": m.get("host") or m.get("server"),
                "user": m.get("user")
            }
        projects[pname] = modules
    return projects


def get_module_config(project, module):
    projects = parse_xml()
    if project not in projects:
        return None
    return projects[project].get(module)


# ==========================================================
# SSH HELPERS
# ==========================================================
def _get_pkey_object(pkey_path):
    if not pkey_path:
        return None
    try:
        return paramiko.RSAKey.from_private_key_file(pkey_path)
    except Exception as e:
        log.warning("Unable to load private key from %s: %s", pkey_path, e)
        return None


def ssh_run_command(host, command, username=None, pkey_path=None, timeout=30):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = _get_pkey_object(pkey_path)

    try:
        if pkey:
            log.info("SSH connect -> host=%s user=%s key=%s", host, username, pkey_path)
            client.connect(hostname=host, username=username, pkey=pkey, timeout=timeout)
        else:
            log.info("SSH connect (agent/keys lookup) -> host=%s user=%s", host, username)
            client.connect(hostname=host, username=username,
                           allow_agent=True, look_for_keys=True, timeout=timeout)
        stdin, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        return out, err
    finally:
        try:
            client.close()
        except Exception:
            pass


def sftp_get(host, remote_path, local_path, username=None, pkey_path=None, timeout=30):
    try:
        if pkey_path:
            pkey = _get_pkey_object(pkey_path)
            transport = paramiko.Transport((host, 22))
            transport.connect(username=username, pkey=pkey)
            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.get(remote_path, local_path)
            sftp.close()
            transport.close()
        else:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, username=username, allow_agent=True,
                           look_for_keys=True, timeout=timeout)
            sftp = client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
            client.close()
    except Exception as e:
        log.exception("sftp_get failed: %s", e)
        raise


# ==========================================================
# CORE FUNCTIONALITY
# ==========================================================
def find_latest_log_via_ls(host, dirpath, base, include_gz=True, date_str=None, username=None):
    """Find log file on remote host by date or latest."""
    if date_str:
        try:
            d = dateparser.parse(date_str, settings={'DATE_ORDER': 'DMY'}).date()
            date_part = d.strftime("%d-%m-%Y")
        except Exception:
            raise ValueError("Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.")
        pattern = f"{base}-{date_part}.log*"
        cmd = f"ls -1 {os.path.join(dirpath, pattern)} 2>/dev/null | head -1"
    else:
        pattern = f"{base}*.log*"
        cmd = f"ls -t {os.path.join(dirpath, pattern)} 2>/dev/null | head -1"

    log.debug(f"DEBUG CMD: {cmd} | HOST: {host} | USER: {username}")
    out, err = ssh_run_command(host, cmd, username=username)
    if err and not out:
        return None
    return out.strip() if out else None


# ==========================================================
# ROUTES
# ==========================================================
@app.route("/projects", methods=["GET"])
def list_projects():
    projects = parse_xml()
    return jsonify({"projects": list(projects.keys())})


@app.route("/modules/<project>", methods=["GET"])
def list_modules(project):
    projects = parse_xml()
    if project not in projects:
        return jsonify({"modules": []}), 404
    return jsonify({"modules": list(projects[project].keys())})


@app.route("/download", methods=["GET"])
def download_log():
    project = request.args.get("project")
    module = request.args.get("module")
    date = request.args.get("date")

    if not project or not module:
        return jsonify({"error": "project and module required"}), 400

    mod_cfg = get_module_config(project, module)
    if not mod_cfg:
        return jsonify({"error": "project/module not found"}), 404

    username = request.args.get("ssh_user") or mod_cfg.get("user")
    host = mod_cfg.get("server")
    dirpath = mod_cfg.get("path")
    base = mod_cfg.get("base")

    log.info("Request for download: project=%s module=%s host=%s date=%s", project, module, host, date)
    log.info("Resolved SSH username=%s", username)

    try:
        remote_file = find_latest_log_via_ls(host, dirpath, base, date_str=date, username=username)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.exception("Error finding log")
        return jsonify({"error": f"error finding log: {e}"}), 500

    if not remote_file:
        return jsonify({"error": "no matching log file found on server"}), 404

    filename = os.path.basename(remote_file)
    local_temp = os.path.join(TEMP_DIR, f"{datetime.utcnow().timestamp()}_{filename}")

    try:
        sftp_get(host, remote_file, local_temp, username=username)
    except Exception as e:
        log.exception("SFTP download failed")
        return jsonify({"error": f"failed to download log: {e}"}), 500

    filesize = os.path.getsize(local_temp)
    if filesize > MAX_ZIP_MB * 1024 * 1024:
        zip_path = shutil.make_archive(local_temp, 'zip',
                                       root_dir=os.path.dirname(local_temp),
                                       base_dir=os.path.basename(local_temp))
        os.remove(local_temp)
        to_send = zip_path
    else:
        to_send = local_temp

    log.info("Served file %s to client %s for %s/%s", os.path.basename(to_send),
             request.remote_addr, project, module)

    try:
        return send_file(to_send, as_attachment=True)
    finally:
        try:
            if os.path.exists(to_send):
                os.remove(to_send)
        except Exception:
            log.exception("Error cleaning up temp file")


# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    log.info("Starting Central Log Downloader (Flask development mode)...")
    app.run(host="0.0.0.0", port=5000)
