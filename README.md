# 🧰 Log Downloader

A lightweight Flask-based web service to remotely fetch and download logs from servers over SSH.
It supports filtering logs by date and automatically zips large files before sending them.



## ⚙️ Requirements

Python 3.10+ (recommended: 3.11 or 3.12)
pip (Python package manager)
SSH key access to target servers (passwordless preferred)



## 🪄 Installation Guide

### 1️⃣ Clone the Repository
```bash
git clone https://github.com/<your-username>/log-downloader.git
cd log-downloader
```

### 2️⃣ Create a Virtual Environment
```bash
python3 -m venv venv
```
### 3️⃣ Activate the Virtual Environment
#### For Linux / macOS:
```bash
source venv/bin/activate
```

#### For Windows (PowerShell):
```bash
venv\Scripts\activate
```



## 📦 Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```



## ⚙️ Configuration

Create or edit your config.xml file in the root directory.
Here’s an example:

```bash
<projects>
  <project name="MyApp">
    <module name="backend" 
            path="/var/log/myapp/backend" 
            base="app" 
            host="192.168.1.10" 
            user="ubuntu" />
  </project>
</projects>
```

### Tags:
name → project/module name
path → directory on remote server
base → log filename prefix (e.g., module-01-11-2025.log)
host → SSH hostname or IP
user → SSH username



## 🔑 SSH Configuration
Ensure your machine has SSH access to the remote host:

```bash
ssh ubuntu@192.168.1.10
```
If your private key is in a custom location, update SSH_KEY_PATH in app.py.



## 🚀 Run the Application
From your project directory:

```bash
python app.py
```
You’ll see output like:

```bash
* Running on all addresses (0.0.0.0)
* Flask app 'app' running on http://0.0.0.0:5000
```


## 🌐 API Endpoints
### 🔹 List Projects

```bash
GET /projects
```
### 🔹 List Modules for a Project

```bash
GET /modules/<project>
```

### 🔹 Download a Log File

```bash
GET /download?project=MyApp&module=backend&date=2025-11-01
```

### Optional parameters:

date → Filter by date (e.g., 2025-11-01 or 01-11-2025)
ssh_user → Override SSH user (if different from config)

 

## 📁 Example Usage
```bash
curl -O "http://localhost:5000/download?project=MyApp&module=backend&date=2025-11-01"
```

If the log file is large (>20 MB), it will be automatically zipped.



## 🧹 Temporary Files
All downloaded files are stored temporarily in:
```bash
temp_downloads/
```

They’re automatically deleted after sending the response.



## 🧹 Temporary Files
All downloaded files are stored temporarily in:
```bash
temp_downloads/
```

They’re automatically deleted after sending the response.


## ⚙️ Production Deployment
For production, use a WSGI server like gunicorn.

### Install Gunicorn:
```bash
pip install gunicorn
```

### Run with Gunicorn:
```bash
gunicorn --bind 0.0.0.0:5000 --workers 3 app:app
```

## 🧠 Notes
Use only on trusted networks — Flask’s built-in server is for testing/development.

Works best with SSH key-based authentication.

All operations are logged under log-downloader logger.

## 🐞 Troubleshooting
### Permission denied / SSH issues

```bash
# Ensure your public key is added on target server
cat ~/.ssh/id_rsa.pub | ssh user@host 'cat >> ~/.ssh/authorized_keys'
```
### Invalid date format

```text
Use either YYYY-MM-DD or DD-MM-YYYY
```

### File not found

```bash
Check your config.xml → path, base, and host entries.
```
