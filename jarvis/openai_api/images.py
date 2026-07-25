"""Image generation endpoint — OpenAI-compatible stub.

Jarvis does not support image generation.  This stub returns a 400
error indicating the model is not available, matching the OpenAI
error format for non-existent models.
"""
import uuid
from datetime import datetime, UTC

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ImageGenerationRequest(BaseModel):
    model: str
    prompt: str
    n: int = 1
    quality: str = "standard"
    response_format: str = "url"
    size: str = "1024x1024"
    style: str = "vivid"
    user: str | None = None


class ImageEditRequest(BaseModel):
    model: str
    image: str
    prompt: str
    mask: str | None = None
    n: int = 1
    response_format: str = "url"
    size: str = "1024x1024"
    user: str | None = None


class ImageVariationRequest(BaseModel):
    model: str
    image: str
    n: int = 1
    response_format: str = "url"
    size: str = "1024x1024"
    user: str | None = None


@router.post("/v1/images/generations")
async def create_image_generation(body: ImageGenerationRequest):
    raise HTTPException(400, {
        "error": {
            "message": f"Model '{body.model}' is not available for image generation. "
                       "Jarvis does not support image generation models.",
            "type": "invalid_request_error",
            "code": "model_not_available",
        }
    })


@router.post("/v1/images/edits")
async def create_image_edit(body: ImageEditRequest):
    raise HTTPException(400, {
        "error": {
            "message": f"Model '{body.model}' is not available for image editing. "
                       "Jarvis does not support image generation models.",
            "type": "invalid_request_error",
            "code": "model_not_available",
        }
    })


@router.post("/v1/images/variations")
async def create_image_variation(body: ImageVariationRequest):
    raise HTTPException(400, {
        "error": {
            "message": f"Model '{body.model}' is not available for image variations. "
                       "Jarvis does not support image generation models.",
            "type": "invalid_request_error",
            "code": "model_not_available",
        }
    })
