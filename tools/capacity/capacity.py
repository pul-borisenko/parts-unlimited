#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Расчёт ёмкости спринта для GitHub Projects (ЛР 2.1, задача 3).

В Azure Boards ёмкость задаётся на вкладке Capacity: часы в день на участника,
выходные дни участника и команды. В GitHub Projects такой вкладки нет, поэтому
ёмкость описывается в файле capacity.json рядом со скриптом, а скрипт сравнивает её
с суммой поля «Остаток» по незавершённым задачам текущей итерации.

Запуск локально:
    GITHUB_TOKEN=<PAT с областями read:project и repo> python capacity.py --config capacity.json
В GitHub Actions — workflow .github/workflows/capacity.yml (секрет PROJECT_TOKEN).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GRAPHQL_URL = os.environ.get("GITHUB_GRAPHQL_URL", os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + "/graphql")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

Q_PROJECT = """
query($owner: String!, $number: Int!, $cursor: String) {
  %s(login: $owner) {
    projectV2(number: $number) {
      title
      url
      fields(first: 50) {
        nodes {
          ... on ProjectV2IterationField {
            name
            configuration {
              iterations { id title startDate duration }
              completedIterations { id title startDate duration }
            }
          }
        }
      }
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isArchived
          content {
            __typename
            ... on Issue { number title state url assignees(first: 10) { nodes { login } } }
            ... on DraftIssue { title assignees(first: 10) { nodes { login } } }
          }
          fieldValues(first: 30) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldIterationValue { iterationId title field { ... on ProjectV2IterationField { name } } }
              ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2Field { name } } }
              ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2SingleSelectField { name } } }
            }
          }
        }
      }
    }
  }
}"""


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "capacity/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GraphQL: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    if payload.get("errors"):
        raise LookupError("; ".join(e.get("message", "") for e in payload["errors"]))
    return payload["data"]


def fetch_project(token: str, owner: str, number: int) -> dict[str, Any]:
    """Читает поля и все элементы проекта; владельцем может быть организация или пользователь."""
    last_error: Exception | None = None
    for kind in ("organization", "user"):
        items: list[dict[str, Any]] = []
        cursor = None
        try:
            while True:
                data = graphql(token, Q_PROJECT % kind, {"owner": owner, "number": number, "cursor": cursor})
                project = (data.get(kind) or {}).get("projectV2")
                if project is None:
                    raise LookupError(f"проект №{number} не найден у {owner}")
                items.extend(project["items"]["nodes"])
                page = project["items"]["pageInfo"]
                if not page["hasNextPage"]:
                    project["items"] = items
                    return project
                cursor = page["endCursor"]
        except LookupError as exc:
            last_error = exc
    raise SystemExit(f"Не удалось прочитать проект: {last_error}")


# --------------------------------------------------------------------------- расчёт

def parse_ranges(ranges: list[dict[str, str]]) -> set[dt.date]:
    days: set[dt.date] = set()
    for r in ranges or []:
        start = dt.date.fromisoformat(r["from"])
        end = dt.date.fromisoformat(r.get("to", r["from"]))
        while start <= end:
            days.add(start)
            start += dt.timedelta(days=1)
    return days


def pick_iteration(project: dict[str, Any], field_name: str, title: str | None, today: dt.date) -> dict[str, Any]:
    field = next((f for f in project["fields"]["nodes"] if f and f.get("name") == field_name), None)
    if field is None:
        raise SystemExit(f"В проекте нет поля-итерации «{field_name}»")
    cfg = field["configuration"]
    iterations = cfg["completedIterations"] + cfg["iterations"]
    for it in iterations:
        it["start"] = dt.date.fromisoformat(it["startDate"])
        it["end"] = it["start"] + dt.timedelta(days=int(it["duration"]) - 1)
    if title:
        chosen = next((it for it in iterations if it["title"] == title), None)
        if chosen is None:
            raise SystemExit(f"Итерация «{title}» не найдена. Есть: {', '.join(i['title'] for i in iterations)}")
        return chosen
    current = next((it for it in iterations if it["start"] <= today <= it["end"]), None)
    if current:
        return current
    upcoming = sorted((it for it in iterations if it["start"] > today), key=lambda it: it["start"])
    if not upcoming:
        raise SystemExit("Нет текущей или будущей итерации")
    return upcoming[0]


def working_days(start: dt.date, end: dt.date, workdays: set[int], excluded: set[dt.date]) -> list[dt.date]:
    days, d = [], start
    while d <= end:
        if d.isoweekday() in workdays and d not in excluded:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def item_values(item: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for v in item["fieldValues"]["nodes"]:
        field = (v or {}).get("field") or {}
        name = field.get("name")
        if not name:
            continue
        if v["__typename"] == "ProjectV2ItemFieldIterationValue":
            values[name] = v["iterationId"]
        elif v["__typename"] == "ProjectV2ItemFieldNumberValue":
            values[name] = v["number"]
        elif v["__typename"] == "ProjectV2ItemFieldSingleSelectValue":
            values[name] = v["name"]
    return values


def compute(config: dict[str, Any], project: dict[str, Any], iteration: dict[str, Any], today: dt.date) -> dict[str, Any]:
    f = config["fields"]
    workdays = set(config.get("workdays", [1, 2, 3, 4, 5]))
    team_off = parse_ranges(config.get("teamDaysOff", []))
    done = set(config.get("doneStatuses", ["Готово"]))
    from_day = max(today, iteration["start"])
    team_days = working_days(from_day, iteration["end"], workdays, team_off)

    members = []
    for m in config.get("members", []):
        off = parse_ranges(m.get("daysOff", []))
        days = [d for d in team_days if d not in off]
        members.append({
            "login": m["login"], "hoursPerDay": float(m.get("hoursPerDay", 0)), "activity": m.get("activity"),
            "daysOff": len(team_days) - len(days), "days": len(days),
            "capacity": float(m.get("hoursPerDay", 0)) * len(days), "assigned": 0.0, "tasks": [],
        })
    by_login = {m["login"].lower(): m for m in members}
    unassigned = {"login": "не назначено", "assigned": 0.0, "tasks": []}
    others: dict[str, dict[str, Any]] = {}
    by_activity: dict[str, dict[str, float]] = {}

    for item in project["items"]:
        if item.get("isArchived") or not item.get("content"):
            continue
        values = item_values(item)
        if values.get(f["iteration"]) != iteration["id"]:
            continue
        remaining = values.get(f["remaining"])
        if remaining is None or values.get(f["status"]) in done:
            continue
        content = item["content"]
        if content.get("state") == "CLOSED":
            continue
        logins = [a["login"] for a in (content.get("assignees") or {}).get("nodes", [])]
        task = {"title": content.get("title"), "number": content.get("number"), "remaining": float(remaining)}
        if not logins:
            bucket = unassigned
        else:
            key = logins[0].lower()
            bucket = by_login.get(key) or others.setdefault(key, {"login": logins[0], "assigned": 0.0, "tasks": [],
                                                                   "capacity": 0.0, "days": 0, "daysOff": 0,
                                                                   "hoursPerDay": 0.0, "activity": None})
        bucket["assigned"] += float(remaining)
        bucket["tasks"].append(task)
        activity = values.get(f.get("activity", ""), "без активности")
        stats = by_activity.setdefault(activity, {"capacity": 0.0, "assigned": 0.0})
        stats["assigned"] += float(remaining)

    all_members = members + list(others.values())
    for m in members:
        if m["activity"]:
            by_activity.setdefault(m["activity"], {"capacity": 0.0, "assigned": 0.0})["capacity"] += m["capacity"]
    total_capacity = sum(m["capacity"] for m in members)
    total_work = sum(m["assigned"] for m in all_members) + unassigned["assigned"]
    return {
        "iteration": iteration, "from": from_day, "teamDays": len(team_days),
        "members": all_members, "unassigned": unassigned, "byActivity": by_activity,
        "capacity": total_capacity, "work": total_work,
    }


def verdict(capacity: float, work: float) -> str:
    if work > capacity:
        return f"превышение на {work - capacity:g} ч"
    return f"запас {capacity - work:g} ч"


def render(result: dict[str, Any], project_title: str) -> str:
    it = result["iteration"]
    lines = [
        f"## Ёмкость спринта: {it['title']}",
        "",
        f"Проект «{project_title}», итерация {it['start']:%d.%m.%Y} – {it['end']:%d.%m.%Y}. "
        f"Расчёт с {result['from']:%d.%m.%Y}, рабочих дней команды: {result['teamDays']}.",
        "",
        "| Участник | Часов в день | Выходных | Ёмкость, ч | Остаток работы, ч | Итог |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for m in result["members"]:
        lines.append(f"| {m['login']} | {m['hoursPerDay']:g} | {m['daysOff']} | {m['capacity']:g} | "
                     f"{m['assigned']:g} | {verdict(m['capacity'], m['assigned'])} |")
    u = result["unassigned"]
    if u["assigned"]:
        lines.append(f"| не назначено | | | | {u['assigned']:g} | назначьте исполнителей |")
    lines.append(f"| **Команда** | | | **{result['capacity']:g}** | **{result['work']:g}** | "
                 f"**{verdict(result['capacity'], result['work'])}** |")
    if result["byActivity"]:
        lines += ["", "| Активность | Ёмкость, ч | Остаток работы, ч | Итог |", "| --- | ---: | ---: | --- |"]
        for name, s in sorted(result["byActivity"].items()):
            lines.append(f"| {name} | {s['capacity']:g} | {s['assigned']:g} | {verdict(s['capacity'], s['assigned'])} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ёмкость спринта для GitHub Projects")
    p.add_argument("--config", default=str(Path(__file__).with_name("capacity.json")))
    p.add_argument("--iteration", default="", help="название итерации; пусто — текущая")
    p.add_argument("--today", help="дата расчёта ГГГГ-ММ-ДД (по умолчанию сегодня)")
    p.add_argument("--fail-on-overload", action="store_true", help="код возврата 1, если работы больше, чем ёмкость")
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Задайте токен в переменной GITHUB_TOKEN (области read:project и repo).", file=sys.stderr)
        return 2
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    project = fetch_project(token, config["owner"], int(config["projectNumber"]))
    iteration = pick_iteration(project, config["fields"]["iteration"], args.iteration or None, today)
    result = compute(config, project, iteration, today)
    report = render(result, project["title"])
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    if args.fail_on_overload and result["work"] > result["capacity"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
