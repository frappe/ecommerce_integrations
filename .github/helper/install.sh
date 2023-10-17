#!/bin/bash

set -e

cd ~ || exit

sudo apt-get update
sudo apt-get -y remove mysql-server mysql-client
sudo apt-get -y install redis-server libcups2-dev mariadb-client-10.6 -qq

pip install frappe-bench

git clone https://github.com/frappe/frappe --branch develop --depth 1
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site
cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

mysql --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mysql --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

mysql --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE USER 'test_frappe'@'localhost' IDENTIFIED BY 'test_frappe'"
mysql --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE DATABASE test_frappe"
mysql --host 127.0.0.1 --port 3306 -u root -proot -e "GRANT ALL PRIVILEGES ON \`test_frappe\`.* TO 'test_frappe'@'localhost'"

mysql --host 127.0.0.1 --port 3306 -u root -proot -e "FLUSH PRIVILEGES"

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app payments --branch develop
bench get-app erpnext --branch develop

bench start &
bench --site test_site reinstall --yes

bench get-app ecommerce_integrations "${GITHUB_WORKSPACE}"
bench --site test_site install-app ecommerce_integrations
bench setup requirements --dev
