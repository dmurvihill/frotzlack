#!/bin/bash

cp -R /vagrant/* /home/vagrant
cd /home/vagrant

#  Install system requirements
apt-get update
apt-get install -y curl git make libffi-dev libssl-dev python python-dev python-pip python-virtualenv uudeview

#  Install Python requirements
pip install --upgrade setuptools
virtualenv -p python2.7 venv
source venv/bin/activate
pip install -q -r requirements.txt

#  Install Frotz/Zork
wget -q https://github.com/DavidGriffith/frotz/archive/2.44.tar.gz
curl -s http://www.infocom-if.org/downloads/zorki.hqx > zorki.hqx
tar zxf 2.44.tar.gz
uudeview -i zorki.hqx
make --directory=frotz-2.44 install_dumb

#  Set up runtime state
mkdir log
chown vagrant:vagrant frotz-2.44/dfrotz ZORKI log venv

#  Clean up directory
rm 2.44.tar.gz
rm zorki.hqx

#  Run
echo "starting Frotzlack"
sudo -u vagrant /home/vagrant/venv/bin/python /vagrant/frotzlack.py
