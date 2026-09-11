#!/bin/bash
set -euo pipefail
stage=/home/ubuntu/doxagent-stage
id doxagent-desktop >/dev/null 2>&1 || useradd -m -s /bin/bash doxagent-desktop
chpasswd -e < "$stage/desktop-shadow"
rm "$stage/desktop-shadow"
sed -i 's/^port=3389$/port=tcp:\/\/127.0.0.1:3389/' /etc/xrdp/xrdp.ini
usermod -aG ssl-cert xrdp
printf 'startxfce4\n' > /home/doxagent-desktop/.xsession
install -d -o doxagent-desktop -g doxagent-desktop -m 700 /home/doxagent-desktop/Jts /home/doxagent-desktop/.config/autostart /home/doxagent-desktop/Desktop
curl -fL --retry 2 -o "$stage/ibgateway-linux.sh" https://download.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh
chmod +x "$stage/ibgateway-linux.sh"
"$stage/ibgateway-linux.sh" -q -dir /opt/ibgateway
printf '\n-DjtsConfigDir=/home/doxagent-desktop/Jts\n' >> /opt/ibgateway/ibgateway.vmoptions
cat > /home/doxagent-desktop/Desktop/ibgateway.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=IB Gateway
Exec=/opt/ibgateway/ibgateway
Terminal=false
DESKTOP
cp /home/doxagent-desktop/Desktop/ibgateway.desktop /home/doxagent-desktop/.config/autostart/
chmod +x /home/doxagent-desktop/Desktop/ibgateway.desktop
chown -R doxagent-desktop:doxagent-desktop /home/doxagent-desktop
install -d -m 755 /etc/doxagent
bridge=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
printf 'DOCKER_HOST_GATEWAY=%s\n' "$bridge" > /etc/doxagent/broker.env
cat > /etc/systemd/system/doxagent-ibkr-paper-relay.service <<'UNIT'
[Unit]
Description=Private Docker bridge to loopback IB Gateway Paper API
After=network.target docker.service
[Service]
EnvironmentFile=/etc/doxagent/broker.env
ExecStart=/usr/bin/socat TCP4-LISTEN:7496,bind=${DOCKER_HOST_GATEWAY},reuseaddr,fork,range=172.16.0.0/12 TCP4:127.0.0.1:4002
Restart=always
RestartSec=3
NoNewPrivileges=true
User=nobody
[Install]
WantedBy=multi-user.target
UNIT
install -m 755 /home/ubuntu/doxagent/deploy/doxagent-broker-firewall /usr/local/sbin/
install -m 644 /home/ubuntu/doxagent/deploy/doxagent-broker-firewall.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now doxagent-broker-firewall doxagent-ibkr-paper-relay
systemctl restart xrdp
