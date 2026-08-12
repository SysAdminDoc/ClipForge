import os
from pathlib import Path

from clipforge.proxy import PROXY_PROFILE, ProxyCache


def test_proxy_cache_keys_source_metadata_and_validates_manifest(tmp_path):
    source = tmp_path / "source video.mp4"
    source.write_bytes(b"source-v1")
    cache = ProxyCache(tmp_path / "cache")
    first_key = cache.key_for(source)
    proxy_path, _manifest_path = cache.paths_for(source)
    proxy_path.write_bytes(b"proxy-data")
    cache.record(source, proxy_path)

    assert cache.lookup(source) == proxy_path
    assert PROXY_PROFILE["max_width"] == 1280

    source.write_bytes(b"source-v2-with-different-size")
    os.utime(source, None)
    assert cache.key_for(source) != first_key
    assert cache.lookup(source) is None


def test_proxy_cache_rejects_same_metadata_content_changes_and_corruption(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-v1")
    cache = ProxyCache(tmp_path / "cache")
    first_key = cache.key_for(source)
    original_stat = source.stat()
    source.write_bytes(b"source-v2")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert cache.key_for(source) != first_key

    proxy_path, _manifest_path = cache.paths_for(source)
    proxy_path.write_bytes(b"proxy-data")
    cache.record(source, proxy_path)
    proxy_path.write_bytes(b"proxy-tata")
    assert cache.lookup(source) is None


def test_proxy_cache_reports_usage_and_can_be_cleared(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    cache = ProxyCache(tmp_path / "cache", max_bytes=1024)
    proxy_path, _manifest_path = cache.paths_for(source)
    proxy_path.write_bytes(b"proxy")
    cache.record(source, proxy_path)

    stats = cache.stats()
    assert stats["bytes"] == len(b"proxy")
    assert stats["entries"] == 1
    assert stats["max_bytes"] == 1024
    assert len(cache.clear()) >= 2
    assert cache.stats()["bytes"] == 0


def test_proxy_cache_cleans_interrupted_files_and_prunes_oldest(tmp_path):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    interrupted = cache_root / ".proxy.clipforge-crash.mp4"
    partial = cache_root / "manifest.json.partial"
    interrupted.write_bytes(b"partial")
    partial.write_bytes(b"partial")
    cache = ProxyCache(cache_root)

    assert not interrupted.exists()
    assert not partial.exists()

    old_proxy = cache_root / "old.mp4"
    new_proxy = cache_root / "new.mp4"
    old_proxy.write_bytes(b"a" * 10)
    new_proxy.write_bytes(b"b" * 10)
    old_proxy.with_suffix(".json").write_text("{}", encoding="utf-8")
    new_proxy.with_suffix(".json").write_text("{}", encoding="utf-8")
    os.utime(old_proxy, (1, 1))
    os.utime(new_proxy, (2, 2))

    removed = cache.prune(max_bytes=10)
    assert removed == [old_proxy]
    assert not old_proxy.exists()
    assert new_proxy.exists()


def test_proxy_command_and_size_estimate_are_bounded(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    cache = ProxyCache(tmp_path / "cache")
    output, _manifest = cache.paths_for(source)
    command = cache.command("ffmpeg", source, output)

    assert command[0] == "ffmpeg"
    assert str(source.resolve()) in command
    assert str(output) == command[-1]
    assert any(r"min(1280\,iw)" in part for part in command)
    assert cache.estimate_size({"duration": 60, "width": 3840}) > cache.estimate_size(
        {"duration": 60, "width": 1280}
    )
