
from io import BytesIO

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

from ai_service import AIServiceError, generate_try_on


app = FastAPI(
    title="VirtuFit API",
    description="Virtual Try-On Backend",
    version="1.0.0",
)

# Development configuration.
# Restrict allowed origins before deploying to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

MAX_IMAGE_SIZE = 15 * 1024 * 1024  # 15 MB per image

ALLOWED_CATEGORIES = {
    "tops",
    "bottoms",
    "one-pieces",
}


def validate_image(image_bytes: bytes, field_name: str) -> None:
    """Validate image size, format, and file contents."""

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must not be empty.",
        )

    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"{field_name} exceeds the 15 MB limit.",
        )

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} must be JPEG, PNG, or WebP.",
                )

            image.verify()

    except HTTPException:
        raise

    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} is not a valid image.",
        )


@app.get("/")
def home():
    return {
        "message": "VirtuFit API is running",
        "status": "success",
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "VirtuFit API",
    }


@app.post("/api/v1/try-on")
async def try_on(
    person_image: UploadFile = File(...),
    clothing_image: UploadFile = File(...),
    category: str = Form("tops"),
):
    """Generate a virtual try-on image using the remote AI service."""

    if person_image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Person image must be JPEG, PNG, or WebP.",
        )

    if clothing_image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Clothing image must be JPEG, PNG, or WebP.",
        )

    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unsupported clothing category.",
                "allowed_categories": sorted(ALLOWED_CATEGORIES),
            },
        )

    person_bytes = await person_image.read(MAX_IMAGE_SIZE + 1)
    clothing_bytes = await clothing_image.read(MAX_IMAGE_SIZE + 1)

    validate_image(person_bytes, "Person image")
    validate_image(clothing_bytes, "Clothing image")

    try:
        result_bytes, result_content_type = await generate_try_on(
            person_image=person_bytes,
            person_filename=person_image.filename or "person.jpg",
            person_content_type=person_image.content_type,
            clothing_image=clothing_bytes,
            clothing_filename=clothing_image.filename or "clothing.jpg",
            clothing_content_type=clothing_image.content_type,
            category=category,
        )

    except AIServiceError as exc:
        message = str(exc)

        # Missing configuration or a disconnected Colab service.
        status_code = (
            503
            if (
                "not configured" in message.lower()
                or "could not connect" in message.lower()
                or "timed out" in message.lower()
            )
            else 502
        )

        raise HTTPException(
            status_code=status_code,
            detail=message,
        ) from exc

    return Response(
        content=result_bytes,
        media_type=result_content_type,
        headers={
            "Content-Disposition": 'inline; filename="virtufit-result.png"',
            "Cache-Control": "no-store",
        },
    )