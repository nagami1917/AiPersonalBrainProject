from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["note", "document"]
WorkspaceRole = Literal["owner", "admin", "member", "viewer"]


class WorkspaceOut(BaseModel):
    id: str
    name: str
    slug: str
    role: WorkspaceRole
    owner_id: str
    personal: bool
    member_count: int = 1


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class MemberOut(BaseModel):
    user_id: str
    display_name: str | None = None
    email: str | None = None
    role: WorkspaceRole
    joined_at: datetime


class MemberInvite(BaseModel):
    email: str
    role: Literal["admin", "member", "viewer"] = "member"


class MemberRoleUpdate(BaseModel):
    role: Literal["admin", "member", "viewer"]
