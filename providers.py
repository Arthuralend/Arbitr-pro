"""Поставщики данных kad.arbitr.ru и приведение их ответов к единому формату.

Единый формат дела (dict):
{
  "case_id", "number", "url", "type_code" ("bankruptcy"|"civil"|"admin"|""), "type_title",
  "state", "finished", "start_date" (YYYY-MM-DD),
  "court", "judges": [..],
  "participants": {"plaintiffs": [...], "respondents": [...], "thirds": [...], "others": [...]},
      участник: {"name", "inn", "address"}
  "instances": [{"number", "level", "name", "court", "next_event",
                 "events": [{"key", "date", "publish_date", "type", "content", "info",
                             "file", "declarer", "declarer_inn", "claim_sum"}]}],
  "hearings": [{"start", "end", "location", "summary"}],
}
"""

import hashlib
import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

TIMEOUT = 180  # сервисы просят ждать ответа минимум 120 секунд


class ProviderError(Exception):
    pass


class NotFound(ProviderError):
    pass


# ---------------------------------------------------------------- утилиты


def iso_date(value):
    """'03.05.2024' / '2024-05-03' / '03.05.2024, 10:19:23' -> '2024-05-03' (или '')."""
    if not value:
        return ""
    value = str(value).strip()
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", value)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return m.group(0)
    return ""


def iso_datetime(value):
    """'05.09.2025, 10:19:23' / '2016-11-21 10:25:00' -> '2025-09-05T10:19:23'."""
    if not value:
        return ""
    value = str(value).strip()
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4}),?\s*(\d{2}:\d{2}(:\d{2})?)?", value)
    if m:
        d = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return f"{d}T{m.group(4)}" if m.group(4) else d
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T]?(\d{2}:\d{2}(:\d{2})?)?", value)
    if m:
        return f"{m.group(1)}T{m.group(2)}" if m.group(2) else m.group(1)
    return ""


def clean(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.lower() in ("null", "none") else value


def type_from_letter(letter):
    letter = (letter or "").strip().upper()
    return {"Б": "bankruptcy", "B": "bankruptcy", "Г": "civil", "G": "civil",
            "А": "admin", "A": "admin"}.get(letter, "")


TYPE_TITLES = {"bankruptcy": "Банкротное дело", "civil": "Гражданское дело",
               "admin": "Административное дело"}


def event_key(ev):
    raw = "|".join(str(ev.get(k, "")) for k in ("date", "type", "content", "file", "info", "declarer"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "kad-favorites/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            raise ProviderError(f"HTTP {e.code}: {body[:300]}")
    except urllib.error.URLError as e:
        raise ProviderError(f"Нет связи с сервисом: {e.reason}")
    except TimeoutError:
        raise ProviderError("Сервис не ответил вовремя, попробуйте позже")
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderError(f"Сервис вернул не JSON: {body[:300]}")


def _participant(p):
    return {
        "name": clean(p.get("Name") or p.get("name")),
        "inn": clean(p.get("INN") or p.get("Inn") or p.get("inn")),
        "address": clean(p.get("Address") or p.get("adress") or p.get("address")),
    }


def _finish_event(ev):
    ev["key"] = ev.get("key") or event_key(ev)
    return ev


# ---------------------------------------------------------------- api-cloud.ru


class ApiCloud:
    """https://api-cloud.ru/api/kad_arbitr.php — type=caseInfo / search / fileDownload."""

    name = "api-cloud"
    URL = "https://api-cloud.ru/api/kad_arbitr.php"

    def __init__(self, token):
        if not token:
            raise ProviderError("Не указан токен api-cloud.ru (Настройки)")
        self.token = token

    def _call(self, **params):
        params["token"] = self.token
        data = http_get_json(self.URL + "?" + urllib.parse.urlencode(params))
        if "error" in data and str(data.get("status")) != "200":
            raise ProviderError(f"api-cloud: {data.get('message') or data.get('errormsg') or data.get('error')}")
        return data

    def case_info(self, number=None, case_id=None):
        data = self._call(type="caseInfo", **({"CaseId": case_id} if case_id else {"CaseNumber": number}))
        if not data.get("found") or not data.get("Result"):
            raise NotFound("Дело не найдено")
        return self.normalize(data["Result"])

    def search(self, participant, case_type="B", page=1):
        data = self._call(type="search", participant=participant, CaseType=case_type, page=page)
        return [{
            "case_id": r.get("caseId", ""),
            "number": r.get("caseNumber", ""),
            "date": iso_date(r.get("date")),
            "court": r.get("instance", ""),
            "type_title": r.get("typeTitle", ""),
            "plaintiffs": [_participant(p) for p in r.get("plaintiff") or []],
            "respondents": [_participant(p) for p in r.get("respondent") or []],
        } for r in data.get("Result") or []]

    @staticmethod
    def normalize(result):
        info = result.get("CaseInfo") or {}
        parts = result.get("Participants") or {}
        case_id = info.get("CaseId", "")
        type_code = clean(info.get("typeCode"))
        type_code = {"administrative": "admin"}.get(type_code, type_code)
        out = {
            "case_id": case_id,
            "number": clean(info.get("CaseNumber")),
            "url": f"https://kad.arbitr.ru/Card/{case_id}" if case_id else "",
            "type_code": type_code,
            "type_title": clean(info.get("typeTitle")) or TYPE_TITLES.get(type_code, ""),
            "state": clean(info.get("State")),
            "finished": str(info.get("finish")).lower() == "true",
            "start_date": iso_date(info.get("StartDate")),
            "participants": {
                "plaintiffs": [_participant(p) for p in parts.get("Plaintiffs") or []],
                "respondents": [_participant(p) for p in parts.get("Respondents") or []],
                "thirds": [_participant(p) for p in parts.get("Thirds") or []],
                "others": [_participant(p) for p in parts.get("Others") or []],
            },
            "instances": [],
            "hearings": [],
        }
        judges = []
        for inst in result.get("CaseInstances") or []:
            court = (inst.get("Court") or {}).get("Name", "")
            for j in inst.get("Judges") or []:
                name = j.get("Name") if isinstance(j, dict) else j
                if name and name not in judges:
                    judges.append(name)
            events = []
            for ev in inst.get("InstanceEvents") or []:
                events.append(_finish_event({
                    "date": iso_date(ev.get("Date")),
                    "publish_date": iso_datetime(ev.get("PublishDate")),
                    "type": clean(ev.get("EventTypeName")),
                    "content": "; ".join(ev.get("ContentTypes") or []),
                    "info": clean(ev.get("AdditionalInfo")).lstrip(". "),
                    "file": clean(ev.get("File")),
                    "declarer": clean(ev.get("Declarers")),
                    "declarer_inn": clean(ev.get("DeclarerInn")),
                    "claim_sum": ev.get("ClaimSum") or 0,
                }))
            out["instances"].append({
                "number": clean(inst.get("InstanceNumber")),
                "level": inst.get("InstanceLevel"),
                "name": "",
                "court": court,
                "next_event": clean(inst.get("NextInstanceEvent")),
                "events": events,
            })
            # календарь заседаний в примере документации лежит внутри инстанции
            for h in inst.get("CourtHearings") or []:
                out["hearings"].append(_hearing(h))
        for h in result.get("CourtHearings") or []:
            out["hearings"].append(_hearing(h))
        out["judges"] = judges
        out["court"] = out["instances"][0]["court"] if out["instances"] else ""
        return out


def _hearing(h):
    return {"start": clean(h.get("Start")), "end": clean(h.get("End")),
            "location": clean(h.get("Location")), "summary": clean(h.get("Summary"))}


# ---------------------------------------------------------------- api-assist.com


class ApiAssist:
    """https://service.api-assist.com/parser/arbitr_api/ — details_by_number / details_by_id / search."""

    name = "api-assist"
    URL = "https://service.api-assist.com/parser/arbitr_api/"

    def __init__(self, token):
        if not token:
            raise ProviderError("Не указан ключ api-assist.com (Настройки)")
        self.token = token

    def _call(self, method, **params):
        params["key"] = self.token
        data = http_get_json(self.URL + method + "?" + urllib.parse.urlencode(params))
        if data.get("error"):
            raise ProviderError(f"api-assist: {data['error']}")
        if str(data.get("Success")) != "1":
            raise ProviderError("api-assist: источник не ответил, повторите запрос позже")
        return data

    def case_info(self, number=None, case_id=None):
        if case_id:
            data = self._call("details_by_id", CaseId=case_id)
        else:
            data = self._call("details_by_number", CaseNumber=number)
        cases = data.get("Cases") or []
        if not cases:
            raise NotFound("Дело не найдено")
        return self.normalize(cases[0])

    def search(self, participant, case_type="B", page=1):
        data = self._call("search", Inn=participant, CaseType=case_type, page=page)
        return [{
            "case_id": c.get("CaseId", ""),
            "number": c.get("CaseNumber", ""),
            "date": iso_date(c.get("StartDate")),
            "court": c.get("Court", ""),
            "type_title": TYPE_TITLES.get(type_from_letter(c.get("CaseType")), ""),
            "plaintiffs": [_participant(p) for p in c.get("Plaintiffs") or []],
            "respondents": [_participant(p) for p in c.get("Respondents") or []],
        } for c in data.get("Cases") or []]

    @staticmethod
    def normalize(c):
        case_id = c.get("CaseId", "")
        type_code = type_from_letter(c.get("CaseType"))
        out = {
            "case_id": case_id,
            "number": clean(c.get("CaseNumber")),
            "url": f"https://kad.arbitr.ru/Card/{case_id}" if case_id else "",
            "type_code": type_code,
            "type_title": TYPE_TITLES.get(type_code, ""),
            "state": clean(c.get("State")),
            "finished": bool(c.get("Finished")),
            "start_date": iso_date(c.get("StartDate")),
            "participants": {
                "plaintiffs": [_participant(p) for p in c.get("Plaintiffs") or []],
                "respondents": [_participant(p) for p in c.get("Respondents") or []],
                "thirds": [_participant(p) for p in c.get("Thirds") or []],
                "others": [_participant(p) for p in c.get("Others") or []],
            },
            "instances": [],
            "hearings": [_hearing(h) for h in c.get("CourtHearings") or []],
        }
        judges = []
        for inst in c.get("CaseInstances") or []:
            for j in inst.get("Judges") or []:
                if j and j not in judges:
                    judges.append(j)
            events = []
            for ev in inst.get("InstanceEvents") or []:
                e = {
                    "date": iso_date(ev.get("Date")),
                    "publish_date": iso_datetime(ev.get("PublishDate")),
                    "type": clean(ev.get("EventTypeName")),
                    "content": clean(ev.get("EventContentTypeName")),
                    "info": clean(ev.get("AdditionalInfo")).lstrip(". "),
                    "file": clean(ev.get("File")),
                    "declarer": clean(ev.get("Declarer")),
                    "declarer_inn": clean(ev.get("DeclarerInn")),
                    "claim_sum": ev.get("ClaimSum") or 0,
                }
                if ev.get("Id"):
                    e["key"] = ev["Id"]
                events.append(_finish_event(e))
            out["instances"].append({
                "number": clean(inst.get("InstanceNumber")),
                "level": None,
                "name": clean(inst.get("Name")),
                "court": clean((inst.get("Court") or {}).get("Name")),
                "next_event": clean(inst.get("State")),
                "events": events,
            })
        out["judges"] = judges
        out["court"] = out["instances"][0]["court"] if out["instances"] else ""
        return out


# ---------------------------------------------------------------- демо-режим


class Demo:
    """Тестовые данные без обращения к интернету — чтобы попробовать программу без ключа."""

    name = "demo"
    COMPANIES = ["ООО «Ромашка»", "АО «СтройИнвест»", "ООО «ТехноТорг»", "Иванов Иван Иванович",
                 "ООО «Северный ветер»", "ПАО «Металлург»", "Петрова Анна Сергеевна"]
    CREDITORS = ["ПАО Сбербанк", "ФНС России", "АО «Альфа-Банк»", "ООО «Поставщик»"]

    def __init__(self, token=None):
        pass

    def case_info(self, number=None, case_id=None):
        seed = number or case_id
        rnd = random.Random(seed)
        number = number or f"А40-{rnd.randint(1000, 299999)}/{rnd.randint(2019, 2026)}"
        case_id = case_id or "demo-" + hashlib.md5(number.encode()).hexdigest()[:24]
        debtor = rnd.choice(self.COMPANIES)
        creditor = rnd.choice(self.CREDITORS)
        start = datetime(2026, 1, 1) - timedelta(days=rnd.randint(30, 900))
        steps = [
            ("Заявление (исковое заявление)", "", "Сумма исковых требований 1567424,91"),
            ("Определение", "Принять к производству заявление о признании должника банкротом. Назначить судебное заседание по рассмотрению обоснованности заявления о признании должника банкротом (ст.42 ФЗ О несостоятельности)", ""),
            ("Определение", "Признать заявление обоснованным и ввести процедуру наблюдения", ""),
            ("Решение", "Признать должника банкротом и открыть конкурсное производство", ""),
            ("Ходатайство (заявление)", "", "О включении в реестр требований кредиторов"),
            ("Определение", "Об отложении рассмотрения заявления/жалобы", ""),
        ]
        # «новые» события: число зависит от текущей минуты, чтобы обновление что-то находило
        count = rnd.randint(2, 4) + datetime.now().minute % 3
        events = []
        for i, (typ, content, info) in enumerate(steps[:count]):
            d = (start + timedelta(days=20 * i)).strftime("%Y-%m-%d")
            events.append(_finish_event({
                "date": d, "publish_date": d, "type": typ, "content": content, "info": info,
                "file": "", "declarer": creditor if i == 0 else "", "declarer_inn": "",
                "claim_sum": 0,
            }))
        events.reverse()
        hearing = (datetime.now() + timedelta(days=rnd.randint(3, 40))).replace(
            hour=rnd.choice([10, 11, 14]), minute=rnd.choice([0, 15, 30]), second=0, microsecond=0)
        return {
            "case_id": case_id, "number": number, "url": f"https://kad.arbitr.ru/Card/{case_id}",
            "type_code": "bankruptcy", "type_title": "Банкротное дело",
            "state": "Рассматривается в первой инстанции", "finished": False,
            "start_date": start.strftime("%Y-%m-%d"), "court": "АС города Москвы",
            "judges": ["Королева И. Ю."],
            "participants": {
                "plaintiffs": [{"name": creditor, "inn": "7707083893", "address": ""}],
                "respondents": [{"name": debtor, "inn": str(rnd.randint(10**9, 10**10 - 1)), "address": "г. Москва"}],
                "thirds": [], "others": [],
            },
            "instances": [{"number": number, "level": 1, "name": "Первая инстанция",
                           "court": "АС города Москвы",
                           "next_event": f"Следующее заседание: {hearing:%d.%m.%Y, %H:%M} , 1410",
                           "events": events}],
            "hearings": [{"start": hearing.strftime("%Y-%m-%dT%H:%M:00+03"),
                          "end": (hearing + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00+03"),
                          "location": "115225, г. Москва, ул. Большая Тульская, д. 17, зал 1410",
                          "summary": f"Заседание по делу {number} в АС города Москвы"}],
        }

    def search(self, participant, case_type="B", page=1):
        rnd = random.Random(participant)
        res = []
        for _ in range(3):
            n = f"А40-{rnd.randint(1000, 299999)}/{rnd.randint(2019, 2026)}"
            res.append({"case_id": "", "number": n, "date": "2025-03-01", "court": "АС города Москвы",
                        "type_title": "Банкротное дело",
                        "plaintiffs": [{"name": rnd.choice(self.CREDITORS), "inn": "", "address": ""}],
                        "respondents": [{"name": participant, "inn": "", "address": ""}]})
        return res


PROVIDERS = {"api-cloud": ApiCloud, "api-assist": ApiAssist, "demo": Demo}


def get_provider(settings):
    name = settings.get("provider") or "demo"
    cls = PROVIDERS.get(name)
    if not cls:
        raise ProviderError(f"Неизвестный поставщик: {name}")
    return cls(settings.get(f"token_{name}", ""))
