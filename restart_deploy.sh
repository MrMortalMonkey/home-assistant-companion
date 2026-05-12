#!/bin/bash
pkill -f deploy_server.py
sleep 2
cd /home/lolufe/assistant
nohup python3 deploy_server.py > /dev/null 2>&1 &
echo "restarted"
