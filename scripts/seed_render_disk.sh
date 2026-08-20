#!/bin/sh
# Boş Render diski data/ üzerine biner. Diskte yoksa git snapshot'ını bir kez
# kopyala (demo tohum değil; yerel analiz). Var olan dosyaya dokunma.
set -e
mkdir -p data
for f in channels.csv videos.csv claim_index.csv claim_archive.csv suspects.csv narrative_clusters.csv watchlist.json monitor.db; do
  if [ ! -f "data/$f" ] && [ -f "data_seed/$f" ]; then
    cp -f "data_seed/$f" "data/$f"
    echo "restored data/$f from snapshot"
  fi
done
exec gunicorn --workers 1 app:app
