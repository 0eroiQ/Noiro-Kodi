from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Profile:
    id: str
    name: str
    avatar: str = "default"
    pin_hash: Optional[str] = None
    target_language: str = "hr"
    auto_translate: bool = False
    email: Optional[str] = None
    created_at: str = ""

    def public_dict(self):
        value = asdict(self)
        value["locked"] = bool(value.pop("pin_hash"))
        return value


@dataclass
class LinkSession:
    code: str
    link: str
    qrcode: str
    expires_at: float
    failures: int = 0


@dataclass
class StreamCandidate:
    id: str
    title: str
    url: Optional[str] = None
    info_hash: Optional[str] = None
    file_idx: Optional[int] = None
    addon_name: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    behavior_hints: Dict[str, Any] = field(default_factory=dict)

    @property
    def locked(self):
        return not bool(self.url) and bool(self.info_hash)

    def to_dict(self):
        value = asdict(self)
        value["locked"] = self.locked
        value["playable"] = bool(self.url)
        return value


@dataclass
class ReleaseArtifact:
    name: str
    sha256: str
    size: int
    asset_id: Optional[int] = None
