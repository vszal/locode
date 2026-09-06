"""`_model_disk_bytes` must size the revision that will load, not the cache.

A model re-pulled after an upstream update keeps several revisions under
`snapshots/`, all symlinked to the same blobs. Summing them refused an 11 GB
model as 21.9 GB. See ROADMAP §5.139.
"""
import os

import pytest

from locode.server import manager
from locode.server.manager import _model_disk_bytes

MID = "org/model"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A fake HF hub dir; returns a helper that builds revisions of blobs."""
    hub_dir = tmp_path / "hub"
    blobs = hub_dir / f"models--org--model" / "blobs"
    snaps = hub_dir / f"models--org--model" / "snapshots"
    blobs.mkdir(parents=True)
    snaps.mkdir(parents=True)
    monkeypatch.setattr(manager, "_hf_hub_dir", lambda: hub_dir)

    def make_blob(name: str, size: int):
        b = blobs / name
        b.write_bytes(b"\0" * size)
        return b

    def make_revision(rev: str, targets):
        d = snaps / rev
        d.mkdir()
        for i, blob in enumerate(targets, 1):
            (d / f"model-0000{i}.safetensors").symlink_to(blob)
        return d

    return make_blob, make_revision


def test_uncached_model_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_hf_hub_dir", lambda: tmp_path / "empty")
    assert _model_disk_bytes(MID) is None


def test_a_bare_model_id_is_none():
    assert _model_disk_bytes("no-slash") is None


def test_single_revision_sums_its_shards(hub):
    make_blob, make_revision = hub
    make_revision("rev1", [make_blob("a", 1000), make_blob("b", 2000)])
    assert _model_disk_bytes(MID) == 3000


def test_two_revisions_sharing_blobs_are_not_double_counted(hub):
    """The real bug: two revisions, same blobs, must measure one revision."""
    make_blob, make_revision = hub
    shards = [make_blob("a", 4000), make_blob("b", 6000)]
    make_revision("rev1", shards)
    make_revision("rev2", shards)
    assert _model_disk_bytes(MID) == 10000


def test_differing_revisions_take_the_largest(hub):
    make_blob, make_revision = hub
    make_revision("small", [make_blob("a", 1000)])
    make_revision("big", [make_blob("b", 5000), make_blob("c", 3000)])
    assert _model_disk_bytes(MID) == 8000


def test_duplicate_names_within_one_revision_count_once(hub):
    """Two filenames pointing at one blob is still one blob in memory."""
    make_blob, make_revision = hub
    blob = make_blob("a", 7000)
    d = make_revision("rev1", [blob])
    (d / "alias.safetensors").symlink_to(blob)
    assert _model_disk_bytes(MID) == 7000


def test_revision_with_no_safetensors_is_none(hub):
    make_blob, make_revision = hub
    make_revision("rev1", [])
    assert _model_disk_bytes(MID) is None


def test_a_broken_symlink_is_skipped_not_fatal(hub):
    make_blob, make_revision = hub
    d = make_revision("rev1", [make_blob("a", 2000)])
    (d / "gone.safetensors").symlink_to(d / "does-not-exist")
    assert _model_disk_bytes(MID) == 2000
