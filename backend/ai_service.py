import asyncio
import os

import httpx


class AIServiceError(Exception):
    """Raised when the AI service cannot process a try-on request."""


def get_ai_service_url() -> str:
    """
    Read the remote AI endpoint URL from an environment variable.

    Example:
    https://your-public-host/try-on
    """
    url = os.getenv("VIRTUFIT_AI_URL", "").strip()

    if not url:
        raise AIServiceError(
            "AI service URL is not configured. "
            "Set the VIRTUFIT_AI_URL environment variable."
        )

    if not url.startswith(("http://", "https://")):
        raise AIServiceError(
            "VIRTUFIT_AI_URL must start with http:// or https://."
        )

    return url.rstrip("/")


async def generate_try_on(
    person_image: bytes,
    person_filename: str,
    person_content_type: str,
    clothing_image: bytes,
    clothing_filename: str,
    clothing_content_type: str,
    category: str = "tops",
) -> tuple[bytes, str]:
    """
    Send images to the remote AI service.

    Workflow:
    1. Upload the images and receive a job_id.
    2. Poll the job status until it completes or fails.
    3. Download and return the generated image.
    """

    url = get_ai_service_url()

    files = {
        "person_image": (
            person_filename,
            person_image,
            person_content_type,
        ),
        "clothing_image": (
            clothing_filename,
            clothing_image,
            clothing_content_type,
        ),
    }

    data = {"category": category}

    timeout = httpx.Timeout(
        connect=30.0,
        read=60.0,
        write=120.0,
        pool=30.0,
    )

    # Maximum time allowed for the entire try-on job.
    max_wait_seconds = 900

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        ) as client:

            # Step 1: Submit the try-on job.
            response = await client.post(
                url,
                files=files,
                data=data,
            )

            if response.is_error:
                raise AIServiceError(
                    f"Remote AI service returned HTTP "
                    f"{response.status_code}: {response.text[:500]}"
                )

            try:
                job_data = response.json()
                job_id = job_data["job_id"]
            except (ValueError, KeyError, TypeError) as exc:
                raise AIServiceError(
                    "The AI service returned an invalid job response."
                ) from exc

            if not isinstance(job_id, str) or not job_id.strip():
                raise AIServiceError(
                    "The AI service returned an invalid job ID."
                )

            # Step 2: Prepare job status and result URLs.
            base_url = url.removesuffix("/try-on")

            status_url = f"{base_url}/jobs/{job_id}"
            result_url = f"{status_url}/result"

            start_time = asyncio.get_running_loop().time()

            while True:
                elapsed = (
                    asyncio.get_running_loop().time() - start_time
                )

                if elapsed >= max_wait_seconds:
                    raise AIServiceError(
                        "Try-on generation exceeded the 15-minute "
                        "time limit. Check the Colab runtime and "
                        "the AI model."
                    )

                # Give the model time to process the images.
                await asyncio.sleep(3)

                status_response = await client.get(status_url)

                if status_response.is_error:
                    raise AIServiceError(
                        f"Could not check AI job status. "
                        f"HTTP {status_response.status_code}: "
                        f"{status_response.text[:300]}"
                    )

                try:
                    status_data = status_response.json()
                except ValueError as exc:
                    raise AIServiceError(
                        "The AI service returned an invalid "
                        "job status response."
                    ) from exc

                status = status_data.get("status")

                # The model reported a processing failure.
                if status == "failed":
                    error_message = status_data.get("error")

                    raise AIServiceError(
                        "Try-on generation failed: "
                        f"{error_message or 'Unknown model error.'}"
                    )

                # The generated image is ready.
                if status == "completed":
                    image_response = await client.get(result_url)

                    if image_response.is_error:
                        raise AIServiceError(
                            f"Could not download the generated image. "
                            f"HTTP {image_response.status_code}: "
                            f"{image_response.text[:300]}"
                        )

                    content_type = (
                        image_response.headers.get(
                            "content-type", ""
                        )
                        .split(";")[0]
                        .strip()
                        .lower()
                    )

                    if not content_type.startswith("image/"):
                        raise AIServiceError(
                            "The AI service did not return an image. "
                            f"Received content type: "
                            f"{content_type or 'unknown'}."
                        )

                    if not image_response.content:
                        raise AIServiceError(
                            "The AI service returned an empty image."
                        )

                    return image_response.content, content_type

                # Continue polling while the job is in progress.
                if status not in ("queued", "running"):
                    raise AIServiceError(
                        "The AI service returned an unexpected "
                        f"job status: {status!r}."
                    )

    except httpx.TimeoutException as exc:
        raise AIServiceError(
            "A request to the AI service timed out. Check that "
            "Colab and the ngrok tunnel are running."
        ) from exc

    except httpx.RequestError as exc:
        raise AIServiceError(
            "Could not connect to the AI service. Check the public "
            "URL and make sure the Colab service is running."
        ) from exc

