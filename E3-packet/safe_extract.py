"""Reviewed pnpm-tar extraction only; no project imports or general tar extraction."""
import io
import json
import pathlib
import sys
import tarfile


def extract(archive, destination):
    root = pathlib.Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        assert 0 < len(members) <= 10000
        assert sum(member.size for member in members) <= 100 * 1024**2
        for member in members:
            name = pathlib.PurePosixPath(member.name)
            assert not name.is_absolute() and ".." not in name.parts
            assert name.parts and name.parts[0] == "package"
            assert member.isfile() or member.isdir()
        for member in members:
            target = root / pathlib.PurePosixPath(member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as src, target.open("xb") as dst:
                    while True:
                        data = src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                target.chmod(member.mode & 0o777)
    assert (root / "package/bin/pnpm.cjs").is_file()


def fixture():
    root = pathlib.Path("/work/fixture/extract")
    root.mkdir(parents=True, exist_ok=False)
    for name, member, valid in [("good", "package/bin/pnpm.cjs", True), ("escape", "../escape", False)]:
        archive = root / (name + ".tgz")
        with tarfile.open(archive, "w:gz") as target:
            data = b"fixture"
            entry = tarfile.TarInfo(member)
            entry.size = len(data)
            target.addfile(entry, io.BytesIO(data))
        try:
            extract(archive, root / name)
        except AssertionError:
            assert not valid
        else:
            assert valid
    print(json.dumps({"extraction": True, "unsafePathRejected": True}))


if __name__ == "__main__":
    if sys.argv[1] == "fixture":
        fixture()
    else:
        extract(sys.argv[1], sys.argv[2])
