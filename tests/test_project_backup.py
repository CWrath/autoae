import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.projectbackuptool import (
    check_destination, is_inside, parse_patterns, prune_snapshots, run_local_backup, unique_names,
)


def write(path, text="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))


class LocalBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "Project")
        self.dest = os.path.join(self.tmp.name, "nas")
        os.makedirs(self.dest)
        write(os.path.join(self.src, "a.txt"), "hello")
        write(os.path.join(self.src, "sub", "b.txt"), "world")
        write(os.path.join(self.src, "node_modules", "junk.js"), "junk")

    def test_parse_patterns_drops_blanks(self):
        self.assertEqual(parse_patterns("a, ,b,"), ["a", "b"])
        self.assertEqual(parse_patterns(""), [])

    def test_snapshot_copies_and_excludes(self):
        result = run_local_backup([self.src], self.dest, "Backups", "snapshots", ["node_modules"],
                                  now=datetime(2024, 1, 2, 3, 4, 5))
        target = os.path.join(self.dest, "Backups", "2024-01-02_030405", "Project")
        self.assertEqual(result["copied"], 2)
        self.assertTrue(os.path.isfile(os.path.join(target, "sub", "b.txt")))
        self.assertFalse(os.path.exists(os.path.join(target, "node_modules")))

    def test_mirror_skips_unchanged_and_recopies_changed(self):
        run_local_backup([self.src], self.dest, "B", "mirror", [])
        again = run_local_backup([self.src], self.dest, "B", "mirror", [])
        self.assertEqual((again["copied"], again["skipped"]), (0, 3))
        write(os.path.join(self.src, "a.txt"), "hello changed")
        third = run_local_backup([self.src], self.dest, "B", "mirror", [])
        self.assertEqual(third["copied"], 1)

    def test_prune_keeps_newest_and_ignores_other_folders(self):
        root = os.path.join(self.dest, "B")
        for name in ("2024-01-01_000000", "2024-01-02_000000", "2024-01-03_000000", "notes"):
            os.makedirs(os.path.join(root, name))
        self.assertEqual(prune_snapshots(root, 2), 1)
        self.assertEqual(sorted(os.listdir(root)), ["2024-01-02_000000", "2024-01-03_000000", "notes"])
        self.assertEqual(prune_snapshots(root, 0), 0)

    def test_destination_inside_source_rejected(self):
        with self.assertRaises(ValueError):
            run_local_backup([self.src], os.path.join(self.src, "sub"), "B", "snapshots", [])

    def test_unreachable_destination_raises_and_is_not_created(self):
        missing = os.path.join(self.tmp.name, "unplugged")
        ok, _ = check_destination(missing)
        self.assertFalse(ok)
        with self.assertRaises(OSError):
            run_local_backup([self.src], missing, "B", "snapshots", [])
        self.assertFalse(os.path.exists(missing))

    def test_duplicate_source_names(self):
        names = [n for _, n in unique_names(["/x/Project", "/y/Project"])]
        self.assertEqual(names, ["Project", "Project_2"])

    def test_is_inside(self):
        self.assertTrue(is_inside(os.path.join(self.src, "a"), self.src))
        self.assertFalse(is_inside(self.src + "2", self.src))


if __name__ == "__main__":
    unittest.main()
