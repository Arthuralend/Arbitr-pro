#!/bin/bash
# Проверка собранного приложения на Mac: сервер, API, интерфейс в окне, скриншот.
set -u
mkdir -p diag
APP="dist/Arbitr-pro.app/Contents/MacOS/Arbitr-pro"
export ARBITR_PORT=8765 ARBITR_DATA="$PWD/diag/data" ARBITR_SELFTEST=1
"$APP" &
PID=$!
for i in $(seq 40); do curl -s -o /dev/null localhost:8765/api/cases && break; sleep 1; done
sleep 15  # самопроверка интерфейса
screencapture -x diag/app.png || echo "screencapture failed" >> diag/selftest.txt
{
  echo "== index: $(curl -s -o /dev/null -w '%{http_code}' localhost:8765/)  app.js: $(curl -s -o /dev/null -w '%{http_code}' localhost:8765/app.js)"
  echo "== cases:"; curl -s localhost:8765/api/cases | head -c 800; echo
  echo "== search:"; curl -s -X POST localhost:8765/api/search -H 'Content-Type: application/json' \
      -d '{"participant":"Ромашка","case_type":"B"}' | head -c 400; echo
  echo "== log.txt:"; cat diag/data/log.txt 2>/dev/null
} >> diag/selftest.txt
kill $PID 2>/dev/null
cat diag/selftest.txt
grep -q "SELFTEST done" diag/selftest.txt
