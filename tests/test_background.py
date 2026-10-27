import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_services as bs
from tools import projectbackuptool as pb


class ServicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(bs, "data_dir", lambda: Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flags_default_off_and_round_trip(self):
        self.assertEqual(bs.get_services(), {"auto_ingest": False, "backup": False})
        bs.set_service("backup", True)
        self.assertEqual(bs.get_services(), {"auto_ingest": False, "backup": True})
        bs.set_service("backup", False)
        self.assertFalse(bs.get_services()["backup"])

    def test_corrupt_settings_read_as_off(self):
        (Path(self.tmp.name) / "background_services.json").write_text("{nope")
        self.assertFalse(bs.get_services()["backup"])

    def test_port_lock_allows_one_owner(self):
        first, second = bs.PortLock(53791), bs.PortLock(53791)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertTrue(bs.is_running(53791))
        first.release()
        self.assertFalse(bs.is_running(53791))

    def test_handover_launches_agent_once_needed(self):
        with mock.patch.object(bs, "tray_available", return_value=True), \
                mock.patch.object(bs, "is_running", return_value=False), \
                mock.patch.object(bs, "launch_agent") as launch:
            self.assertTrue(bs.handover("auto_ingest"))
        launch.assert_called_once()
        self.assertTrue(bs.get_services()["auto_ingest"])

    def test_handover_does_not_launch_a_second_agent(self):
        with mock.patch.object(bs, "tray_available", return_value=True), \
                mock.patch.object(bs, "is_running", return_value=True), \
                mock.patch.object(bs, "launch_agent") as launch:
            self.assertTrue(bs.handover("backup"))
        launch.assert_not_called()

    def test_handover_refused_without_tray_support(self):
        with mock.patch.object(bs, "tray_available", return_value=False):
            self.assertFalse(bs.handover("backup"))
        self.assertFalse(bs.get_services()["backup"])

    def test_handover_rolls_back_when_launch_fails(self):
        with mock.patch.object(bs, "tray_available", return_value=True), \
                mock.patch.object(bs, "is_running", return_value=False), \
                mock.patch.object(bs, "launch_agent", side_effect=OSError("no")):
            self.assertFalse(bs.handover("backup"))
        self.assertFalse(bs.get_services()["backup"])


class BackupConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "Proj")
        self.dest = os.path.join(self.tmp.name, "nas")
        os.makedirs(self.src)
        os.makedirs(self.dest)
        with open(os.path.join(self.src, "a.txt"), "w") as handle:
            handle.write("hi")
        self.config = {"project_path": [self.src], "cloud_enabled": False, "local_enabled": True,
                       "local_path": self.dest, "local_mode": "mirror", "local_keep": "3",
                       "backup_folder": "B", "exclude_patterns": ""}

    def test_config_problems(self):
        self.assertEqual(pb.config_problem(self.config), "")
        self.assertIn("project folder", pb.config_problem(dict(self.config, project_path=[])))
        self.assertIn("does not exist", pb.config_problem(dict(self.config, project_path=[self.src + "x"])))
        self.assertIn("destination", pb.config_problem(dict(self.config, local_enabled=False)))
        self.assertIn("Local / NAS", pb.config_problem(dict(self.config, local_path="")))
        self.assertIn("whole number", pb.config_problem(dict(self.config, local_keep="x")))

    def test_cloud_needs_sign_in(self):
        problem = pb.config_problem(dict(self.config, cloud_enabled=True, dropbox_key="K"))
        self.assertTrue(problem)

    def test_run_backup_headless(self):
        messages = []
        self.assertEqual(pb.run_backup(self.config, messages.append), [])
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "B", "Latest", "Proj", "a.txt")))

    def test_run_backup_reports_failed_destination(self):
        failures = pb.run_backup(dict(self.config, local_path=os.path.join(self.tmp.name, "unplugged")), lambda m: None)
        self.assertEqual(failures, ["local"])

    def test_scheduling(self):
        import schedule
        for frequency in ("hourly", "daily", "weekly"):
            scheduler = schedule.Scheduler()
            pb.add_backup_job(scheduler, {"frequency": frequency, "hour": "02", "minute": "30"}, lambda: None)
            self.assertEqual(len(scheduler.jobs), 1)
            self.assertIsNotNone(scheduler.next_run)
        with self.assertRaises(ValueError):
            pb.add_backup_job(schedule.Scheduler(), {"frequency": "daily", "hour": "25", "minute": "0"}, lambda: None)
        with self.assertRaises(ValueError):
            pb.add_backup_job(schedule.Scheduler(), {"frequency": "yearly"}, lambda: None)


class FakeObserver:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass


class AgentTests(unittest.TestCase):
    def setUp(self):
        import tray_agent
        self.tray_agent = tray_agent
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rules_file = os.path.join(self.tmp.name, "rules.json")
        self.config_file = os.path.join(self.tmp.name, "backup.json")
        self.problems = []
        self.observers = []
        outer = self

        sorter = types.SimpleNamespace(
            RULES_FILE=self.rules_file,
            load_rules=lambda path: [{"name": "r"}],
            validate_rules=lambda rules: list(outer.problems),
        )
        engine = types.SimpleNamespace(start_observer=lambda rules: outer.observers.append(FakeObserver()) or outer.observers[-1])
        self.jobs = []
        self.backup_problem = ""
        backup = types.SimpleNamespace(
            CONFIG_FILE=self.config_file,
            config_problem=lambda config: outer.backup_problem,
            add_backup_job=lambda scheduler, config, job: scheduler.every().day.at("03:00").do(job),
            run_backup=lambda config, log: [],
        )
        self.agent = tray_agent.Agent(types.SimpleNamespace(sorter=sorter, engine=engine, backup=backup))

    def touch(self, path, text="{}"):
        Path(path).write_text(text)
        os.utime(path, None)

    def test_ingest_starts_stops_and_restarts_on_rule_change(self):
        self.touch(self.rules_file)
        self.agent.sync_ingest(True)
        self.assertEqual(len(self.observers), 1)
        self.assertIn("watching 1", self.agent.ingest_text)
        self.agent.sync_ingest(True)
        self.assertEqual(len(self.observers), 1)
        os.utime(self.rules_file, ns=(1, self.tray_agent.stamp_of(self.rules_file) + 5_000_000_000))
        self.agent.sync_ingest(True)
        self.assertEqual(len(self.observers), 2)
        self.assertTrue(self.observers[0].stopped)
        self.agent.sync_ingest(False)
        self.assertTrue(self.observers[1].stopped)
        self.assertEqual(self.agent.ingest_text, "Auto Ingest: off")

    def test_ingest_problems_are_reported_not_started(self):
        self.touch(self.rules_file)
        self.problems = ["watch folder does not exist: X"]
        self.agent.sync_ingest(True)
        self.assertEqual(self.observers, [])
        self.assertIn("needs attention", self.agent.ingest_text)

    def test_backup_scheduled_then_cleared(self):
        self.touch(self.config_file)
        self.agent.sync_backup(True)
        self.assertTrue(self.agent.backup_scheduled)
        with mock.patch.object(bs, "get_services", return_value={"auto_ingest": False, "backup": True}):
            self.assertIn("next", self.agent.backup_text)
        self.agent.sync_backup(False)
        self.assertFalse(self.agent.backup_scheduled)
        self.assertEqual(self.agent.scheduler.jobs, [])

    def test_backup_with_problem_is_not_scheduled(self):
        self.touch(self.config_file)
        self.backup_problem = "Sign in first"
        self.agent.sync_backup(True)
        self.assertFalse(self.agent.backup_scheduled)
        self.assertIn("needs attention", self.agent.backup_state)

    def test_backup_job_records_result(self):
        self.touch(self.config_file, json.dumps({}))
        self.agent.run_backup_job()
        self.assertIn("OK", self.agent.backup_state)
        self.assertFalse(self.agent.backup_lock.locked())

    def test_agent_exits_after_idle(self):
        with mock.patch.object(bs, "get_services", return_value={"auto_ingest": False, "backup": False}), \
                mock.patch.object(self.tray_agent, "IDLE_EXIT_SECONDS", 0):
            self.agent.tick()
            self.assertFalse(self.agent.stop_event.is_set())
            self.agent.tick()
        self.assertTrue(self.agent.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
