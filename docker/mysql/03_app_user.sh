#!/bin/bash
# The account the app connects as: SELECT on card_analytics, nothing else.
#
# The compiler already refuses anything but a SELECT; this makes the database refuse
# too, so a bug there cannot become a write. Runs automatically on a NEW MySQL volume
# (docker-entrypoint-initdb.d, after the schema and data files). For an existing
# volume, run it once by hand:
#
#   docker exec nl2sql-mysql bash /docker-entrypoint-initdb.d/03_app_user.sh
#
# Safe to re-run: it creates the user if missing, resets its password to
# APP_DB_PASSWORD, and leaves it with SELECT only.
set -euo pipefail

: "${APP_DB_USER:?APP_DB_USER is not set}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is not set}"
DATABASE="${MYSQL_DATABASE:-card_analytics}"

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS '${APP_DB_USER}'@'%' IDENTIFIED BY '${APP_DB_PASSWORD}';
ALTER USER '${APP_DB_USER}'@'%' IDENTIFIED BY '${APP_DB_PASSWORD}';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${APP_DB_USER}'@'%';
GRANT SELECT ON \`${DATABASE}\`.* TO '${APP_DB_USER}'@'%';
FLUSH PRIVILEGES;
SQL

echo "app user '${APP_DB_USER}': SELECT on ${DATABASE} only"
