# Cara Menjalankan BrightBean Studio (Lokal, tanpa Docker)

Aplikasi ini Django + SQLite. Sudah di-setup lengkap. Untuk membuka lagi kapan pun:

## Jalankan server
Buka terminal (git-bash) di folder ini, lalu:

    cd "D:/brightbean-studio-main/brightbean-studio-main"
    DJANGO_SETTINGS_MODULE=config.settings.development .venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000

Lalu buka di browser:  http://127.0.0.1:8000

## Login
    Email    : admin@localhost
    Password : admin12345

Admin Django (opsional): http://127.0.0.1:8000/admin/

## Kalau tampilan berantakan (CSS hilang), build ulang Tailwind:
    cd "D:/brightbean-studio-main/brightbean-studio-main/theme/static_src"
    npm run build

## Catatan
- Database = SQLite (file db.sqlite3 di folder proyek). Tidak perlu Docker/PostgreSQL.
- Untuk connect & posting ke Facebook/Instagram/YouTube dll, isi kredensial OAuth
  tiap platform di file .env (sekarang masih kosong). Untuk menjelajah UI tidak perlu.
- Untuk menghentikan server: tekan Ctrl+C di terminal-nya.
