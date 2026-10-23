"""Хранилище избранных дел (SQLite), автоназвания, стадии, резервные копии, экспорт."""

import csv
import io
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import providers

BACKUP_FORMAT = "kad-favorites"
BACKUP_VERSION = 1
KEEP_AUTO_BACKUPS = 30

STAGES = ["Наблюдение", "Финансовое оздоровление", "Внешнее управление", "Конкурсное производство",
          "Реструктуризация долгов", "Реализация имущества", "Мировое соглашение",
          "Завершено", "Прекращено"]


def now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- номера дел

_CASE_RE = re.compile(r"^А\d{1,3}-\d{1,7}/(\d{4}|\d{2})(-\d+)*$")
_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def normalize_number(raw):
    """' a40-12345/2024 ' -> 'А40-12345/2024'. Латинская A заменяется на кириллическую."""
    s = re.sub(r"\s+", "", raw or "").upper()
    s = s.replace("A", "А").replace("–", "-").replace("—", "-")
    return s


def parse_entries(text):
    """Разбирает список номеров/ссылок kad.arbitr.ru.

    Возвращает (entries, errors): entries — [{"number"} | {"case_id"}], errors — нераспознанные строки.
    """
    entries, errors, seen = [], [], set()
    for chunk in re.split(r"[\n,;]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        guid = _GUID_RE.search(chunk) if "arbitr.ru" in chunk.lower() else None
        if guid:
            key = ("case_id", guid.group(0).lower())
        else:
            num = normalize_number(chunk)
            if not _CASE_RE.match(num):
                errors.append(chunk)
                continue
            key = ("number", num)
        if key not in seen:
            seen.add(key)
            entries.append({key[0]: key[1]})
    return entries, errors


# ---------------------------------------------------------------- названия и стадии


def nice_quotes(name):
    """ООО "Ромашка" -> ООО «Ромашка»."""
    name = re.sub(r'"([^"]*)"', r"«\1»", name or "")
    return re.sub(r"\s+", " ", name).strip()


def debtor_of(data):
    parts = (data or {}).get("participants") or {}
    for group in ("respondents", "plaintiffs"):
        for p in parts.get(group) or []:
            if p.get("name"):
                return p
    return None


def auto_title(data):
    if not data:
        return ""
    parts = data.get("participants") or {}
    if data.get("type_code") == "bankruptcy":
        d = debtor_of(data)
        return f"Банкротство: {nice_quotes(d['name'])}" if d else "Банкротное дело"
    pl = [nice_quotes(p["name"]) for p in parts.get("plaintiffs") or [] if p.get("name")]
    rs = [nice_quotes(p["name"]) for p in parts.get("respondents") or [] if p.get("name")]

    def short(names):
        return names[0] + (f" и ещё {len(names) - 1}" if len(names) > 1 else "") if names else ""

    if pl and rs:
        return f"{short(pl)} — {short(rs)}"
    return short(pl) or short(rs) or data.get("type_title", "")


_STAGE_RULES = [
    ("Завершено", [r"завершить (конкурсное|процедур|реализаци)", r"процедура .* завершена"]),
    ("Прекращено", [r"прекратить производство"]),
    ("Мировое соглашение", [r"утвердить мировое соглашение"]),
    ("Реализация имущества", [r"(ввести|введена).{0,60}реализаци[яи] имущества",
                              r"реализаци[юя] имущества.{0,30}(ввести|введен)"]),
    ("Реструктуризация долгов", [r"(ввести|введена).{0,60}реструктуризаци[июя] долгов"]),
    ("Конкурсное производство", [r"открыть конкурсное производство", r"(ввести|открыто).{0,40}конкурсное производство"]),
    ("Внешнее управление", [r"ввести внешнее управление"]),
    ("Финансовое оздоровление", [r"ввести финансовое оздоровление"]),
    ("Наблюдение", [r"ввести (процедуру )?наблюдени[ея]"]),
]


def all_events(data):
    evs = []
    for inst in (data or {}).get("instances") or []:
        for e in inst.get("events") or []:
            evs.append({**e, "instance": inst.get("number") or "", "court": inst.get("court") or ""})
    evs.sort(key=lambda e: (e.get("date") or "", e.get("publish_date") or ""), reverse=True)
    return evs


def detect_stage(data):
    """Стадия банкротства по последнему подходящему судебному акту (эвристика по тексту)."""
    if not data or data.get("type_code") != "bankruptcy":
        return ""
    for e in all_events(data):  # от новых к старым
        text = f"{e.get('content', '')} {e.get('info', '')}".lower()
        for stage, patterns in _STAGE_RULES:
            if any(re.search(p, text) for p in patterns):
                return stage
    return ""


_NEXT_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4}),?\s*(\d{2}):(\d{2})")


def hearings_of(data):
    """Заседания: из календаря, а если его нет — из строки «Следующее заседание: ...»."""
    out, seen = [], set()
    for h in (data or {}).get("hearings") or []:
        start = (h.get("start") or "")[:16]
        if start and start not in seen:
            seen.add(start)
            out.append({**h, "start": start, "end": (h.get("end") or "")[:16]})
    for inst in (data or {}).get("instances") or []:
        m = _NEXT_RE.search(inst.get("next_event") or "")
        if m:
            start = f"{m.group(3)}-{m.group(2)}-{m.group(1)}T{m.group(4)}:{m.group(5)}"
            if start not in seen:
                seen.add(start)
                room = (inst.get("next_event") or "").split(",")[-1].strip()
                out.append({"start": start, "end": "", "location": f"{inst.get('court', '')}, зал {room}".strip(", "),
                            "summary": f"Заседание по делу {inst.get('number') or data.get('number')}"})
    out.sort(key=lambda h: h["start"])
    return out


# ---------------------------------------------------------------- хранилище


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT UNIQUE,
    case_id TEXT,
    custom_title TEXT NOT NULL DEFAULT '',
    auto_title TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    stage_manual TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    new_events TEXT NOT NULL DEFAULT '[]',
    data TEXT
);
CREATE TABLE IF NOT EXISTS feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_ref INTEGER NOT NULL,
    ts TEXT NOT NULL,
    event TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, data_dir, default_provider="demo"):
        self.data_dir = data_dir
        self.default_provider = default_provider
        self.backup_dir = os.path.join(data_dir, "backups")
        os.makedirs(self.backup_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(data_dir, "favorites.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -------- настройки

    def settings(self):
        with self.lock:
            s = {r["k"]: r["v"] for r in self.db.execute("SELECT k, v FROM settings")}
        s.setdefault("provider", self.default_provider)
        return s

    def save_settings(self, values):
        with self.lock:
            for k, v in values.items():
                if k in ("provider", "token_api-cloud", "token_api-assist"):
                    self.db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (k, str(v).strip()))
            self.db.commit()

    def public_settings(self):
        s = self.settings()
        return {"provider": s["provider"],
                "has_token": {p: bool(s.get(f"token_{p}")) for p in ("api-cloud", "api-assist")}}

    # -------- дела

    def _row(self, r, full=False):
        data = json.loads(r["data"]) if r["data"] else None
        new_keys = json.loads(r["new_events"])
        evs = all_events(data)
        debtor = debtor_of(data) if data and data.get("type_code") == "bankruptcy" else None
        upcoming = [h for h in hearings_of(data) if h["start"] >= now()[:16]]
        item = {
            "id": r["id"], "number": r["number"] or "", "case_id": r["case_id"] or "",
            "title": r["custom_title"] or r["auto_title"] or r["number"] or r["case_id"],
            "custom_title": r["custom_title"], "auto_title": r["auto_title"],
            "tags": json.loads(r["tags"]), "notes": r["notes"],
            "stage_manual": r["stage_manual"], "stage_auto": detect_stage(data),
            "pinned": bool(r["pinned"]), "archived": bool(r["archived"]),
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "last_checked": r["last_checked"], "last_error": r["last_error"],
            "new_count": len(new_keys), "loaded": data is not None,
            "url": (data or {}).get("url") or (f"https://kad.arbitr.ru/Card/{r['case_id']}" if r["case_id"] else ""),
            "court": (data or {}).get("court", ""), "type_code": (data or {}).get("type_code", ""),
            "type_title": (data or {}).get("type_title", ""), "state": (data or {}).get("state", ""),
            "finished": bool((data or {}).get("finished")), "start_date": (data or {}).get("start_date", ""),
            "judges": (data or {}).get("judges", []),
            "debtor": debtor, "last_event": evs[0] if evs else None,
            "next_hearing": upcoming[0] if upcoming else None,
        }
        item["stage"] = item["stage_manual"] or item["stage_auto"]
        if full:
            item["data"] = data
            item["events"] = [{**e, "is_new": e["key"] in new_keys} for e in evs]
            item["hearings"] = hearings_of(data)
        return item

    def list_cases(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM cases ORDER BY pinned DESC, created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, cid, full=True):
        with self.lock:
            r = self.db.execute("SELECT * FROM cases WHERE id = ?", (cid,)).fetchone()
        return self._row(r, full) if r else None

    def add(self, entry, title="", tags=None):
        """Добавляет дело без загрузки данных. Возвращает (id, created)."""
        with self.lock:
            if entry.get("number"):
                r = self.db.execute("SELECT id FROM cases WHERE number = ?", (entry["number"],)).fetchone()
            else:
                r = self.db.execute("SELECT id FROM cases WHERE lower(case_id) = ?", (entry["case_id"],)).fetchone()
            if r:
                return r["id"], False
            t = now()
            cur = self.db.execute(
                "INSERT INTO cases (number, case_id, custom_title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (entry.get("number"), entry.get("case_id"), title.strip(), json.dumps(tags or [], ensure_ascii=False), t, t))
            self.db.commit()
            return cur.lastrowid, True

    def update(self, cid, fields):
        allowed = {"custom_title": str, "notes": str, "stage_manual": str, "pinned": int, "archived": int}
        sets, vals = [], []
        for k, conv in allowed.items():
            if k in fields:
                sets.append(f"{k} = ?")
                vals.append(conv(fields[k]))
        if "tags" in fields:
            tags = sorted({t.strip() for t in fields["tags"] if t and t.strip()})
            sets.append("tags = ?")
            vals.append(json.dumps(tags, ensure_ascii=False))
        if fields.get("mark_seen"):
            sets.append("new_events = '[]'")
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(now())
        with self.lock:
            self.db.execute(f"UPDATE cases SET {', '.join(sets)} WHERE id = ?", (*vals, cid))
            self.db.commit()

    def delete(self, cid):
        with self.lock:
            self.db.execute("DELETE FROM cases WHERE id = ?", (cid,))
            self.db.execute("DELETE FROM feed WHERE case_ref = ?", (cid,))
            self.db.commit()

    def mark_all_seen(self):
        with self.lock:
            self.db.execute("UPDATE cases SET new_events = '[]'")
            self.db.commit()

    # -------- загрузка/обновление из API

    def refresh(self, cid, provider):
        with self.lock:
            r = self.db.execute("SELECT * FROM cases WHERE id = ?", (cid,)).fetchone()
        if not r:
            raise KeyError(cid)
        try:
            data = provider.case_info(number=r["number"], case_id=None if r["number"] else r["case_id"])
        except providers.ProviderError as e:
            with self.lock:
                self.db.execute("UPDATE cases SET last_error = ?, last_checked = ? WHERE id = ?", (str(e), now(), cid))
                self.db.commit()
            raise
        old = json.loads(r["data"]) if r["data"] else None
        new_keys = set(json.loads(r["new_events"]))
        added = []
        if old is not None:  # при первой загрузке всё старое — не «новое»
            old_keys = {e["key"] for e in all_events(old)}
            added = [e for e in all_events(data) if e["key"] not in old_keys]
            new_keys |= {e["key"] for e in added}
        number = data.get("number") or r["number"]
        t = now()
        with self.lock:
            dup = self.db.execute("SELECT id FROM cases WHERE number = ? AND id != ?", (number, cid)).fetchone()
            if dup:  # добавляли по ссылке, а такой номер уже есть
                number = r["number"]
            self.db.execute(
                "UPDATE cases SET number = ?, case_id = ?, auto_title = ?, data = ?, new_events = ?, "
                "last_checked = ?, last_error = '', updated_at = ? WHERE id = ?",
                (number, data.get("case_id") or r["case_id"], auto_title(data),
                 json.dumps(data, ensure_ascii=False), json.dumps(sorted(new_keys)), t, t, cid))
            for e in reversed(added):
                self.db.execute("INSERT INTO feed (case_ref, ts, event) VALUES (?,?,?)",
                                (cid, t, json.dumps(e, ensure_ascii=False)))
            self.db.commit()
        return len(added)

    def feed(self, limit=300):
        with self.lock:
            rows = self.db.execute(
                "SELECT f.id, f.ts, f.event, c.id AS cid, c.number, c.custom_title, c.auto_title "
                "FROM feed f JOIN cases c ON c.id = f.case_ref ORDER BY f.id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "case": r["cid"], "number": r["number"],
                 "title": r["custom_title"] or r["auto_title"] or r["number"], "event": json.loads(r["event"])}
                for r in rows]

    # -------- резервные копии

    def backup_dict(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM cases ORDER BY id").fetchall()
        return {
            "format": BACKUP_FORMAT, "version": BACKUP_VERSION, "exported_at": now(),
            "count": len(rows),
            "cases": [{
                "number": r["number"], "case_id": r["case_id"], "custom_title": r["custom_title"],
                "auto_title": r["auto_title"], "tags": json.loads(r["tags"]), "notes": r["notes"],
                "stage_manual": r["stage_manual"], "pinned": bool(r["pinned"]), "archived": bool(r["archived"]),
                "created_at": r["created_at"], "last_checked": r["last_checked"],
                "data": json.loads(r["data"]) if r["data"] else None,
            } for r in rows],
        }

    def import_backup(self, backup, mode="merge"):
        """mode='merge' — добавить новые и дополнить существующие; 'replace' — заменить всё."""
        if not isinstance(backup, dict) or backup.get("format") != BACKUP_FORMAT:
            raise ValueError("Это не файл резервной копии этой программы")
        items = backup.get("cases") or []
        added = updated = 0
        with self.lock:
            if mode == "replace":
                self.auto_backup(force=True, label="before-replace")
                self.db.execute("DELETE FROM cases")
                self.db.execute("DELETE FROM feed")
            for it in items:
                number = normalize_number(it.get("number")) if it.get("number") else None
                case_id = (it.get("case_id") or "").lower() or None
                if not number and not case_id:
                    continue
                r = None
                if number:
                    r = self.db.execute("SELECT * FROM cases WHERE number = ?", (number,)).fetchone()
                if not r and case_id:
                    r = self.db.execute("SELECT * FROM cases WHERE lower(case_id) = ?", (case_id,)).fetchone()
                data = json.dumps(it["data"], ensure_ascii=False) if it.get("data") else None
                if r:
                    tags = sorted(set(json.loads(r["tags"])) | set(it.get("tags") or []))
                    notes = r["notes"]
                    if it.get("notes") and it["notes"] not in notes:
                        notes = (notes + "\n\n" + it["notes"]).strip()
                    self.db.execute(
                        "UPDATE cases SET custom_title = ?, tags = ?, notes = ?, stage_manual = ?, "
                        "data = COALESCE(data, ?), auto_title = CASE WHEN auto_title = '' THEN ? ELSE auto_title END, "
                        "updated_at = ? WHERE id = ?",
                        (r["custom_title"] or it.get("custom_title") or "", json.dumps(tags, ensure_ascii=False),
                         notes, r["stage_manual"] or it.get("stage_manual") or "", data,
                         it.get("auto_title") or "", now(), r["id"]))
                    updated += 1
                else:
                    self.db.execute(
                        "INSERT INTO cases (number, case_id, custom_title, auto_title, tags, notes, stage_manual, "
                        "pinned, archived, created_at, updated_at, last_checked, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (number, case_id, it.get("custom_title") or "", it.get("auto_title") or "",
                         json.dumps(it.get("tags") or [], ensure_ascii=False), it.get("notes") or "",
                         it.get("stage_manual") or "", int(bool(it.get("pinned"))), int(bool(it.get("archived"))),
                         it.get("created_at") or now(), now(), it.get("last_checked") or "", data))
                    added += 1
            self.db.commit()
        return {"added": added, "updated": updated}

    def auto_backup(self, force=False, label="auto"):
        """Ежедневная копия в data/backups (одна на день, хранятся последние 30)."""
        name = f"{label}-{datetime.now():%Y-%m-%d}.json" if label == "auto" else f"{label}-{datetime.now():%Y-%m-%d_%H%M%S}.json"
        path = os.path.join(self.backup_dir, name)
        with self.lock:  # копии из разных потоков не должны писать один файл одновременно
            count = self.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            if count == 0 and not force:
                return None
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(self.backup_dict(), f, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
            autos = sorted(p for p in os.listdir(self.backup_dir) if p.startswith("auto-"))
            for old in autos[:-KEEP_AUTO_BACKUPS]:
                os.remove(os.path.join(self.backup_dir, old))
        return name

    def list_backups(self):
        out = []
        for name in sorted(os.listdir(self.backup_dir), reverse=True):
            if name.endswith(".json"):
                p = os.path.join(self.backup_dir, name)
                out.append({"name": name, "size": os.path.getsize(p),
                            "mtime": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
        return out

    def read_backup(self, name):
        if os.path.basename(name) != name or not name.endswith(".json"):
            raise ValueError("Неверное имя файла")
        with open(os.path.join(self.backup_dir, name), encoding="utf-8") as f:
            return json.load(f)

    # -------- экспорт

    def export_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Номер дела", "Название", "Автоназвание", "Тип", "Стадия", "Суд", "Судья", "Должник", "ИНН должника",
                    "Состояние", "Дата регистрации", "Последнее событие", "Ближайшее заседание",
                    "Метки", "Заметки", "Архив", "Ссылка"])
        for c in self.list_cases():
            le = c["last_event"]
            w.writerow([c["number"], c["title"], c["auto_title"], c["type_title"], c["stage"], c["court"],
                        ", ".join(c["judges"]), (c["debtor"] or {}).get("name", ""), (c["debtor"] or {}).get("inn", ""),
                        c["state"], c["start_date"],
                        f"{le['date']} {le['type']} {le.get('content', '')}".strip() if le else "",
                        (c["next_hearing"] or {}).get("start", "").replace("T", " "),
                        ", ".join(c["tags"]), c["notes"], "да" if c["archived"] else "", c["url"]])
        return "﻿" + buf.getvalue()  # BOM — чтобы Excel открыл кириллицу

    def export_numbers(self):
        return "\n".join(c["number"] or c["url"] for c in self.list_cases()) + "\n"

    def export_ics(self):
        def esc(s):
            return (s or "").replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")

        def dt(s):
            return s.replace("-", "").replace(":", "")[:13] + "00"

        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//kad-favorites//RU", "CALSCALE:GREGORIAN",
                 "X-WR-CALNAME:Судебные заседания (избранное)", "X-WR-TIMEZONE:Europe/Moscow"]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for c in self.list_cases():
            if c["archived"]:
                continue
            full = self.get(c["id"])
            for h in full["hearings"]:
                start = h["start"]
                end = h["end"] or (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="minutes")
                lines += ["BEGIN:VEVENT", f"UID:{c['id']}-{dt(start)}@kad-favorites", f"DTSTAMP:{stamp}",
                          f"DTSTART:{dt(start)}", f"DTEND:{dt(end)}",
                          f"SUMMARY:{esc(c['number'] + ' — ' + c['title'])}",
                          f"LOCATION:{esc(h.get('location'))}",
                          f"DESCRIPTION:{esc((h.get('summary') or '') + chr(10) + c['url'])}",
                          "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", f"DESCRIPTION:{esc(c['number'])}", "END:VALARM",
                          "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"
