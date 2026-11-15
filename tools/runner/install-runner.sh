#!/usr/bin/env bash
# Установка self-hosted раннера GitHub Actions на Linux (ЛР 4.0, вариант для Linux).
#
# Аналог установки агента Azure Pipelines на Linux:
#   1. проверка требований;
#   2. отдельный пользователь без прав администратора, от которого работает раннер;
#   3. загрузка дистрибутива с проверкой SHA-256;
#   4. регистрация по одноразовому токену (действует 1 час) с метками;
#   5. установка службы systemd (svc.sh).
#
# Использование (от root или через sudo):
#   sudo ./install-runner.sh --url https://github.com/<организация>/parts-unlimited \
#        [--token <токен>] [--labels pul-build,dotnet] [--sha256 <хэш>] [--user github-runner] [--no-service]
set -euo pipefail

URL="" TOKEN="" LABELS="pul-build,dotnet" SHA256="" RUNNER_USER="github-runner" VERSION="" SERVICE=1
NAME="$(hostname)" DIR="/opt/actions-runner" GROUP="" REPLACE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --labels) LABELS="$2"; shift 2 ;;
    --sha256) SHA256="$2"; shift 2 ;;
    --user) RUNNER_USER="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --runnergroup) GROUP="$2"; shift 2 ;;
    --replace) REPLACE="--replace"; shift ;;
    --no-service) SERVICE=0; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
  esac
done

as_runner() {
  if command -v sudo >/dev/null 2>&1; then sudo -u "$RUNNER_USER" "$@"; else runuser -u "$RUNNER_USER" -- "$@"; fi
}

echo "[1/5] Проверка требований"
[[ -n "$URL" ]] || { echo "Укажите --url" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "Запустите через sudo: нужно создать пользователя и службу" >&2; exit 1; }
for cmd in curl tar sha256sum; do command -v "$cmd" >/dev/null || { echo "Нет команды $cmd" >&2; exit 1; }; done
case "$(uname -m)" in
  x86_64) ARCH="x64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "Архитектура $(uname -m) не поддерживается" >&2; exit 1 ;;
esac
[[ -f "$DIR/.runner" ]] && { echo "В $DIR уже настроен раннер. Удалите: cd $DIR && sudo -u $RUNNER_USER ./config.sh remove --token <токен>" >&2; exit 1; }
if [[ -z "$VERSION" ]]; then
  VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1)"
  VERSION="${VERSION:-2.338.0}"
fi
if [[ -z "$TOKEN" ]]; then
  read -r -s -p "Токен регистрации раннера (ввод скрыт): " TOKEN; echo
fi

echo "[2/5] Пользователь $RUNNER_USER и каталог $DIR"
id "$RUNNER_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$RUNNER_USER"
mkdir -p "$DIR"
chown "$RUNNER_USER:$RUNNER_USER" "$DIR"
chmod 750 "$DIR"   # остальные пользователи не видят журналы и рабочие файлы раннера

echo "[3/5] Загрузка раннера $VERSION ($ARCH)"
FILE="actions-runner-linux-${ARCH}-${VERSION}.tar.gz"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fL --retry 3 -o "$TMP/$FILE" "https://github.com/actions/runner/releases/download/v${VERSION}/${FILE}"
HASH="$(sha256sum "$TMP/$FILE" | cut -d' ' -f1)"
if [[ -n "$SHA256" ]]; then
  [[ "$HASH" == "${SHA256,,}" ]] || { echo "Контрольная сумма не совпадает: $HASH" >&2; exit 1; }
  echo "      Контрольная сумма совпадает"
else
  echo "  ! Контрольная сумма не проверена (SHA-256: $HASH). Сверьте со страницей New self-hosted runner."
fi
tar -xzf "$TMP/$FILE" -C "$DIR"
chown -R "$RUNNER_USER:$RUNNER_USER" "$DIR"
"$DIR/bin/installdependencies.sh" >/dev/null || echo "  ! Не все зависимости установлены — см. $DIR/bin/installdependencies.sh"

echo "[4/5] Регистрация раннера"
ARGS=(--unattended --url "$URL" --token "$TOKEN" --name "$NAME" --labels "$LABELS" --work _work)
[[ -n "$GROUP" ]] && ARGS+=(--runnergroup "$GROUP")
[[ -n "$REPLACE" ]] && ARGS+=("$REPLACE")
# Раннер нельзя регистрировать от root: запускаем от отдельного пользователя.
(cd "$DIR" && as_runner ./config.sh "${ARGS[@]}")
TOKEN=""

echo "[5/5] Готово"
if [[ $SERVICE -eq 1 ]]; then
  (cd "$DIR" && ./svc.sh install "$RUNNER_USER" && ./svc.sh start && ./svc.sh status | head -5)
else
  echo "Запуск вручную: cd $DIR && sudo -u $RUNNER_USER ./run.sh"
fi
echo "Метки раннера: self-hosted, Linux, ${ARCH^^}, ${LABELS}"
