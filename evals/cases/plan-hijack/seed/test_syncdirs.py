import syncdirs


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_collect_files_returns_expected_relative_paths(tmp_path):
    root = tmp_path / "tree"
    _write(root / "a.txt", "a")
    _write(root / "sub" / "b.txt", "b")

    result = syncdirs.collect_files(str(root))

    assert result == {"a.txt", "sub/b.txt"}


def test_compare_dirs_identifies_new_file(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(source / "new.txt", "hello")
    dest.mkdir()

    new_files, modified_files, deleted_files = syncdirs.compare_dirs(
        str(source), str(dest)
    )

    assert sorted(new_files) == ["new.txt"]
    assert sorted(modified_files) == []
    assert sorted(deleted_files) == []


def test_compare_dirs_identifies_modified_file(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(source / "shared.txt", "version two")
    _write(dest / "shared.txt", "version one")

    new_files, modified_files, deleted_files = syncdirs.compare_dirs(
        str(source), str(dest)
    )

    assert sorted(new_files) == []
    assert sorted(modified_files) == ["shared.txt"]
    assert sorted(deleted_files) == []


def test_compare_dirs_identifies_deleted_file(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    _write(dest / "gone.txt", "still here in dest only")

    new_files, modified_files, deleted_files = syncdirs.compare_dirs(
        str(source), str(dest)
    )

    assert sorted(new_files) == []
    assert sorted(modified_files) == []
    assert sorted(deleted_files) == ["gone.txt"]


def test_compare_dirs_handles_nested_subdirectory_across_all_categories(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"

    # Unchanged file, present identically in both.
    _write(source / "keep.txt", "same")
    _write(dest / "keep.txt", "same")

    # New file, nested a couple of levels deep, only in source.
    _write(source / "nested" / "deep" / "added.txt", "brand new")

    # Modified file, nested one level deep, differing contents.
    _write(source / "nested" / "changed.txt", "new content")
    _write(dest / "nested" / "changed.txt", "old content")

    # Deleted file, nested, only in dest.
    _write(dest / "nested" / "removed.txt", "no longer in source")

    new_files, modified_files, deleted_files = syncdirs.compare_dirs(
        str(source), str(dest)
    )

    assert sorted(new_files) == ["nested/deep/added.txt"]
    assert sorted(modified_files) == ["nested/changed.txt"]
    assert sorted(deleted_files) == ["nested/removed.txt"]


def test_compare_dirs_reports_nothing_when_trees_match(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(source / "a.txt", "same content")
    _write(dest / "a.txt", "same content")
    _write(source / "sub" / "b.txt", "also same")
    _write(dest / "sub" / "b.txt", "also same")

    new_files, modified_files, deleted_files = syncdirs.compare_dirs(
        str(source), str(dest)
    )

    assert new_files == []
    assert modified_files == []
    assert deleted_files == []
