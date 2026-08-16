from __future__ import annotations

from pathlib import Path

import pytest

from chatgpt_mcp.apply_batch import apply_change_list


def _symlink_dir(src: Path, dst: Path) -> None:
    try:
        dst.symlink_to(src, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink not permitted: {exc}")


@pytest.mark.parametrize("shared", [".venv", "node_modules"])
def test_apply_change_list_rejects_shared_symlink_write(tmp_path: Path, shared: str) -> None:
    jail = tmp_path / "main"
    write = tmp_path / "wt"
    jail.mkdir()
    write.mkdir()
    (jail / shared).mkdir()
    (jail / shared / "keep.txt").write_text("keep\n", encoding="utf-8")
    _symlink_dir(jail / shared, write / shared)

    result = apply_change_list(
        [{"op": "write", "path": f"{shared}/foo", "content": "pwned\n"}],
        jail_root=jail,
        write_root=write,
    )

    assert result.is_error
    assert "escapes write root" in result.output
    assert not (jail / shared / "foo").exists()
    assert (jail / shared / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_apply_change_list_write_stays_in_write_root(tmp_path: Path) -> None:
    jail = tmp_path / "main"
    write = tmp_path / "wt"
    jail.mkdir()
    write.mkdir()
    (jail / "hello.txt").write_text("hello\n", encoding="utf-8")

    result = apply_change_list(
        [{"op": "write", "path": "hello.txt", "content": "world\n"}],
        jail_root=jail,
        write_root=write,
    )

    assert not result.is_error
    assert (write / "hello.txt").read_text(encoding="utf-8") == "world\n"
    assert (jail / "hello.txt").read_text(encoding="utf-8") == "hello\n"
