from dataclasses import dataclass

import pytest

from shared.errors import ArtifactNotFoundError, FileTooLargeError


@dataclass
class FileMeta:
    artifact_id: str = "art_1"
    task_id: str = "t_1"
    sha256: str = "abc"
    mime: str = "image/png"
    size: int = 1024
    local_path: str = "/tmp/x.png"


class FakeLarkCLI:
    def __init__(self):
        self.uploads = []

    def drive_upload(self, path, *, parent_node_token, mime):
        self.uploads.append((path, mime))
        return {
            "file_token": "boxcn_xxx",
            "drive_url": "https://drive.example.com/boxcn_xxx",
        }


@dataclass
class _FakeArtifact:
    artifact_id: str = "art_1"
    size_bytes: int = 1024
    sha256: str = "abc"
    file_token: str = None
    storage_type: str = "pending"
    drive_url: str = ""


class FakeArtifactRepo:
    def __init__(self):
        self.artifacts = {"art_1": _FakeArtifact()}

    def get(self, aid):
        if aid not in self.artifacts:
            raise ArtifactNotFoundError(f"artifact {aid} not found")
        return self.artifacts[aid]


def test_upload_rejects_too_large():
    from feishu_adapter.drive_adapter import DriveAdapter

    cli = FakeLarkCLI()
    repo = FakeArtifactRepo()
    ad = DriveAdapter(
        parent_node_token="parent", lark_cli=cli, artifact_repo=repo, max_size_mb=500
    )
    meta = FileMeta(size=600 * 1024 * 1024)
    with pytest.raises(FileTooLargeError):
        ad.upload(meta)


def test_upload_returns_drive_token():
    from feishu_adapter.drive_adapter import DriveAdapter

    cli = FakeLarkCLI()
    repo = FakeArtifactRepo()
    ad = DriveAdapter(
        parent_node_token="parent", lark_cli=cli, artifact_repo=repo, max_size_mb=500
    )
    result = ad.upload(FileMeta())
    assert result.file_token == "boxcn_xxx"
    assert result.drive_url.startswith("https://")


def test_upload_idempotent_on_repeat_artifact_id():
    from feishu_adapter.drive_adapter import DriveAdapter

    cli = FakeLarkCLI()
    repo = FakeArtifactRepo()
    repo.artifacts["art_1"].file_token = "cached_token"
    repo.artifacts["art_1"].storage_type = "drive"
    ad = DriveAdapter(
        parent_node_token="parent", lark_cli=cli, artifact_repo=repo, max_size_mb=500
    )
    result = ad.upload(FileMeta())
    assert result.file_token == "cached_token"
    assert len(cli.uploads) == 0