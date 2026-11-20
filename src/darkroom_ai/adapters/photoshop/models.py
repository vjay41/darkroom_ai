"""Typed results returned by the Photoshop adapter."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class PhotoshopInfo(_Result):
    name: str
    version: str
    documents: int


class DocumentInfo(_Result):
    name: str
    width: int
    height: int
    bits: int
    mode: str
    layers: int


class ActionManagerInfo(_Result):
    title: str
    document_id: int
    number_of_layers: int
    has_background_layer: bool


class LayerInfo(_Result):
    name: str
    has_mask: bool
    is_background: bool
