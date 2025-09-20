```
sudo apt-get update && sudo apt-get upgrade
```


```
sudo apt install python3-pip python3-dev nginx
```


sudo apt-get update && sudo apt-get upgrade -y

sudo apt install python3-venv -y

sudo apt install python3-pip python3-venv python3-dev nginx -y

mkdir ~/PGMS

cd ~/PGMS

python3 -m venv venv

source venv/bin/activate

pip install --upgrade pip

https://github.com/Mr-Jerry-Haxor/PG-MS.git
cd PG-MS

pip install -r requirements.txt

python manage.py migrate

copy paste  .env, credentials.json, service_account.json , token.json files using nano


python manage.py collectstatic --noinput


pip install django gunicorn


sudo ufw allow 8000

(check for issues)

python manage.py runserver    

source ~/PGMS/venv/bin/activate

gunicorn --bind 0.0.0.0:8000 pgms.wsgi:application


config


```
sudo nano /etc/systemd/system/gunicorn.socket
```

paste in above file 

```
[Unit]
Description=gunicorn socket

[Socket]
ListenStream=/run/gunicorn.sock

[Install]
WantedBy=sockets.target
```


sudo nano /etc/systemd/system/gunicorn.service


```
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


sudo systemctl start gunicorn.socket

sudo systemctl status gunicorn.socket



```
sudo systemctl enable gunicorn.socket
```


sudo nano /etc/nginx/sites-available/pgms

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

sudo ln -s /etc/nginx/sites-available/pgms /etc/nginx/sites-enabled/

sudo nginx -t
sudo systemctl restart nginx

sudo apt install certbot python3-certbot-nginx

sudo certbot --nginx -d  pgms.devhost.my

sudo nginx -t
sudo systemctl restart nginx

sudo cat /etc/nginx/sites-available/pgms
