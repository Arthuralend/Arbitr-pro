"""python -m unittest discover tests   (из папки программы)"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402
import providers  # noqa: E402

# Пример ответа caseInfo из документации api-cloud.ru (без комментариев)
API_CLOUD_CASE = {
    "status": 200, "found": True,
    "Result": {
        "CaseInfo": {"CaseId": "a1b872f8-355f-4ab9-9f00-09bb91959f73", "CaseNumber": "А57-10677/2024",
                     "StartDateUnix": "1713906000", "StartDate": "24.04.2024", "finish": "false",
                     "State": "Рассматривается в первой инстанции", "typeCode": "bankruptcy",
                     "typeTitle": "Банкротное дело"},
        "Participants": {
            "Plaintiffs": [{"Id": "ba32", "Name": "Симаков Максим Иванович", "Address": "Данные скрыты",
                            "INN": "645399785646", "BirthDate": None}],
            "Respondents": [{"Id": "e29c", "Name": "Симаков Максим Иванович", "Address": "Данные скрыты",
                             "INN": "645399785646", "BirthDate": None}],
            "Thirds": [{"Id": "1a85", "Name": "АО Почта Банк", "Address": "107061, Москва", "INN": None}],
            "Others": []},
        "CaseInstances": [{
            "Id": "06f8", "InstanceNumber": "А57-10677/2024", "InstanceLevel": 1,
            "NextInstanceEvent": "Следующее заседание: 04.06.2024, 10:10 , 1410",
            "Court": {"Id": "c9d9", "Name": "АС Саратовской области", "Url": "http://saratov.arbitr.ru/"},
            "Judges": [{"Id": "5618", "Name": "Королева И. Ю."}],
            "InstanceEvents": [
                {"EventTypeName": "Определение", "EventTypeId": "3f4a",
                 "AdditionalInfo": "Дата и время судебного заседания 04.06.2024, 10:10, 1410",
                 "ContentTypes": ["Принять к производству заявление о признании должника банкротом."],
                 "Date": "03.05.2024", "PublishDate": "05.09.2025, 10:19:23",
                 "File": "https://kad.arbitr.ru/Kad/PdfDocument/a1b8/9d85/A57-10677-2024_20240503_Opredelenie.pdf",
                 "Declarers": None, "DeclarerInn": None, "Comment": None, "ClaimSum": 0},
                {"EventTypeName": "Заявление (исковое заявление)", "EventTypeId": "f51e",
                 "AdditionalInfo": ". Сумма исковых требований 1567424,91", "Date": "24.04.2024",
                 "PublishDate": "05.09.2025, 10:19:23", "File": None, "Declarers": "Симаков Максим Иванович",
                 "DeclarerInn": "645399785646", "Comment": None, "ClaimSum": 1567424.91}],
            "CourtHearings": [{"Location": "г. Саратов, зал 1410", "Summary": "Заседание по делу А57-10677/2024",
                               "Start": "2024-06-04T10:10:00+4", "End": "2024-06-04T11:10:00+4"}]}]},
}

# Пример ответа details_by_id из документации api-assist.com
API_ASSIST_CASE = {
    "Success": 1,
    "Cases": [{
        "CaseId": "6fb9afec-b71d-4183-b917-4cace5958c16", "CaseNumber": "А71-1202/2015", "CaseType": "А",
        "Plaintiffs": [{"Name": 'ООО "Детство"', "Address": "101000, Москва", "Id": "3c79"}],
        "Respondents": [{"Name": "Межрайонная ИФНС России №9 по Удмуртской Республике", "Address": "Ижевск", "Id": "b49d"}],
        "StartDate": "2015-02-06", "State": "Рассмотрение дела завершено", "Finished": True,
        "CaseInstances": [{
            "Id": "5400", "InstanceNumber": "А71-1202/15", "Name": "Первая инстанция",
            "State": "Следующее заседание: 18.11.2020, 10:15 , №217 Матвеева Н.В.",
            "Court": {"Code": "UDMURTIYA", "Name": "АС Удмуртской Республики"},
            "Judges": ["Бушуева Е. А."],
            "InstanceEvents": [{
                "EventTypeName": "Определение", "Id": "8d0e1fc6-9df7-46bb-8530-10a971190666",
                "AdditionalInfo": "Дата и время судебного заседания 20.01.2016, 11:00, 22", "Date": "2015-12-11",
                "File": "https://kad.arbitr.ru/PdfDocument/8d0e/A71-1202-2015_20151211_Opredelenie.pdf",
                "PublishDate": "2015-12-12", "EventContentTypeName": "Об отложении рассмотрения заявления/жалобы",
                "Declarer": "АНО ВНИИКОП-ОСТРОВ", "DeclarerInn": "5003079985", "ClaimSum": 10000}]}],
        "CourtHearings": [{"Location": "Ижевск, ул. Ломоносова 5, 22", "Summary": "Заседание",
                           "Start": "2015-03-19T11:00:00+04", "End": "2015-03-19T12:00:00+04"}]}],
}


class FakeProvider:
    def __init__(self, data):
        self.data = data

    def case_info(self, number=None, case_id=None):
        return json.loads(json.dumps(self.data))


class TestNumbers(unittest.TestCase):
    def test_parse(self):
        entries, errors = core.parse_entries(
            "a40-12345/2024\n А41-7890/25 ; А40-12345/2024, мусор,\n"
            "https://kad.arbitr.ru/Card/523A1650-18f5-4140-b201-f0fc59631fee\nА40-1/2024-15-60")
        self.assertEqual(entries, [
            {"number": "А40-12345/2024"}, {"number": "А41-7890/25"},
            {"case_id": "523a1650-18f5-4140-b201-f0fc59631fee"}, {"number": "А40-1/2024-15-60"}])
        self.assertEqual(errors, ["мусор"])


class TestProviders(unittest.TestCase):
    def test_api_cloud(self):
        d = providers.ApiCloud.normalize(API_CLOUD_CASE["Result"])
        self.assertEqual(d["number"], "А57-10677/2024")
        self.assertEqual(d["type_code"], "bankruptcy")
        self.assertEqual(d["start_date"], "2024-04-24")
        self.assertEqual(d["court"], "АС Саратовской области")
        self.assertEqual(d["judges"], ["Королева И. Ю."])
        self.assertEqual(len(d["instances"][0]["events"]), 2)
        self.assertEqual(d["instances"][0]["events"][1]["info"], "Сумма исковых требований 1567424,91")
        self.assertEqual(d["participants"]["thirds"][0]["inn"], "")
        self.assertEqual(core.auto_title(d), "Банкротство: Симаков Максим Иванович")
        self.assertEqual(core.hearings_of(d)[0]["start"], "2024-06-04T10:10")

    def test_api_assist(self):
        d = providers.ApiAssist.normalize(API_ASSIST_CASE["Cases"][0])
        self.assertEqual(d["type_code"], "admin")
        self.assertTrue(d["finished"])
        self.assertEqual(d["instances"][0]["events"][0]["key"], "8d0e1fc6-9df7-46bb-8530-10a971190666")
        self.assertEqual(core.auto_title(d), "ООО «Детство» — Межрайонная ИФНС России №9 по Удмуртской Республике")
        starts = [h["start"] for h in core.hearings_of(d)]
        self.assertEqual(starts, ["2015-03-19T11:00", "2020-11-18T10:15"])


class TestStage(unittest.TestCase):
    def test_detect(self):
        d = providers.ApiCloud.normalize(API_CLOUD_CASE["Result"])
        self.assertEqual(core.detect_stage(d), "")
        d["instances"][0]["events"].insert(0, {"date": "2024-07-01", "type": "Определение", "key": "x",
                                               "content": "Признать заявление обоснованным и ввести процедуру наблюдения"})
        self.assertEqual(core.detect_stage(d), "Наблюдение")
        d["instances"][0]["events"].insert(0, {"date": "2024-12-01", "type": "Решение", "key": "y",
                                               "content": "Признать должника банкротом и ввести процедуру реализации имущества гражданина"})
        self.assertEqual(core.detect_stage(d), "Реализация имущества")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.tmp.name)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_flow(self):
        data = providers.ApiCloud.normalize(API_CLOUD_CASE["Result"])
        cid, created = self.store.add({"number": "А57-10677/2024"})
        self.assertTrue(created)
        self.assertFalse(self.store.add({"number": "А57-10677/2024"})[1])

        # первая загрузка — без «новых»
        self.assertEqual(self.store.refresh(cid, FakeProvider(data)), 0)
        c = self.store.get(cid)
        self.assertEqual(c["title"], "Банкротство: Симаков Максим Иванович")

        # появилось новое событие
        data["instances"][0]["events"].insert(0, {"date": "2024-06-04", "type": "Определение", "content": "Об отложении",
                                                  "info": "", "file": "", "declarer": "", "key": "new1"})
        self.assertEqual(self.store.refresh(cid, FakeProvider(data)), 1)
        self.assertEqual(self.store.get(cid)["new_count"], 1)
        self.assertEqual(len(self.store.feed()), 1)

        # своё название, метки, архив
        self.store.update(cid, {"custom_title": "Симаков — мой кредит", "tags": ["банк", " клиент "], "mark_seen": True})
        c = self.store.get(cid)
        self.assertEqual(c["title"], "Симаков — мой кредит")
        self.assertEqual(c["auto_title"], "Банкротство: Симаков Максим Иванович")
        self.assertEqual(c["tags"], ["банк", "клиент"])
        self.assertEqual(c["new_count"], 0)

        # резервная копия -> чистый профиль -> восстановление
        backup = json.loads(json.dumps(self.store.backup_dict()))
        with tempfile.TemporaryDirectory() as d2:
            s2 = core.Store(d2)
            self.assertEqual(s2.import_backup(backup), {"added": 1, "updated": 0})
            c2 = s2.list_cases()[0]
            self.assertEqual(c2["title"], "Симаков — мой кредит")
            self.assertEqual(c2["court"], "АС Саратовской области")
            self.assertEqual(s2.import_backup(backup), {"added": 0, "updated": 1})
            self.assertEqual(len(s2.list_cases()), 1)
            s2.db.close()

        self.assertIn("А57-10677/2024", self.store.export_csv())
        self.assertIn("BEGIN:VEVENT", self.store.export_ics())
        self.assertTrue(self.store.auto_backup())
        self.assertEqual(len(self.store.list_backups()), 1)

    def test_add_by_link(self):
        data = providers.ApiCloud.normalize(API_CLOUD_CASE["Result"])
        cid, _ = self.store.add({"case_id": "a1b872f8-355f-4ab9-9f00-09bb91959f73"})
        self.store.refresh(cid, FakeProvider(data))
        self.assertEqual(self.store.get(cid)["number"], "А57-10677/2024")


if __name__ == "__main__":
    unittest.main()
