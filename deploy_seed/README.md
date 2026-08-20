Yereldeki gerçek `data/monitor.db` snapshot'ı (demo/tohum değil).

Render kalıcı disk `data/` üzerine boş bindiği için build bu dosyayı
`data_seed/`'e alır; start'ta `data/monitor.db` yoksa bir kez kopyalanır.
Live `data/monitor.db` git'te tutulmaz (`.gitignore`).
