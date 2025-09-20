# 🚀 Django Deployment Guide (PGMS Project)

This file contains the full step-by-step deployment guide for your **PGMS** Django project using **Gunicorn** and **Nginx** on Ubuntu (PEP-668 safe).

---

## 1. Update and Install System Packages

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt install python3-pip python3-venv python3-dev nginx -y
```

---

## 2. Setup Project Directory and Virtual Environment

```bash
mkdir ~/PGMS
cd ~/PGMS

# Create virtual environment safely
python3 -m venv venv
source venv/bin/activate

# Upgrade pip inside venv
pip install --upgrade pip
```

---

## 3. Clone Your Project Repository

```bash
git clone https://github.com/Mr-Jerry-Haxor/PG-MS.git
cd PG-MS
```

---

## 4. Install Project Dependencies

```bash
pip install -r requirements.txt
```

Copy any required secret files into the project (e.g. `.env`, `credentials.json`, `service_account.json`, `token.json`). You can use `nano` or `scp` to add them.

---

## 5. Run Django Setup Commands

```bash
# Apply database migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput
```

---

## 6. Install Django and Gunicorn (if not already in requirements.txt)

```bash
pip install django gunicorn
```

---

## 7. Test Locally Before Configuring Services

```bash
# Allow test port
sudo ufw allow 8000

# Run Django dev server (check for issues)
python manage.py runserver 0.0.0.0:8000

# Test Gunicorn
source ~/PGMS/venv/bin/activate
gunicorn --bind 0.0.0.0:8000 pgms.wsgi:application
```

---

## 8. Configure Gunicorn with systemd

### Create **gunicorn.socket**

```bash
sudo nano /etc/systemd/system/gunicorn.socket
```

Paste:

```ini
[Unit]
Description=gunicorn socket

[Socket]
ListenStream=/run/gunicorn.sock

[Install]
WantedBy=sockets.target
```

### Create **gunicorn.service**

```bash
sudo nano /etc/systemd/system/gunicorn.service
```

Paste:

```ini
[Unit]
Description=gunicorn daemon
Requires=gunicorn.socket
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/home/ubuntu/PGMS/PG-MS
ExecStart=/home/ubuntu/PGMS/venv/bin/gunicorn \
          --access-logfile - \
          --workers 3 \
          --bind unix:/run/gunicorn.sock \
          pgms.wsgi:application

[Install]
WantedBy=multi-user.target
```

### Start and Enable Gunicorn

```bash
sudo systemctl start gunicorn.socket
sudo systemctl status gunicorn.socket
sudo systemctl enable gunicorn.socket
```

---

## 9. Configure Nginx as Reverse Proxy

```bash
sudo nano /etc/nginx/sites-available/pgms
```

Paste:

```nginx
server {
    listen 80;
    server_name pgms.devhost.my;

    client_max_body_size 200M;   # allow up to 200 MB uploads
    client_body_timeout 300s;         # allow 5 minutes to send file
    proxy_read_timeout 300s;          # allow backend to process 5 min

    location = /favicon.ico { access_log off; log_not_found off; }

    # Serve static files
    location /static/ {
        alias /home/ubuntu/PGMS/PG-MS/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # Serve media files (user uploads)
    location /media/ {
        alias /home/ubuntu/PGMS/PG-MS/media/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/run/gunicorn.sock;
    }
}
```

Enable the site and restart Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/pgms /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 10. Fix Permissions for Static/Media Files

Ensure Nginx can traverse and read the directories:

```bash
sudo chown -R ubuntu:www-data /home/ubuntu/PGMS/PG-MS/staticfiles /home/ubuntu/PGMS/PG-MS/media

# Directories need execute permission
sudo find /home/ubuntu/PGMS/PG-MS/staticfiles -type d -exec chmod 755 {} \;
sudo find /home/ubuntu/PGMS/PG-MS/media -type d -exec chmod 755 {} \;

# Files need read permission
sudo find /home/ubuntu/PGMS/PG-MS/staticfiles -type f -exec chmod 644 {} \;
sudo find /home/ubuntu/PGMS/PG-MS/media -type f -exec chmod 644 {} \;

# Parent dirs must also be traversable
sudo chmod 755 /home /home/ubuntu /home/ubuntu/PGMS /home/ubuntu/PGMS/PG-MS
```

Reload Nginx after changes:

```bash
sudo systemctl reload nginx
```

---

## 11. Enable HTTPS with Let’s Encrypt

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d pgms.devhost.my
```

Test and restart:

```bash
sudo nginx -t
sudo systemctl restart nginx
```

---

## 12. Deployment Automation Script (Optional)

Create a `deploy.sh` script inside `~/PGMS`:

```bash
#!/bin/bash
set -e

cd /home/ubuntu/PGMS/PG-MS

echo "Pulling latest code..."
git pull origin main

source ../venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Running migrations..."
python manage.py migrate

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Restarting Gunicorn..."
sudo systemctl restart gunicorn
echo "Deployment complete."
```

Make it executable:

```bash
chmod +x ~/PGMS/deploy.sh
```

Now you can update your server with:

```bash
~/PGMS/deploy.sh
```

---

## ✅ Verification

- Visit `http://pgms.devhost.my` → should load PGMS via Nginx + Gunicorn.
- Visit `https://pgms.devhost.my` → should be secured with Let’s Encrypt SSL.
- Static files load from `/static/`.
- User uploads load from `/media/`.

```

```
