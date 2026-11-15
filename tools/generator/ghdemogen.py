#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ghdemogen — генератор тестовых данных для GitHub.

Аналог Azure DevOps Demo Generator для ЛР 2.0 «Подготовка тестовых данных».
По шаблону (templates/*.json) создаёт в организации GitHub:

  * репозиторий, если его ещё нет;
  * типы задач (Epic, Feature, PBI, Task, Bug) и метки;
  * вехи (milestones) — релизы, которые видны маркерами на дорожной карте;
  * проект GitHub Projects с полями Status, Спринт (итерации), Область, Оценка,
    Остаток, Активность, Начало, Окончание;
  * задачи (issues) с иерархией «эпик → функция → PBI → задача» и значениями полей.

Нужен только Python 3.9+: сторонние пакеты не используются.

Примеры:
    python ghdemogen.py                         # интерактивный режим, как в ADOGenerator
    python ghdemogen.py --owner pul-borisenko --yes
    python ghdemogen.py --dry-run               # показать план без обращения к GitHub
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
GRAPHQL_URL = os.environ.get("GITHUB_GRAPHQL_URL", API_URL + "/graphql")
API_VERSION = "2022-11-28"
USER_AGENT = "ghdemogen/1.0 (+lab 2.0)"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
MARKER = "_Создано генератором тестовых данных ghdemogen (шаблон «{template}»)._"
REQUIRED_SCOPES = {"repo", "project"}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # консоль Windows без UTF-8 не уронит вывод
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------- вывод

def info(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- API

class GitHubError(Exception):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.payload = payload


class GitHub:
    """Минимальный клиент REST и GraphQL API GitHub на стандартной библиотеке."""

    def __init__(self, token: str, delay: float = 1.0, verbose: bool = False, max_attempts: int = 6):
        self.token = token
        self.delay = delay
        self.verbose = verbose
        self.max_attempts = max_attempts
        self.last_headers: dict[str, str] = {}

    # -- низкий уровень
    def request(self, method: str, url: str, body: Any = None) -> tuple[int, Any, dict[str, str]]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        for attempt in range(1, self.max_attempts + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status, raw, resp_headers = resp.status, resp.read(), dict(resp.headers)
            except urllib.error.HTTPError as exc:
                status, raw, resp_headers = exc.code, exc.read(), dict(exc.headers or {})
            except urllib.error.URLError as exc:
                if attempt == self.max_attempts:
                    raise GitHubError(0, f"нет связи с {url}: {exc.reason}") from exc
                time.sleep(2 ** attempt)
                continue
            self.last_headers = {k.lower(): v for k, v in resp_headers.items()}
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                payload = raw.decode("utf-8", "replace")
            if self.verbose:
                info(f"    {method} {url} -> {status}")
            wait = self._rate_limit_wait(status, payload)
            if wait is not None and attempt < self.max_attempts:
                warn(f"сработало ограничение частоты запросов GitHub, ждём {wait} с")
                time.sleep(wait)
                continue
            if status >= 500 and attempt < self.max_attempts:
                time.sleep(2 ** attempt)
                continue
            return status, payload, self.last_headers
        raise GitHubError(0, f"{method} {url}: попытки исчерпаны")

    def _rate_limit_wait(self, status: int, payload: Any) -> int | None:
        if status not in (403, 429):
            return None
        h = self.last_headers
        message = payload.get("message", "") if isinstance(payload, dict) else str(payload)
        limited = status == 429 or "retry-after" in h or h.get("x-ratelimit-remaining") == "0" \
            or "rate limit" in message.lower()
        if not limited:
            return None
        if "retry-after" in h:
            return max(1, int(float(h["retry-after"])))
        if h.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in h:
            return max(1, int(h["x-ratelimit-reset"]) - int(time.time()) + 1)
        return 60

    # -- удобные обёртки
    def rest_status(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, payload, _ = self.request(method, API_URL + path, body)
        return status, payload

    def rest(self, method: str, path: str, body: Any = None, expected: tuple[int, ...] = (200, 201)) -> Any:
        status, payload = self.rest_status(method, path, body)
        if status not in expected:
            message = payload.get("message", payload) if isinstance(payload, dict) else payload
            details = payload.get("errors") if isinstance(payload, dict) else None
            raise GitHubError(status, f"{method} {path}: {message}{' ' + json.dumps(details, ensure_ascii=False) if details else ''}", payload)
        return payload

    def paginate(self, path: str) -> Iterator[Any]:
        sep = "&" if "?" in path else "?"
        page = 1
        while True:
            chunk = self.rest("GET", f"{path}{sep}per_page=100&page={page}", expected=(200,))
            yield from chunk
            if len(chunk) < 100:
                return
            page += 1

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        status, payload, _ = self.request("POST", GRAPHQL_URL, {"query": query, "variables": variables or {}})
        if status != 200 or not isinstance(payload, dict):
            raise GitHubError(status, f"GraphQL: {payload}", payload)
        if payload.get("errors"):
            raise GitHubError(200, "GraphQL: " + "; ".join(e.get("message", str(e)) for e in payload["errors"]), payload)
        return payload["data"]

    def pause(self) -> None:
        """Пауза после запроса, создающего контент: GitHub ограничивает частоту таких запросов."""
        if self.delay:
            time.sleep(self.delay)


# --------------------------------------------------------------------------- GraphQL

Q_PROJECTS = """
query($login: String!, $q: String!) {
  %s(login: $login) {
    projectsV2(first: 20, query: $q) { nodes { id number title url closed } }
  }
}"""

Q_FIELDS = """
query($id: ID!) {
  node(id: $id) {
    ... on ProjectV2 {
      fields(first: 50) {
        nodes {
          __typename
          ... on ProjectV2Field { id name dataType }
          ... on ProjectV2SingleSelectField { id name dataType options { id name color description } }
          ... on ProjectV2IterationField {
            id name dataType
            configuration {
              iterations { id title startDate duration }
              completedIterations { id title startDate duration }
            }
          }
        }
      }
    }
  }
}"""

M_CREATE_PROJECT = """
mutation($input: CreateProjectV2Input!) {
  createProjectV2(input: $input) { projectV2 { id number title url } }
}"""

M_LINK_REPO = """
mutation($input: LinkProjectV2ToRepositoryInput!) {
  linkProjectV2ToRepository(input: $input) { clientMutationId }
}"""

M_UPDATE_PROJECT = """
mutation($input: UpdateProjectV2Input!) {
  updateProjectV2(input: $input) { projectV2 { id } }
}"""

M_CREATE_FIELD = """
mutation($input: CreateProjectV2FieldInput!) {
  createProjectV2Field(input: $input) {
    projectV2Field {
      ... on ProjectV2Field { id name }
      ... on ProjectV2SingleSelectField { id name }
      ... on ProjectV2IterationField { id name }
    }
  }
}"""

M_UPDATE_FIELD = """
mutation($input: UpdateProjectV2FieldInput!) {
  updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id name } } }
}"""

M_ADD_ITEM = """
mutation($input: AddProjectV2ItemByIdInput!) {
  addProjectV2ItemById(input: $input) { item { id } }
}"""

Q_ISSUE_ITEMS = """
query($id: ID!) {
  node(id: $id) {
    ... on Issue { projectItems(first: 50, includeArchived: true) { nodes { id project { id } } } }
  }
}"""


def build_field_values_mutation(count: int) -> str:
    """Одна мутация на все поля элемента: меньше запросов — меньше риск ограничений частоты."""
    defs = ["$project: ID!", "$item: ID!"]
    parts = []
    for i in range(count):
        defs += [f"$f{i}: ID!", f"$v{i}: ProjectV2FieldValue!"]
        parts.append(
            f"u{i}: updateProjectV2ItemFieldValue(input: {{projectId: $project, itemId: $item, "
            f"fieldId: $f{i}, value: $v{i}}}) {{ clientMutationId }}"
        )
    return "mutation(" + ", ".join(defs) + ") {\n  " + "\n  ".join(parts) + "\n}"


# --------------------------------------------------------------------------- шаблон

def load_template(name_or_path: str) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.suffix:
        path = TEMPLATES_DIR / f"{name_or_path}.json"
    with path.open(encoding="utf-8") as fh:
        tpl = json.load(fh)
    validate_template(tpl)
    return tpl


def list_templates() -> list[tuple[str, str]]:
    result = []
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            result.append((path.stem, json.load(fh).get("name", path.stem)))
    return result


def walk(items: list[dict[str, Any]], parent: dict[str, Any] | None = None) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    for item in items:
        yield item, parent
        yield from walk(item.get("children", []), item)


def validate_template(tpl: dict[str, Any]) -> None:
    statuses = {s["key"] for s in tpl["status"]}
    activities = {a["name"] for a in tpl["activities"]}
    areas = {a["name"] for a in tpl["areas"]}
    milestones = {m["title"] for m in tpl.get("milestones", [])}
    types = {t["name"] for t in tpl["issueTypes"]}
    labels = {lab["name"] for lab in tpl.get("labels", [])}
    count = tpl["sprints"]["count"]
    problems = []
    for item, _ in walk(tpl["items"]):
        where = f"«{item.get('title', '?')}»"
        if item.get("type") not in types:
            problems.append(f"{where}: неизвестный тип {item.get('type')}")
        if "status" in item and item["status"] not in statuses:
            problems.append(f"{where}: неизвестное состояние {item['status']}")
        if "activity" in item and item["activity"] not in activities:
            problems.append(f"{where}: неизвестная активность {item['activity']}")
        if "area" in item and item["area"] not in areas:
            problems.append(f"{where}: неизвестная область {item['area']}")
        if "milestone" in item and item["milestone"] not in milestones:
            problems.append(f"{where}: неизвестная веха {item['milestone']}")
        if "sprint" in item and not 1 <= item["sprint"] <= count:
            problems.append(f"{where}: спринт {item['sprint']} вне диапазона 1..{count}")
        for lab in item.get("labels", []):
            if lab not in labels:
                problems.append(f"{where}: метка {lab} не описана в разделе labels")
    if problems:
        raise ValueError("Ошибки в шаблоне:\n  " + "\n  ".join(problems))


def derive_status(item: dict[str, Any]) -> str:
    """Состояние эпика/функции выводится из дочерних элементов."""
    if "status" in item:
        return item["status"]
    child_states = [derive_status(c) for c in item.get("children", [])]
    if child_states and all(s == "done" for s in child_states):
        return "done"
    if any(s in ("committed", "done") for s in child_states):
        return "committed"
    if any(s == "approved" for s in child_states):
        return "approved"
    return "new"


def compute_sprints(tpl: dict[str, Any], today: dt.date) -> list[dict[str, Any]]:
    cfg = tpl["sprints"]
    length = int(cfg["lengthDays"])
    monday = today - dt.timedelta(days=today.weekday())
    first = monday - dt.timedelta(days=length * (int(cfg["current"]) - 1))
    sprints = []
    for i in range(int(cfg["count"])):
        start = first + dt.timedelta(days=length * i)
        sprints.append({
            "n": i + 1,
            "title": cfg["titlePattern"].format(n=i + 1),
            "start": start,
            "end": start + dt.timedelta(days=length - 1),
            "duration": length,
        })
    return sprints


# --------------------------------------------------------------------------- генератор

class Generator:
    def __init__(self, gh: GitHub, tpl: dict[str, Any], owner: str, repo: str, project_title: str,
                 today: dt.date | None = None):
        self.gh = gh
        self.tpl = tpl
        self.owner = owner
        self.repo_name = repo
        self.project_title = project_title
        self.today = today or dt.date.today()
        self.sprints = compute_sprints(tpl, self.today)
        self.names = tpl["fieldNames"]
        self.login = ""
        self.is_org = True
        self.owner_node_id = ""
        self.repo: dict[str, Any] = {}
        self.available_types: set[str] = set()
        self.milestones: dict[str, int] = {}
        self.project: dict[str, Any] = {}
        self.fields: dict[str, dict[str, Any]] = {}
        self.status_options: dict[str, str] = {}
        self.created: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        warn(msg)

    # -- шаг 1: кто мы и кто владелец
    def check_identity(self) -> None:
        me = self.gh.rest("GET", "/user", expected=(200,))
        self.login = me["login"]
        scopes_header = self.gh.last_headers.get("x-oauth-scopes")
        info(f"[1/8] Токен принадлежит пользователю {self.login}")
        if scopes_header is not None:  # classic PAT; у fine-grained токенов этого заголовка нет
            scopes = {s.strip() for s in scopes_header.split(",") if s.strip()}
            missing = REQUIRED_SCOPES - scopes
            if missing:
                raise GitHubError(0, f"у токена нет областей доступа: {', '.join(sorted(missing))}. "
                                     "Выпустите токен с областями repo, project и admin:org.")
            if "admin:org" not in scopes:
                self._warn("нет области admin:org — типы задач в организации создать не получится")
        status, org = self.gh.rest_status("GET", f"/orgs/{self.owner}")
        if status == 200:
            self.is_org, self.owner_node_id = True, org["node_id"]
            info(f"      Владелец: организация {org['login']}")
            return
        status, user = self.gh.rest_status("GET", f"/users/{self.owner}")
        if status != 200:
            raise GitHubError(status, f"организация или пользователь «{self.owner}» не найдены")
        if user["login"].lower() != self.login.lower():
            raise GitHubError(0, "создавать проект можно только в своей учётной записи или в организации")
        self.is_org, self.owner_node_id = False, user["node_id"]
        self._warn("владелец — личная учётная запись; рекомендуется организация (команды, типы задач)")

    # -- шаг 2: репозиторий
    def ensure_repo(self) -> None:
        status, repo = self.gh.rest_status("GET", f"/repos/{self.owner}/{self.repo_name}")
        if status == 200:
            info(f"[2/8] Репозиторий {repo['full_name']} уже существует — используем его")
            if not repo.get("has_issues", True):
                self.gh.rest("PATCH", f"/repos/{self.owner}/{self.repo_name}", {"has_issues": True})
        elif status == 404:
            body = {
                "name": self.repo_name,
                "description": self.tpl.get("repository", {}).get("description", ""),
                "private": False, "has_issues": True, "has_wiki": True, "has_projects": True,
                "auto_init": True,
            }
            path = f"/orgs/{self.owner}/repos" if self.is_org else "/user/repos"
            repo = self.gh.rest("POST", path, body, expected=(201,))
            self.gh.pause()
            info(f"[2/8] Создан репозиторий {repo['full_name']}")
        else:
            raise GitHubError(status, f"не удалось получить репозиторий: {repo}")
        self.repo = repo

    # -- шаг 3: типы задач
    def ensure_issue_types(self) -> None:
        base = f"/orgs/{self.owner}/issue-types" if self.is_org else "/user/issue-types"
        status, existing = self.gh.rest_status("GET", base)
        if status != 200 or not isinstance(existing, list):
            self._warn("типы задач недоступны — тип будет записан меткой «тип: …»")
            info("[3/8] Типы задач: пропущено")
            return
        by_name = {t["name"].lower(): t for t in existing}
        created = []
        for t in self.tpl["issueTypes"]:
            current = by_name.get(t["name"].lower())
            if current:
                if current.get("is_enabled", True):
                    self.available_types.add(current["name"])
                else:
                    self._warn(f"тип {current['name']} выключен в настройках организации — включите его")
                continue
            status, data = self.gh.rest_status("POST", base, {
                "name": t["name"], "description": t.get("description"), "color": t.get("color"), "is_enabled": True,
            })
            self.gh.pause()
            if status in (200, 201):
                self.available_types.add(data["name"])
                created.append(data["name"])
            else:
                msg = data.get("message") if isinstance(data, dict) else data
                self._warn(f"не удалось создать тип {t['name']}: HTTP {status} {msg}")
        info(f"[3/8] Типы задач: доступны {', '.join(sorted(self.available_types)) or '—'}"
             + (f"; созданы {', '.join(created)}" if created else ""))

    # -- шаг 4: метки и вехи
    def ensure_labels_and_milestones(self) -> None:
        repo_path = f"/repos/{self.owner}/{self.repo_name}"
        existing = {lab["name"].lower() for lab in self.gh.paginate(f"{repo_path}/labels")}
        wanted = list(self.tpl.get("labels", []))
        for t in self.tpl["issueTypes"]:
            if t["name"] not in self.available_types:
                wanted.append({"name": f"тип: {t['name']}", "color": "ededed", "description": t.get("description", "")})
        new_labels = 0
        for lab in wanted:
            if lab["name"].lower() in existing:
                continue
            self.gh.rest("POST", f"{repo_path}/labels", lab, expected=(201,))
            self.gh.pause()
            new_labels += 1
        current = {m["title"]: m for m in self.gh.paginate(f"{repo_path}/milestones?state=all")}
        for ms in self.tpl.get("milestones", []):
            if ms["title"] in current:
                self.milestones[ms["title"]] = current[ms["title"]]["number"]
                continue
            due = self.sprints[ms["endOfSprint"] - 1]["end"]
            data = self.gh.rest("POST", f"{repo_path}/milestones", {
                "title": ms["title"], "description": ms.get("description", ""),
                "due_on": f"{due.isoformat()}T12:00:00Z", "state": "open",
            }, expected=(201,))
            self.gh.pause()
            self.milestones[ms["title"]] = data["number"]
        info(f"[4/8] Метки: новых {new_labels}; вехи: {', '.join(self.milestones) or '—'}")

    # -- шаг 5: проект
    def ensure_project(self) -> None:
        kind = "organization" if self.is_org else "user"
        data = self.gh.graphql(Q_PROJECTS % kind, {"login": self.owner, "q": self.project_title})
        nodes = (data.get(kind) or {}).get("projectsV2", {}).get("nodes", [])
        found = [p for p in nodes if p["title"] == self.project_title and not p.get("closed")]
        if found:
            self.project = found[0]
            info(f"[5/8] Проект «{self.project_title}» уже есть (№{self.project['number']}) — дополняем его")
            try:
                self.gh.graphql(M_LINK_REPO, {"input": {"projectId": self.project["id"], "repositoryId": self.repo["node_id"]}})
            except GitHubError:
                pass  # уже связан
        else:
            data = self.gh.graphql(M_CREATE_PROJECT, {"input": {
                "ownerId": self.owner_node_id, "title": self.project_title, "repositoryId": self.repo["node_id"],
            }})
            self.gh.pause()
            self.project = data["createProjectV2"]["projectV2"]
            info(f"[5/8] Создан проект «{self.project_title}» №{self.project['number']}: {self.project['url']}")
        try:
            self.gh.graphql(M_UPDATE_PROJECT, {"input": {
                "projectId": self.project["id"],
                "shortDescription": f"Тестовые данные: шаблон «{self.tpl['name']}»",
                "readme": self.tpl.get("description", ""),
            }})
        except GitHubError as exc:
            self._warn(f"не удалось обновить описание проекта: {exc}")

    # -- шаг 6: поля
    def _load_fields(self) -> dict[str, dict[str, Any]]:
        data = self.gh.graphql(Q_FIELDS, {"id": self.project["id"]})
        return {f["name"]: f for f in data["node"]["fields"]["nodes"] if f and "name" in f}

    @staticmethod
    def _options(spec: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [{"name": o["name"], "color": o.get("color", "GRAY"), "description": o.get("description", "")} for o in spec]

    def _create_field(self, name: str, data_type: str, **extra: Any) -> None:
        self.gh.graphql(M_CREATE_FIELD, {"input": {"projectId": self.project["id"], "name": name, "dataType": data_type, **extra}})
        self.gh.pause()

    def _ensure_single_select(self, fields: dict[str, Any], name: str, spec: list[dict[str, Any]]) -> None:
        field = fields.get(name)
        if field is None:
            self._create_field(name, "SINGLE_SELECT", singleSelectOptions=self._options(spec))
            return
        have = {o["name"] for o in field.get("options", [])}
        if {o["name"] for o in spec} <= have:
            return
        merged = [{"name": o["name"], "color": o["color"], "description": o.get("description") or ""} for o in field["options"]]
        merged += [o for o in self._options(spec) if o["name"] not in have]
        self.gh.graphql(M_UPDATE_FIELD, {"input": {"fieldId": field["id"], "singleSelectOptions": merged}})
        self.gh.pause()

    def ensure_fields(self) -> None:
        n = self.names
        fields = self._load_fields()
        status_field = fields.get(n["status"])
        if status_field is None:
            raise GitHubError(0, f"в проекте нет поля {n['status']}")
        wanted = [s["name"] for s in self.tpl["status"]]
        if not set(wanted) <= {o["name"] for o in status_field["options"]}:
            try:
                # Заменяем стандартные Todo/In Progress/Done на состояния Scrum из шаблона.
                self.gh.graphql(M_UPDATE_FIELD, {"input": {
                    "fieldId": status_field["id"], "singleSelectOptions": self._options(self.tpl["status"]),
                }})
                self.gh.pause()
            except GitHubError as exc:
                self._warn(f"не удалось изменить значения Status ({exc}); будут использованы стандартные")
        self._ensure_single_select(fields, n["area"], self.tpl["areas"])
        self._ensure_single_select(fields, n["activity"], self.tpl["activities"])
        for key in ("effort", "remaining"):
            if n[key] not in fields:
                self._create_field(n[key], "NUMBER")
        for key in ("start", "end"):
            if n[key] not in fields:
                self._create_field(n[key], "DATE")
        if n["sprint"] not in fields:
            first = self.sprints[0]
            self._create_field(n["sprint"], "ITERATION", iterationConfiguration={
                "startDate": first["start"].isoformat(),
                "duration": first["duration"],
                "iterations": [
                    {"title": s["title"], "startDate": s["start"].isoformat(), "duration": s["duration"]}
                    for s in self.sprints
                ],
            })
        self.fields = self._load_fields()
        sprint_field = self.fields[n["sprint"]]
        cfg = sprint_field.get("configuration") or {}
        by_title = {it["title"]: it for it in cfg.get("iterations", []) + cfg.get("completedIterations", [])}
        for s in self.sprints:
            if s["title"] in by_title:
                s["iterationId"] = by_title[s["title"]]["id"]
            else:
                self._warn(f"в поле {n['sprint']} нет итерации {s['title']} — элементы этого спринта останутся без итерации")
        options = {o["name"]: o["id"] for o in self.fields[n["status"]]["options"]}
        fallback = {"new": "Todo", "approved": "Todo", "committed": "In Progress", "done": "Done"}
        for s in self.tpl["status"]:
            option_id = options.get(s["name"]) or options.get(fallback.get(s["key"], ""))
            if option_id:
                self.status_options[s["key"]] = option_id
        info(f"[6/8] Поля проекта готовы: {', '.join(n[k] for k in ('status', 'sprint', 'area', 'effort', 'remaining', 'activity', 'start', 'end'))}")

    def _option_id(self, field_key: str, option_name: str) -> str | None:
        field = self.fields.get(self.names[field_key]) or {}
        for opt in field.get("options", []):
            if opt["name"] == option_name:
                return opt["id"]
        return None

    def _values_for(self, item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        n, values = self.names, []
        status_id = self.status_options.get(derive_status(item))
        if status_id:
            values.append((self.fields[n["status"]]["id"], {"singleSelectOptionId": status_id}))
        area_id = self._option_id("area", item.get("area", self.tpl["areas"][0]["name"]))
        if area_id:
            values.append((self.fields[n["area"]]["id"], {"singleSelectOptionId": area_id}))
        if "sprint" in item:
            sprint = self.sprints[item["sprint"] - 1]
            if sprint.get("iterationId"):
                values.append((self.fields[n["sprint"]]["id"], {"iterationId": sprint["iterationId"]}))
            values.append((self.fields[n["start"]]["id"], {"date": sprint["start"].isoformat()}))
            values.append((self.fields[n["end"]]["id"], {"date": sprint["end"].isoformat()}))
        if "effort" in item:
            values.append((self.fields[n["effort"]]["id"], {"number": float(item["effort"])}))
        if "remaining" in item:
            values.append((self.fields[n["remaining"]]["id"], {"number": float(item["remaining"])}))
        if "activity" in item:
            activity_id = self._option_id("activity", item["activity"])
            if activity_id:
                values.append((self.fields[n["activity"]]["id"], {"singleSelectOptionId": activity_id}))
        return values

    # -- шаг 7: задачи
    def create_items(self) -> None:
        repo_path = f"/repos/{self.owner}/{self.repo_name}"
        marker = MARKER.format(template=self.tpl["name"])
        flat = list(walk(self.tpl["items"]))
        info(f"[7/8] Создание задач: {len(flat)} шт. (с паузами, чтобы не упереться в ограничения GitHub)")
        records: dict[int, dict[str, Any]] = {}
        previous = self._existing_generated(repo_path, marker)
        if previous:
            info(f"      Найдены задачи от прошлого запуска: {len(previous)} — используем их, дубликаты не создаём")
        for index, (item, parent) in enumerate(flat, start=1):
            labels = list(item.get("labels", []))
            body: dict[str, Any] = {"title": item["title"], "body": (item.get("body", "") + "\n\n" + marker).strip()}
            if item["type"] in self.available_types:
                body["type"] = item["type"]
            else:
                labels.append(f"тип: {item['type']}")
            if labels:
                body["labels"] = labels
            if item.get("milestone"):
                body["milestone"] = self.milestones[item["milestone"]]
            if item.get("assignMe"):
                body["assignees"] = [self.login]
            reused = previous.pop(item["title"], None)
            if reused is not None:
                issue = reused
            else:
                issue = self.gh.rest("POST", f"{repo_path}/issues", body, expected=(201,))
                self.gh.pause()
            record = {"title": item["title"], "type": item["type"], "number": issue["number"],
                      "url": issue["html_url"], "parent": None}
            records[id(item)] = {**record, "id": issue["id"]}
            if parent is not None:
                parent_rec = records[id(parent)]
                status, data = self.gh.rest_status("POST", f"{repo_path}/issues/{parent_rec['number']}/sub_issues",
                                                   {"sub_issue_id": issue["id"]})
                self.gh.pause()
                if status in (200, 201) or reused is not None:
                    # у задачи из прошлого запуска связь обычно уже есть — GitHub отвечает 422
                    record["parent"] = parent_rec["number"]
                else:
                    self._warn(f"#{issue['number']}: не удалось сделать подзадачей #{parent_rec['number']}: HTTP {status}")
            if derive_status(item) == "done" and issue.get("state") != "closed":
                self.gh.rest("PATCH", f"{repo_path}/issues/{issue['number']}",
                             {"state": "closed", "state_reason": "completed"}, expected=(200,))
                self.gh.pause()
            item_id = self._add_to_project(issue["node_id"])
            values = self._values_for(item)
            if values:
                variables: dict[str, Any] = {"project": self.project["id"], "item": item_id}
                for i, (field_id, value) in enumerate(values):
                    variables[f"f{i}"] = field_id
                    variables[f"v{i}"] = value
                self.gh.graphql(build_field_values_mutation(len(values)), variables)
                self.gh.pause()
            self.created.append(record)
            suffix = " (уже была)" if reused is not None else ""
            info(f"      {index:>2}/{len(flat)} #{issue['number']:<4} {item['type']:<8} {item['title']}{suffix}")

    def _existing_generated(self, repo_path: str, marker: str) -> dict[str, dict[str, Any]]:
        """Задачи, созданные прошлым (прерванным) запуском: повторный запуск их дополняет, а не дублирует."""
        found: dict[str, dict[str, Any]] = {}
        for issue in self.gh.paginate(f"{repo_path}/issues?state=all&sort=created&direction=asc"):
            if "pull_request" in issue or marker not in (issue.get("body") or ""):
                continue
            found.setdefault(issue["title"], issue)
        return found

    def _add_to_project(self, content_id: str) -> str:
        """Добавляет задачу в проект и возвращает id элемента.

        Если в проекте включено правило Auto-add, GitHub может добавить задачу сам, одновременно
        со скриптом, — тогда мутация отвечает «Content already exists in this project».
        В этом случае берём уже существующий элемент.
        """
        for attempt in range(1, 5):
            try:
                added = self.gh.graphql(M_ADD_ITEM, {"input": {"projectId": self.project["id"], "contentId": content_id}})
                self.gh.pause()
                return added["addProjectV2ItemById"]["item"]["id"]
            except GitHubError as exc:
                if "already exists" not in str(exc):
                    raise
            self.gh.pause()
            data = self.gh.graphql(Q_ISSUE_ITEMS, {"id": content_id})
            for node in ((data.get("node") or {}).get("projectItems") or {}).get("nodes", []):
                if node and node.get("project", {}).get("id") == self.project["id"]:
                    return node["id"]
            time.sleep(2 * attempt)
        raise GitHubError(0, "не удалось получить элемент проекта для задачи — повторите запуск")

    # -- шаг 8: итог
    def write_result(self, directory: Path) -> Path:
        result = {
            "template": self.tpl["name"],
            "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
            "owner": self.owner,
            "repository": self.repo.get("html_url"),
            "project": {"number": self.project.get("number"), "url": self.project.get("url"), "title": self.project_title},
            "sprints": [{"title": s["title"], "start": s["start"].isoformat(), "end": s["end"].isoformat()} for s in self.sprints],
            "issues": self.created,
            "warnings": self.warnings,
        }
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"ghdemogen-{self.owner}-{self.repo_name}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def run(self, result_dir: Path) -> Path:
        self.check_identity()
        self.ensure_repo()
        self.ensure_issue_types()
        self.ensure_labels_and_milestones()
        self.ensure_project()
        self.ensure_fields()
        self.create_items()
        path = self.write_result(result_dir)
        info(f"[8/8] Готово. Задач создано: {len(self.created)}")
        info(f"      Репозиторий: {self.repo.get('html_url')}")
        info(f"      Проект:      {self.project.get('url')}")
        info(f"      Отчёт:       {path}")
        info("      Проверьте в проекте Settings → Workflows: правило «Item closed» должно ставить «Готово».")
        if self.warnings:
            info(f"      Предупреждений: {len(self.warnings)} (см. выше и в отчёте)")
        return path


# --------------------------------------------------------------------------- режимы запуска

def print_plan(tpl: dict[str, Any], owner: str, repo: str, project: str, today: dt.date) -> None:
    sprints = compute_sprints(tpl, today)
    items = list(walk(tpl["items"]))
    by_type: dict[str, int] = {}
    for item, _ in items:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
    info(f"План для шаблона «{tpl['name']}» (без обращения к GitHub)")
    info(f"  Владелец: {owner or '<не задан>'}, репозиторий: {repo}, проект: {project}")
    info("  Спринты:")
    for s in sprints:
        mark = "  ← текущий" if s["start"] <= today <= s["end"] else ""
        info(f"    {s['title']}: {s['start']:%d.%m.%Y} – {s['end']:%d.%m.%Y}{mark}")
    info("  Задачи: " + ", ".join(f"{k} — {v}" for k, v in by_type.items()) + f" (всего {len(items)})")
    for item, parent in items:
        depth = 0
        p = parent
        while p is not None:
            depth += 1
            p = next((par for it, par in items if it is p), None)
        extra = []
        if "sprint" in item:
            extra.append(sprints[item["sprint"] - 1]["title"])
        extra.append(next(s["name"] for s in tpl["status"] if s["key"] == derive_status(item)))
        if "remaining" in item:
            extra.append(f"остаток {item['remaining']} ч")
        info(f"    {'  ' * depth}{item['type']}: {item['title']} [{', '.join(extra)}]")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default


def choose(title: str, options: list[str]) -> int:
    info(title)
    for i, opt in enumerate(options, start=1):
        info(f"  {i} | {opt}")
    while True:
        raw = input("Ваш выбор: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        info("  Введите номер из списка.")


def read_token(args: argparse.Namespace) -> str:
    if args.token_stdin:
        return sys.stdin.readline().strip()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        info("Используется токен из переменной окружения GITHUB_TOKEN/GH_TOKEN")
        return token
    return getpass.getpass("Введите Personal Access Token (ввод скрыт): ").strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Генератор тестовых данных для GitHub (аналог Azure DevOps Demo Generator)")
    p.add_argument("--owner", help="организация GitHub (или ваш логин)")
    p.add_argument("--repo", default=None, help="имя репозитория (по умолчанию parts-unlimited)")
    p.add_argument("--project", default=None, help="название проекта (по умолчанию Unlimited Parts)")
    p.add_argument("--template", default=None, help="имя шаблона из папки templates или путь к JSON")
    p.add_argument("--token-stdin", action="store_true", help="прочитать токен из стандартного ввода")
    p.add_argument("--delay", type=float, default=1.0, help="пауза между создающими запросами, с (по умолчанию 1)")
    p.add_argument("--today", help="дата отсчёта спринтов ГГГГ-ММ-ДД (по умолчанию сегодня)")
    p.add_argument("--dry-run", action="store_true", help="показать план и выйти, ничего не создавая")
    p.add_argument("--yes", "-y", action="store_true", help="не задавать вопросов, использовать параметры")
    p.add_argument("--result-dir", default=".", help="куда сохранить отчёт о созданных объектах")
    p.add_argument("--verbose", action="store_true", help="печатать каждый HTTP-запрос")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    interactive = not args.yes and not args.dry_run and sys.stdin.isatty()

    info("ghdemogen — генератор тестовых данных для GitHub (аналог Azure DevOps Demo Generator)")
    template_name = args.template
    if interactive:
        if choose("Что сделать?", ["Создать проект по шаблону", "Выход"]) == 1:
            return 0
        templates = list_templates()
        if not template_name:
            template_name = templates[choose("Шаблоны:", [title for _, title in templates])][0]
        choose("Способ авторизации:", ["Personal Access Token (PAT)"])
    tpl = load_template(template_name or "parts-unlimited")

    owner = args.owner or (ask("Имя организации GitHub") if interactive else "")
    repo = args.repo or (ask("Имя репозитория", "parts-unlimited") if interactive else "parts-unlimited")
    project = args.project or (ask("Название проекта", "Unlimited Parts") if interactive else "Unlimited Parts")

    if args.dry_run:
        print_plan(tpl, owner, repo, project, today)
        return 0
    if not owner:
        info("Укажите организацию: --owner <имя>")
        return 2
    token = read_token(args)
    if not token:
        info("Токен не задан.")
        return 2
    if interactive and ask(f"Создать данные в {owner}/{repo}, проект «{project}»? (y/n)", "y").lower() not in ("y", "д", "yes", "да"):
        return 0

    gen = Generator(GitHub(token, delay=args.delay, verbose=args.verbose), tpl, owner, repo, project, today)
    try:
        gen.run(Path(args.result_dir))
    except GitHubError as exc:
        info(f"\nОшибка: {exc}")
        if exc.status == 401:
            info("Проверьте токен: он неверен или истёк.")
        elif exc.status == 403:
            info("Недостаточно прав: нужны области repo, project, admin:org и роль владельца организации.")
        if gen.created:
            info(f"Уже создано задач: {len(gen.created)}. Повторный запуск с теми же параметрами продолжит с места остановки (созданные задачи не дублируются).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
