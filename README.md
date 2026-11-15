# Parts Unlimited

Учебный интернет-магазин автозапчастей для лабораторных работ по дисциплине
«Программная инженерия». Вместо Azure DevOps используется GitHub.

| Что | Где | ЛР |
| --- | --- | --- |
| Сайт ASP.NET Core MVC (.NET 10) | `src/PartsUnlimitedWebsite` | 4.1 |
| Модульные тесты xUnit | `tests/PartsUnlimited.Tests` | 4.1 |
| Конвейер сборки и тестов | `.github/workflows/ci.yml` | 4.0, 4.1 |
| Формы задач (Epic, Feature, PBI, Task, Bug) с полем PUL Ticket ID | `.github/ISSUE_TEMPLATE` | 2.2 |
| Генератор тестовых данных (аналог Azure DevOps Demo Generator) | `tools/generator`, `.github/workflows/demo-data.yml` | 2.0 |
| Расчёт ёмкости спринта | `tools/capacity`, `.github/workflows/capacity.yml` | 2.1 |
| Установка self-hosted раннера | `tools/runner` | 4.0 |

## Запуск локально

Нужен [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0).

```bash
dotnet test PartsUnlimited.slnx
dotnet run --project src/PartsUnlimitedWebsite      # адрес будет в консоли
```

## Переменные репозитория для CI

| Переменная | Значение | Зачем |
| --- | --- | --- |
| `USE_SELF_HOSTED` | `true` | Собирать push в `main` на своём раннере с меткой `pul-build` (ЛР 4.0) |
| `BUILD_CONFIGURATION` | `Release` или `Debug` | Конфигурация сборки (аналог переменной BuildConfiguration в Azure Pipelines) |

Задаются в **Settings → Secrets and variables → Actions → Variables**.
