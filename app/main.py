import os

import fastapi
import vertexai
from fastapi import Depends, File, Form, UploadFile
from fastapi.responses import RedirectResponse
from file_process import delete_gcs_file, process_webm_file
from gemini_process import process_agenda, process_suggest_actions, process_transcript
from logging_config import setup_logger
from models import (
    AgendaModel,
    SuggestActionModel,
    TemplateAction,
    TemplateActionsModel,
    TranscriptionModel,
)

PROJECT_ID = os.environ.get("PROJECT_ID")
LOCATION = "us-central1"
BUCKET_NAME = "audio-playground"

app = fastapi.FastAPI()
logger = setup_logger(__name__)
vertexai.init(project=PROJECT_ID, location=LOCATION)


async def process_audio_files(
    host_audio: UploadFile,
    meet_audio: UploadFile,
) -> TranscriptionModel:
    """Process audio files and return transcription"""
    gcs_file = await process_webm_file(host_audio, meet_audio, BUCKET_NAME)
    logger.info(f"success to process audio files: {gcs_file.gcs_path}")
    try:
        transcription = process_transcript(gcs_file.gcs_path)
        delete_gcs_file(gcs_file)
        return transcription
    except Exception as e:
        delete_gcs_file(gcs_file)
        raise fastapi.HTTPException(500, detail=str(e))


def validate_agenda(json_data: str = Form(...)) -> AgendaModel:
    try:
        agenda = AgendaModel.model_validate_json(json_data)
        return agenda
    except Exception as e:
        raise fastapi.HTTPException(500, detail=str(e))


@app.get("/", include_in_schema=False)
def redirect_to_docs():
    return RedirectResponse(url="/docs")


@app.post("/transcript", response_model=TranscriptionModel)
async def transcript(
    host_audio: UploadFile = File(..., media_type="audio/webm"),
    meet_audio: UploadFile = File(..., media_type="audio/webm"),
):
    return await process_audio_files(host_audio, meet_audio)


@app.post("/agenda", response_model=AgendaModel)
async def agenda(
    host_audio: UploadFile = File(..., media_type="audio/webm"),
    meet_audio: UploadFile = File(..., media_type="audio/webm"),
    agenda: AgendaModel = Depends(validate_agenda),
):
    transcription = await process_audio_files(host_audio, meet_audio)
    logger.info("success to get transcription")
    try:
        return process_agenda(transcription, agenda)
    except Exception as e:
        logger.error(f"failed to process agenda: {e}")
        raise fastapi.HTTPException(500, detail=str(e))


@app.post("/check_agenda", response_model=AgendaModel)
async def check_agenda(
    agenda: AgendaModel = Depends(validate_agenda),
):
    return agenda


@app.get("/actions", response_model=TemplateActionsModel)
def actions():
    return TemplateActionsModel.resolve()


@app.post("/suggest_actions", response_model=SuggestActionModel)
async def suggest_actions(
    template_action: TemplateAction,
    agenda: AgendaModel = Depends(validate_agenda),
):
    return process_suggest_actions(template_action, agenda)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
