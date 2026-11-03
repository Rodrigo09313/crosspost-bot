#!/usr/bin/env bash
# Бэкап: SQLite бота + (если запущен профиль evolution) дамп Postgres и папки инстансов Evolution.
# Запуск из любого места: ./scripts/backup.sh ; ежедневно — см. README (systemd-таймер или cron).
# Переменные: BACKUP_DIR (по умолчанию ./backups), KEEP (сколько последних копий хранить, 14).
#
# Ротация по количеству, а не по возрасту: старые копии удаляются, только когда появилась
# новая. Если бэкапы перестанут создаваться, последние KEEP копий останутся, а не исчезнут.
# Каждый файл пишется во временный *.part; при ошибке любой части конвейера set -e + pipefail
# обрывают скрипт ДО переименования, и битый файл не выдаёт себя за целую копию.
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP="${KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
umask 077  # в бэкапе сессия WhatsApp — только владельцу

echo "[$(date '+%F %T')] Бэкап в $BACKUP_DIR"

# commit <имя> — переименовать готовый <имя>.part в <имя> (вызывается только после успеха).
commit() {
    mv "$BACKUP_DIR/$1.part" "$BACKUP_DIR/$1"
    echo "  $1 ($(du -h "$BACKUP_DIR/$1" | cut -f1))"
}

# 1. SQLite бота: онлайн-бэкап через sqlite3.backup (консистентен при WAL и работающем боте).
#    Через «run --rm», чтобы работало и при остановленном/перезапускающемся боте.
docker compose run --rm --no-deps -T bot python -c '
import sqlite3, sys
src = sqlite3.connect("/data/bot.sqlite3")
dst = sqlite3.connect("/tmp/backup.sqlite3")
src.backup(dst)
dst.close(); src.close()
sys.stdout.buffer.write(open("/tmp/backup.sqlite3", "rb").read())
' | gzip > "$BACKUP_DIR/bot-$STAMP.sqlite3.gz.part"
commit "bot-$STAMP.sqlite3.gz"

# 2. Postgres Evolution API (сессия WhatsApp хранится здесь) — если контейнер запущен.
if [ -n "$(docker compose --profile evolution ps -q --status running postgres 2>/dev/null)" ]; then
    docker compose --profile evolution exec -T postgres \
        sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
        | gzip > "$BACKUP_DIR/evolution-pg-$STAMP.sql.gz.part"
    commit "evolution-pg-$STAMP.sql.gz"
fi

# 3. Папка инстансов Evolution — если контейнер запущен.
if [ -n "$(docker compose --profile evolution ps -q --status running evolution-api 2>/dev/null)" ]; then
    docker compose --profile evolution exec -T evolution-api \
        tar czf - -C /evolution instances > "$BACKUP_DIR/evolution-instances-$STAMP.tar.gz.part"
    commit "evolution-instances-$STAMP.tar.gz"
fi

# 4. Ротация: по каждому виду оставляем KEEP последних копий; недописанные *.part — удаляем.
find "$BACKUP_DIR" -maxdepth 1 -name '*.part' -delete
for kind in bot evolution-pg evolution-instances; do
    # shellcheck disable=SC2012  # имена наши, без пробелов; сортировка по времени
    ls -1t "$BACKUP_DIR/$kind-"*.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
done
echo "[$(date '+%F %T')] Готово, копий каждого вида: до $KEEP"
