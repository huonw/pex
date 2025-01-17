# Copyright 2016 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import contextlib
import errno
import os

import pytest

from pex.common import (
    Chroot,
    PermPreservingZipFile,
    can_write_dir,
    chmod_plus_x,
    is_exe,
    is_script,
    open_zip,
    safe_open,
    temporary_dir,
    touch,
)
from pex.typing import TYPE_CHECKING

try:
    from unittest import mock
except ImportError:
    import mock  # type: ignore[no-redef,import]

if TYPE_CHECKING:
    from typing import Any, Iterator, Tuple


def extract_perms(path):
    # type: (str) -> str
    return oct(os.stat(path).st_mode)


@contextlib.contextmanager
def zip_fixture():
    # type: () -> Iterator[Tuple[str, str, str, str]]
    with temporary_dir() as target_dir:
        one = os.path.join(target_dir, "one")
        touch(one)

        two = os.path.join(target_dir, "two")
        touch(two)
        chmod_plus_x(two)

        assert extract_perms(one) != extract_perms(two)

        zip_file = os.path.join(target_dir, "test.zip")
        with contextlib.closing(PermPreservingZipFile(zip_file, "w")) as zf:
            zf.write(one, "one")
            zf.write(two, "two")

        yield zip_file, os.path.join(target_dir, "extract"), one, two


def test_perm_preserving_zipfile_extractall():
    # type: () -> None
    with zip_fixture() as (zip_file, extract_dir, one, two):
        with contextlib.closing(PermPreservingZipFile(zip_file)) as zf:
            zf.extractall(extract_dir)

            assert extract_perms(one) == extract_perms(os.path.join(extract_dir, "one"))
            assert extract_perms(two) == extract_perms(os.path.join(extract_dir, "two"))


def test_perm_preserving_zipfile_extract():
    # type: () -> None
    with zip_fixture() as (zip_file, extract_dir, one, two):
        with contextlib.closing(PermPreservingZipFile(zip_file)) as zf:
            zf.extract("one", path=extract_dir)
            zf.extract("two", path=extract_dir)

            assert extract_perms(one) == extract_perms(os.path.join(extract_dir, "one"))
            assert extract_perms(two) == extract_perms(os.path.join(extract_dir, "two"))


def assert_chroot_perms(copyfn):
    with temporary_dir() as src:
        one = os.path.join(src, "one")
        touch(one)

        two = os.path.join(src, "two")
        touch(two)
        chmod_plus_x(two)

        with temporary_dir() as dst:
            chroot = Chroot(dst)
            copyfn(chroot, one, "one")
            copyfn(chroot, two, "two")
            assert extract_perms(one) == extract_perms(os.path.join(chroot.path(), "one"))
            assert extract_perms(two) == extract_perms(os.path.join(chroot.path(), "two"))

            zip_path = os.path.join(src, "chroot.zip")
            chroot.zip(zip_path)
            with temporary_dir() as extract_dir:
                with contextlib.closing(PermPreservingZipFile(zip_path)) as zf:
                    zf.extractall(extract_dir)

                    assert extract_perms(one) == extract_perms(os.path.join(extract_dir, "one"))
                    assert extract_perms(two) == extract_perms(os.path.join(extract_dir, "two"))


def test_chroot_perms_copy():
    # type: () -> None
    assert_chroot_perms(Chroot.copy)


def test_chroot_perms_link_same_device():
    # type: () -> None
    assert_chroot_perms(Chroot.link)


def test_chroot_perms_link_cross_device():
    # type: () -> None
    with mock.patch("os.link", spec_set=True, autospec=True) as mock_link:
        expected_errno = errno.EXDEV
        mock_link.side_effect = OSError(expected_errno, os.strerror(expected_errno))

        assert_chroot_perms(Chroot.link)


def test_chroot_zip():
    # type: () -> None
    with temporary_dir() as tmp:
        chroot = Chroot(os.path.join(tmp, "chroot"))
        chroot.write(b"data", "directory/subdirectory/file")
        zip_dst = os.path.join(tmp, "chroot.zip")
        chroot.zip(zip_dst)
        with open_zip(zip_dst) as zip:
            assert [
                "directory/",
                "directory/subdirectory/",
                "directory/subdirectory/file",
            ] == sorted(zip.namelist())
            assert b"" == zip.read("directory/")
            assert b"" == zip.read("directory/subdirectory/")
            assert b"data" == zip.read("directory/subdirectory/file")


def test_chroot_zip_symlink():
    # type: () -> None
    with temporary_dir() as tmp:
        chroot = Chroot(os.path.join(tmp, "chroot"))
        chroot.write(b"data", "directory/subdirectory/file")
        chroot.write(b"data", "directory/subdirectory/file.foo")
        chroot.symlink(
            os.path.join(chroot.path(), "directory/subdirectory/file"),
            "directory/subdirectory/symlinked",
        )

        cwd = os.getcwd()
        try:
            os.chdir(os.path.join(chroot.path(), "directory/subdirectory"))
            chroot.symlink(
                "file",
                "directory/subdirectory/rel-symlinked",
            )
        finally:
            os.chdir(cwd)

        chroot.symlink(os.path.join(chroot.path(), "directory"), "symlinked")
        zip_dst = os.path.join(tmp, "chroot.zip")
        chroot.zip(zip_dst, exclude_file=lambda path: path.endswith(".foo"))
        with open_zip(zip_dst) as zip:
            assert [
                "directory/",
                "directory/subdirectory/",
                "directory/subdirectory/file",
                "directory/subdirectory/rel-symlinked",
                "directory/subdirectory/symlinked",
                "symlinked/",
                "symlinked/subdirectory/",
                "symlinked/subdirectory/file",
                "symlinked/subdirectory/rel-symlinked",
                "symlinked/subdirectory/symlinked",
            ] == sorted(zip.namelist())
            assert b"" == zip.read("directory/")
            assert b"" == zip.read("directory/subdirectory/")
            assert b"data" == zip.read("directory/subdirectory/file")
            assert b"data" == zip.read("directory/subdirectory/rel-symlinked")
            assert b"data" == zip.read("directory/subdirectory/symlinked")
            assert b"" == zip.read("symlinked/")
            assert b"" == zip.read("symlinked/subdirectory/")
            assert b"data" == zip.read("symlinked/subdirectory/file")
            assert b"data" == zip.read("symlinked/subdirectory/rel-symlinked")
            assert b"data" == zip.read("symlinked/subdirectory/symlinked")


def test_can_write_dir_writeable_perms():
    # type: () -> None
    with temporary_dir() as writeable:
        assert can_write_dir(writeable)

        path = os.path.join(writeable, "does/not/exist/yet")
        assert can_write_dir(path)
        touch(path)
        assert not can_write_dir(path), "Should not be able to write to a file."


def test_can_write_dir_unwriteable_perms():
    # type: () -> None
    with temporary_dir() as writeable:
        no_perms_path = os.path.join(writeable, "no_perms")
        os.mkdir(no_perms_path, 0o444)
        assert not can_write_dir(no_perms_path)

        path_that_does_not_exist_yet = os.path.join(no_perms_path, "does/not/exist/yet")
        assert not can_write_dir(path_that_does_not_exist_yet)

        os.chmod(no_perms_path, 0o744)
        assert can_write_dir(no_perms_path)
        assert can_write_dir(path_that_does_not_exist_yet)


@pytest.fixture
def temporary_working_dir():
    # type: () -> Iterator[str]
    cwd = os.getcwd()
    try:
        with temporary_dir() as td:
            os.chdir(td)
            yield td
    finally:
        os.chdir(cwd)


def test_safe_open_abs(temporary_working_dir):
    # type: (str) -> None
    abs_path = os.path.join(temporary_working_dir, "path")
    with safe_open(abs_path, "w") as fp:
        fp.write("contents")

    with open(abs_path) as fp:
        assert "contents" == fp.read()


def test_safe_open_relative(temporary_working_dir):
    # type: (str) -> None
    rel_path = "rel_path"
    with safe_open(rel_path, "w") as fp:
        fp.write("contents")

    abs_path = os.path.join(temporary_working_dir, rel_path)
    with open(abs_path) as fp:
        assert "contents" == fp.read()


def test_is_exe(tmpdir):
    # type: (Any) -> None
    all_exe = os.path.join(str(tmpdir), "all_exe")
    touch(all_exe)
    chmod_plus_x(all_exe)
    assert is_exe(all_exe)

    other_exe = os.path.join(str(tmpdir), "other_exe")
    touch(other_exe)
    os.chmod(other_exe, 0o665)
    assert not is_exe(other_exe)

    not_exe = os.path.join(str(tmpdir), "not_exe")
    touch(not_exe)
    assert not is_exe(not_exe)

    exe_dir = os.path.join(str(tmpdir), "exe_dir")
    os.mkdir(exe_dir)
    chmod_plus_x(exe_dir)
    assert not is_exe(exe_dir)


def test_is_script(tmpdir):
    # type: (Any) -> None
    exe = os.path.join(str(tmpdir), "exe")

    touch(exe)
    assert not is_exe(exe)
    assert not is_script(exe)

    chmod_plus_x(exe)
    assert is_exe(exe)
    assert not is_script(exe)

    with open(exe, "wb") as fp:
        fp.write(bytearray([0xCA, 0xFE, 0xBA, 0xBE]))
    assert not is_script(fp.name)

    with open(exe, "wb") as fp:
        fp.write(b"#!/mystery\n")
        fp.write(bytearray([0xCA, 0xFE, 0xBA, 0xBE]))
    assert is_script(exe)
    assert is_script(exe, pattern=r"^/mystery")
    assert not is_script(exe, pattern=r"^python")

    os.chmod(exe, 0o665)
    assert is_script(exe, check_executable=False)
    assert not is_script(exe)
    assert not is_exe(exe)
