# 🚀 Django Deployment Guide (PGMS Project)

This file contains the full step‑by‑step deployment guide for your **PGMS** Django project using **Gunicorn** and **Nginx** on Ubuntu (PEP‑668 safe).

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

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        root /home/ubuntu/PGMS/PG-MS;
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

## 10. Enable HTTPS with Let’s Encrypt

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

## ✅ Verification

- Visit `http://pgms.devhost.my` → should load PGMS via Nginx + Gunicorn.
- Visit `https://pgms.devhost.my` → should be secured with Let’s Encrypt SSL.

```
# for pull and update 

git pull && sudo systemctl restart gunicorn.socket
```
