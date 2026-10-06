"""Copying many files into a Docker sandbox is one archive, not one call per file.

The 0.5.0 known issue: `upload_files` wrote each file with its own `mkdir` and
`put_archive` (about 0.3 s each), so 200 files took a minute. These tests use a
fake container, so they run where Docker does not.
"""

from __future__ import annotations

import io
import tarfile
from types import SimpleNamespace

import pytest

pytest.importorskip("docker", reason="the Docker SDK is not installed")

from omnicoreagent.sandbox import SandboxManifest  # noqa: E402
from omnicoreagent.sandbox.docker import DockerSandboxRuntime  # noqa: E402


class _FakeContainer:
    def __init__(self):
        self.execs: list[list[str]] = []
        self.archives: list[tuple[str, bytes]] = []

    def exec_run(self, command, **_):
        self.execs.append(list(command))
        return SimpleNamespace(exit_code=0, output=b"")

    def put_archive(self, path, data):
        self.archives.append((path, data))
        return True


def _runtime():
    runtime = DockerSandboxRuntime(options={})
    container = _FakeContainer()
    manifest = SandboxManifest()
    runtime._sessions["s"] = SimpleNamespace(manifest=manifest)
    runtime._containers["s"] = container
    return runtime, container, manifest.working_dir


@pytest.mark.asyncio
async def test_many_files_are_one_archive_with_their_paths_and_modes():
    runtime, container, workdir = _runtime()
    files = {f"d{i % 3}/f{i}.txt": f"body {i}".encode() for i in range(50)}

    await runtime.upload_files("s", files)

    assert len(container.archives) == 1 and len(container.execs) == 1
    extracted_at, data = container.archives[0]
    assert extracted_at == workdir
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == set(files)
        for path, content in files.items():
            member = members[path]
            assert member.mode == 0o644 and (member.uid, member.gid) == (runtime.uid, runtime.gid)
            assert tar.extractfile(member).read() == content


@pytest.mark.asyncio
async def test_one_path_outside_the_working_directory_writes_nothing():
    runtime, container, _ = _runtime()

    with pytest.raises(PermissionError):
        await runtime.upload_files("s", {"ok.txt": b"x", "../../etc/passwd": b"y"})

    assert container.archives == [] and container.execs == []


@pytest.mark.asyncio
async def test_a_refused_archive_is_an_error():
    runtime, container, _ = _runtime()
    container.put_archive = lambda path, data: False

    with pytest.raises(OSError):
        await runtime.upload_files("s", {"a.txt": b"x"})


@pytest.mark.asyncio
async def test_no_files_make_no_calls():
    runtime, container, _ = _runtime()

    await runtime.upload_files("s", {})

    assert container.archives == [] and container.execs == []
