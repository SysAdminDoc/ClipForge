import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QTextEdit

from clipforge.panels.audio import AudioPanel
from clipforge.panels.streams import StreamsPanel
from clipforge.widgets import FileInfoBar, VideoPlayer


_QT_APP = QApplication.instance() or QApplication([])


def test_player_surfaces_decode_error():
    player = VideoPlayer()
    errors = []
    player.playbackError.connect(errors.append)

    player._on_player_error(QMediaPlayer.Error.FormatError, "decoder missing")
    player._on_player_error(QMediaPlayer.Error.FormatError, "decoder missing")

    assert player.lbl_player_status.isVisibleTo(player)
    assert "not supported" in player.lbl_player_status.text()
    assert "decoder missing" in player.lbl_player_status.text()
    assert errors == [player.lbl_player_status.text()]
    player.close()


def test_file_bar_does_not_emit_loaded_for_invalid_media(tmp_path):
    invalid = tmp_path / "broken.mp4"
    invalid.write_bytes(b"broken")
    bar = FileInfoBar()
    loaded = []
    failed = []
    bar.fileLoaded.connect(lambda *args: loaded.append(args))
    bar.fileLoadFailed.connect(lambda *args: failed.append(args))

    bar.load_file(str(invalid))

    assert loaded == []
    assert failed
    assert "could not" in failed[0][1].lower()
    assert bar.filepath() is None
    bar.close()


def test_panels_preserve_non_contiguous_probe_stream_indexes():
    info = {
        "duration": 2.0,
        "streams": [
            {
                "index": 2,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 180,
                "fps": 24,
                "disposition": {"default": 1},
            },
            {
                "index": 5,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
                "disposition": {"default": 1},
            },
        ],
    }
    console = QTextEdit()
    streams = StreamsPanel(console)
    audio = AudioPanel(console)

    streams.load_file("source.mp4", info)
    streams._stream_checks[0].setChecked(False)
    audio.load_file("source.mp4", info)

    assert streams._selected_stream_indexes() == {5}
    assert audio.cmb_audio_stream.currentData() == 5
    assert audio._layout_args() == []
    audio.cmb_audio_layout.setCurrentText("Stereo")
    assert audio._layout_args() == ["-ac", "2"]
    streams.close()
    audio.close()
    console.close()
