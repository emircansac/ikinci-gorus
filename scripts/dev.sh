#!/usr/bin/env bash
# Yerel live test-fix döngüsü — API anahtarı gerektirmez.
set -euo pipefail
cd "$(dirname "$0")/.."
source venv/bin/activate

echo "== 1/4 pytest =="
python -m pytest tests/ -q

echo "== 2/4 yerel healthcheck =="
SKIP_NLI_CHECK=1 python pipeline/00_healthcheck.py --local-only

echo "== 3/4 demo pipeline =="
python demo_seed_and_run.py

echo "== 4/4 API smoke (sunucu açıksa) =="
if curl -sf http://localhost:8000/healthz >/dev/null 2>&1; then
  curl -sf http://localhost:8000/healthz
  echo ""
  curl -sf http://localhost:8000/api/videos | python -c "import sys,json; d=json.load(sys.stdin); print(f'OK: {len(d)} video yüklendi')"
  curl -sf http://localhost:8000/api/channels | python -c "import sys,json; d=json.load(sys.stdin); print(f'OK: {len(d)} kanal yüklendi')"
else
  echo "Sunucu kapalı — 'make serve' ile başlatıp tarayıcıda http://localhost:8000 açın"
fi

echo ""
echo "Gerçek kanal testi için .env dosyasına API anahtarlarını yazın, sonra:"
echo "  make health-api"
echo "  make pipeline-smoke"
