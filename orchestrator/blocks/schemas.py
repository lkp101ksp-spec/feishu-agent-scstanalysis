"""Phase 5 富文本块 schema：6 类 Block Pydantic 模型。"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=3)
    text: str


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class CodeBlock(BaseModel):
    type: Literal["code"] = "code"
    language: str = "plain"
    text: str


class QuoteBlock(BaseModel):
    type: Literal["quote"] = "quote"
    text: str


class QuoteContainerBlock(BaseModel):
    """Phase 5 飞书 doc quote_container 结构（内部嵌套 text）。"""
    type: Literal["quote_container"] = "quote_container"
    text: str


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    headers: list[str]
    rows: list[list[str]]

    @field_validator("rows")
    @classmethod
    def check_rows_columns(cls, v, info):
        headers = info.data.get("headers", [])
        if not headers:
            return v
        col_count = len(headers)
        for i, row in enumerate(v):
            if len(row) != col_count:
                raise ValueError(
                    f"row {i} has {len(row)} columns, expected {col_count}"
                )
        return v


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(min_length=1)


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    url: str = Field(pattern=r"^https?://")
    alt: str = ""
    width: int | None = None
    height: int | None = None


AnyBlock = Union[HeadingBlock, TextBlock, CodeBlock, QuoteBlock,
                  QuoteContainerBlock, TableBlock, ListBlock, ImageBlock]


# === Phase 6: 8 类增量 ===

class EmbedBlock(BaseModel):
    type: Literal["embed"] = "embed"
    url: str = Field(pattern=r"^https?://")
    title: str = ""
    description: str = ""


class DividerBlock(BaseModel):
    type: Literal["divider"] = "divider"


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    emoji: str
    text: str
    color: str = "blue"


class EquationBlock(BaseModel):
    type: Literal["equation"] = "equation"
    latex: str


class MathBlock(BaseModel):
    type: Literal["math"] = "math"
    latex: str
    display_mode: bool = True


class MermaidBlock(BaseModel):
    type: Literal["mermaid"] = "mermaid"
    code: str
    theme: str = "default"


class VideoBlock(BaseModel):
    type: Literal["video"] = "video"
    url: str = Field(pattern=r"^https?://")
    poster_url: str | None = None
    duration: int | None = None


class FileBlock(BaseModel):
    type: Literal["file"] = "file"
    file_token: str
    name: str
    size: int = 0