import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from clipforge.mpv_backend import MpvCapability, MpvWidget, native_version_status, probe_mpv


_QT_APP = QApplication.instance() or QApplication([])


class _FakePlayer:
    def __init__(self, **options):
        self.options = options
        self.commands = []
        self.observers = {}
        self.pause = True
        self.speed = 1.0
        self.volume = 70.0
        self.time_pos = 0.0
        self.terminated = False

    def observe_property(self, name, callback):
        self.observers[name] = callback

    def command(self, *parts):
        self.commands.append(parts)

    def terminate(self):
        self.terminated = True


def test_mpv_widget_embeds_and_maps_transport_controls(monkeypatch, tmp_path):
    players = []
    fake_module = types.ModuleType("mpv")

    def create_player(**options):
        player = _FakePlayer(**options)
        players.append(player)
        return player

    fake_module.MPV = create_player
    monkeypatch.setitem(sys.modules, "mpv", fake_module)
    monkeypatch.setattr(
        "clipforge.mpv_backend.probe_mpv",
        lambda: MpvCapability(True, "1.0.8"),
    )

    media = tmp_path / "sample video.mp4"
    media.write_bytes(b"media")
    widget = MpvWidget()
    widget.load(media)
    widget.play()
    widget.seek(1.25)
    widget.frame_step(1)
    widget.frame_step(-1)
    widget.set_speed(1.5)
    widget.set_volume(42)

    player = players[0]
    assert int(player.options["wid"]) >= 0
    assert player.options["terminal"] is False
    assert player.options["input_default_bindings"] is False
    assert player.commands[0] == ("loadfile", str(media.resolve()), "replace")
    assert ("seek", 1.25, "absolute+exact") in player.commands
    assert ("frame-step",) in player.commands
    assert ("frame-back-step",) in player.commands
    assert player.pause is False
    assert player.speed == 1.5
    assert player.volume == 42

    widget.shutdown()
    assert player.terminated
    widget.close()


def test_mpv_widget_applies_resume_position_as_file_option(monkeypatch, tmp_path):
    players = []
    fake_module = types.ModuleType("mpv")
    fake_module.MPV = lambda **options: players.append(_FakePlayer(**options)) or players[-1]
    monkeypatch.setitem(sys.modules, "mpv", fake_module)
    monkeypatch.setattr(
        "clipforge.mpv_backend.probe_mpv",
        lambda: MpvCapability(True, "1.0.8"),
    )

    media = tmp_path / "resume.mp4"
    media.write_bytes(b"media")
    widget = MpvWidget()
    widget.load(media, start=12.5)

    assert players[0].commands[0] == (
        "loadfile",
        str(media.resolve()),
        "replace",
        "-1",
        "start=12.500000",
    )
    assert players[0].pause is True
    widget.shutdown()
    widget.close()


def test_mpv_native_version_below_minimum_falls_back(monkeypatch):
    fake_module = types.ModuleType("mpv")
    monkeypatch.setitem(sys.modules, "mpv", fake_module)
    monkeypatch.setattr("clipforge.mpv_backend.importlib.metadata.version", lambda _name: "1.0.8")
    monkeypatch.setattr("clipforge.mpv_backend._native_file_version", lambda _path: "0.40.0.0")

    capability = probe_mpv()

    assert native_version_status("0.40.0.0") == "outdated"
    assert capability.available is False
    assert capability.native_version == "0.40.0.0"
    assert capability.native_version_status == "outdated"
    assert "Qt Multimedia fallback" in capability.reason
