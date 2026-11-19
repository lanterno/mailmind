"""`just apple demo`'s launcher (shells/apple/Measure/demo_home.py) checks after each run that the Mac app kept its
preferences in the demo's temp home, apart from the person's own MailMind, and ends the demo saying why when it didn't."""
import importlib.util
import pathlib
import plistlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("demo_home", ROOT / "shells" / "apple" / "Measure" / "demo_home.py")
demo_home = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo_home)


def _kept(home, contents, identifier=demo_home.IDENTIFIER):
    folder = home / "Library" / "Preferences"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{identifier}.plist").write_bytes(contents)


def test_a_run_that_kept_its_preferences_in_its_home_says_what_it_kept(tmp_path, capsys):
    _kept(tmp_path, plistlib.dumps({"NSWindow Frame mail-AppWindow-1": "265 156 1180 760 0 0 1710 1073 ",
                                    "meadow.rail-open": True}))
    assert demo_home.kept_apart(str(tmp_path))["meadow.rail-open"] is True
    demo_home.check_kept_apart(str(tmp_path))
    assert capsys.readouterr().out == ("the Mac app kept its preferences in the demo's home, apart from yours: "
                                       "NSWindow Frame mail-AppWindow-1, meadow.rail-open\n")


def test_a_run_that_set_none_yet_still_kept_its_file_there(tmp_path, capsys):
    _kept(tmp_path, plistlib.dumps({}))
    demo_home.check_kept_apart(str(tmp_path))
    assert capsys.readouterr().out.endswith("apart from yours: none set yet\n")


def test_a_run_that_kept_nothing_in_its_home_ends_the_demo_saying_why(tmp_path):
    with pytest.raises(SystemExit) as ended:
        demo_home.check_kept_apart(str(tmp_path))
    assert "kept no preferences in the demo's home" in str(ended.value)
    assert "RunPreferences.swift" in str(ended.value)


@pytest.mark.parametrize("contents", [b"not a property list", plistlib.dumps(["a list, not a domain"])])
def test_a_file_that_isnt_a_domain_counts_as_none(tmp_path, contents):
    _kept(tmp_path, contents)
    assert demo_home.kept_apart(str(tmp_path)) is None
    with pytest.raises(SystemExit):
        demo_home.check_kept_apart(str(tmp_path))


def test_the_file_is_named_for_the_apps_own_identifier(tmp_path):
    contents = tmp_path / "Other.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.example.other"}))
    app = str(contents / "MacOS" / "Other")
    assert demo_home.identifier(app) == "io.example.other"
    _kept(tmp_path, plistlib.dumps({"k": 1}), identifier="io.example.other")
    assert demo_home.kept_apart(str(tmp_path), "io.example.other") == {"k": 1}
    assert demo_home.kept_apart(str(tmp_path)) is None, "another app's file is not this one's"


def test_an_app_with_no_info_plist_is_mailminds(tmp_path):
    assert demo_home.identifier(str(tmp_path / "MailMind")) == "io.github.lanterno.mailmind"
