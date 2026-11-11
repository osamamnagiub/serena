import os
import shutil
from pathlib import Path

import pytest

from solidlsp.language_servers.csharp_language_server import find_nearest_ancestor_dir, find_project_files_under
from solidlsp.ls_config import Language
from test.conftest import get_repo_path, start_ls_context

_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Library</OutputType>
    <TargetFramework>net10.0</TargetFramework>
  </PropertyGroup>
</Project>
"""

_SLNX = """<Solution>
  <Project Path="Wt.csproj" />
</Solution>
"""


def _add_worktree(repo_root: Path) -> Path:
    worktree = repo_root / ".worktrees" / "wt"
    worktree.mkdir(parents=True)
    (worktree / "Wt.slnx").write_text(_SLNX)
    (worktree / "Wt.csproj").write_text(_CSPROJ)
    (worktree / "WorktreeOnly.cs").write_text("public class WorktreeOnly\n{\n    public int Compute() => 42;\n}\n")
    (worktree / "WorktreeUser.cs").write_text("public class WorktreeUser\n{\n    public int Use() => new WorktreeOnly().Compute();\n}\n")
    return worktree


def test_find_nearest_ancestor_dir_stops_below_root(tmp_path: Path) -> None:
    root = str(tmp_path)
    worktree = _add_worktree(tmp_path)
    (tmp_path / "Root.sln").write_text("")
    file_path = str(worktree / "WorktreeOnly.cs")

    assert find_nearest_ancestor_dir(file_path, root, (".sln", ".slnx"), include_root=False) == str(worktree)
    assert find_nearest_ancestor_dir(str(tmp_path / "Loose.cs"), root, (".sln", ".slnx"), include_root=False) is None
    assert find_nearest_ancestor_dir(str(tmp_path / "Loose.cs"), root, (".sln", ".slnx"), include_root=True) == root


def test_find_project_files_under_skips_dot_and_build_dirs(tmp_path: Path) -> None:
    _add_worktree(tmp_path)
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "A.csproj").write_text(_CSPROJ)
    (tmp_path / "A" / "obj").mkdir()
    (tmp_path / "A" / "obj" / "Gen.csproj").write_text(_CSPROJ)

    assert find_project_files_under(str(tmp_path)) == [str(tmp_path / "A" / "A.csproj")]
    assert find_project_files_under(str(tmp_path / ".worktrees" / "wt")) == [str(tmp_path / ".worktrees" / "wt" / "Wt.csproj")]


@pytest.mark.csharp
def test_references_resolve_inside_nested_worktree_solution(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    shutil.copytree(get_repo_path(Language.CSHARP), repo_root, ignore=shutil.ignore_patterns("bin", "obj"))
    _add_worktree(repo_root)

    with start_ls_context(Language.CSHARP, repo_path=str(repo_root), solidlsp_dir=tmp_path / "solidlsp") as ls:
        worktree_file = os.path.join(".worktrees", "wt", "WorktreeOnly.cs")
        # line 2 = "    public int Compute() => 42;", column 15 = "Compute"
        references = ls.request_references(worktree_file, 2, 15)

        ref_paths = {os.path.basename(ref["absolutePath"]) for ref in references}
        assert "WorktreeUser.cs" in ref_paths, f"expected a reference from the worktree project, got {references}"


@pytest.mark.csharp
def test_worktree_copy_with_identical_project_names_stays_separate(tmp_path: Path) -> None:
    """A worktree holding a copy of the root repo (same project/assembly names) resolves within itself."""
    repo_root = tmp_path / "repo"
    shutil.copytree(get_repo_path(Language.CSHARP), repo_root, ignore=shutil.ignore_patterns("bin", "obj"))
    worktree = repo_root / ".worktrees" / "copy"
    shutil.copytree(repo_root, worktree, ignore=shutil.ignore_patterns("bin", "obj", ".worktrees"))

    with start_ls_context(Language.CSHARP, repo_path=str(repo_root), solidlsp_dir=tmp_path / "solidlsp") as ls:
        worktree_program = os.path.join(".worktrees", "copy", "Program.cs")
        # line 22 = "        public int Add(int a, int b)", column 20 = "Add"
        references = ls.request_references(worktree_program, 22, 20)
        assert references, "expected references inside the worktree copy"
        assert all(".worktrees" in ref["absolutePath"] for ref in references), references

        main_references = ls.request_references("Program.cs", 22, 20)
        assert main_references, "expected references in the root repo"
        assert not any(".worktrees" in ref["absolutePath"] for ref in main_references), main_references

        # second call takes the already-loaded fast path
        assert ls.request_references(worktree_program, 22, 20)
