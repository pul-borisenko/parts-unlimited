# Self-hosted раннер GitHub Actions (ЛР 4.0)

Раннер — аналог локального агента Azure Pipelines: программа на вашем компьютере, которая
забирает задания конвейера с GitHub по исходящему HTTPS (порт 443) и выполняет их.
Входящие порты открывать не нужно. Для self-hosted раннеров плата не взимается.

## Windows

1. Репозиторий → **Settings → Actions → Runners → New self-hosted runner → Windows, x64**.
   Скопируйте со страницы токен (`--token …`, действует 1 час) и SHA-256 архива.
2. Откройте PowerShell **от имени администратора** (не PowerShell ISE и не git-bash):

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-runner.ps1 -Url https://github.com/<организация>/parts-unlimited -AsService -Sha256 <хэш>
```

Скрипт создаст `C:\actions-runner`, оставит права на каталог только администраторам, SYSTEM
и учётной записи службы NETWORK SERVICE, зарегистрирует раннер с метками `pul-build,dotnet`
и установит службу `actions.runner.<организация>-parts-unlimited.<имя ПК>`.

Без `-AsService` раннер запускается вручную: `cd C:\actions-runner; .\run.cmd`.

## Linux

```bash
sudo ./install-runner.sh --url https://github.com/<организация>/parts-unlimited --sha256 <хэш>
```

Раннер работает от отдельного системного пользователя `github-runner` как служба systemd.

## Что ещё нужно на машине раннера

* Git — для `actions/checkout` (без него код скачивается архивом через API).
* .NET 10 SDK ставить не обязательно: шаг `actions/setup-dotnet` скачает его в кэш раннера.

## Метки вместо возможностей агента

| Azure Pipelines | GitHub Actions |
| --- | --- |
| Системные возможности (ОС, архитектура) | Метки по умолчанию: `self-hosted`, `Windows`/`Linux`, `X64`/`ARM64` |
| Пользовательская возможность | Своя метка: при регистрации (`--labels`) или **Settings → Actions → Runners → раннер → Labels** |
| Переменные окружения машины как возможности | Файл `.env` в каталоге раннера (`ИМЯ=значение`) — переменные попадают в задания |
| Требования (demands) конвейера | `runs-on: [self-hosted, pul-build]` в workflow |
| Пул агентов | Группа раннеров (runner group) на уровне организации |

## Удаление и замена

```powershell
cd C:\actions-runner
Stop-Service "actions.runner.*"
.\config.cmd remove --token <токен удаления из Settings → Actions → Runners → раннер → Remove>
```

При повторной регистрации с тем же именем добавьте `-Replace` (`--replace`), иначе регистрация
будет отклонена.
