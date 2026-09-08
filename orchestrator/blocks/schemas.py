"""Phase 5 富文本块 schema：6 类 Block Pydantic 模型。"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


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
    """图片块：url（远程图）或 path（本地文件路径）二选一。

    path：本地图片（如 sc 分析图），由 DocAdapter 按官方三步流程
    插入文档（空 image block → drive 上传 parent_node=block_id →
    PATCH replace_image，Phase 20 真机 2026-09-01 验证）。
    """
    type: Literal["image"] = "image"
    url: str = ""
    path: str = ""
    alt: str = ""
    width: int | None = None
    height: int | None = None

    @model_validator(mode="after")
    def check_url_or_path(self) -> "ImageBlock":
        """url 与 path 至少一个；url 非空时必须是 http(s)。"""
        if not self.url and not self.path:
            raise ValueError("image block requires url or path")
        if self.url and not self.url.startswith(("http://", "https://")):
            raise ValueError(f"image url must be http(s): {self.url!r}")
        return self


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


# 全部块类型的判别联合（Phase 5 基础 8 类 + Phase 6 增量 8 类）；
# 定义必须在所有 Block 类之后——Union 成员运行时求值
AnyBlock = Union[HeadingBlock, TextBlock, CodeBlock, QuoteBlock,
                 QuoteContainerBlock, TableBlock, ListBlock, ImageBlock,
                 EmbedBlock, DividerBlock, CalloutBlock, EquationBlock,
                 MathBlock, MermaidBlock, VideoBlock, FileBlock]
