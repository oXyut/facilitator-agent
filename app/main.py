import asyncio
import os

import fastapi
import vertexai
from dotenv import load_dotenv
from fastapi import Depends, File, Form, Header, UploadFile
from fastapi.responses import RedirectResponse

from app.src.file_process import delete_gcs_file, process_webm_file
from app.src.gemini_process import (
    process_agenda_by_item,
    process_hand_over,
    process_suggest_actions,
    process_transcript,
)
from app.src.logging_config import setup_logger
from app.src.models import (
    AgendaModel,
    SuggestActionModel,
    TemplateAction,
    TemplateActionsModel,
    TranscriptionModel,
)

load_dotenv()

PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = "us-central1"
BUCKET_NAME = os.getenv("BUCKET_NAME")
EXTENSION_ID = os.getenv("EXTENSION_ID")
# EXPECTED_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
API_KEY = os.getenv("API_KEY")

IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None

app = fastapi.FastAPI()
logger = setup_logger(__name__)
vertexai.init(project=PROJECT_ID, location=LOCATION)


# async def check_origin(request: Request):
#     if IS_CLOUD_RUN:
#         if request.headers.get("origin") != EXPECTED_ORIGIN:
#             raise fastapi.HTTPException(403, detail="Forbidden")


async def check_key(key: str = Header(...)):
    # headerにkeyが含まれているかどうかを確認する
    if key != API_KEY:
        raise fastapi.HTTPException(401, detail="Unauthorized")


async def process_audio_files(
    host_audio: UploadFile,
    meet_audio: UploadFile,
) -> TranscriptionModel:
    """
    音声ファイルを処理してトランスクリプションを返す。

    Args:
        host_audio (UploadFile): ホストの音声ファイル。
        meet_audio (UploadFile): ミーティング参加者の音声ファイル。

    Returns:
        TranscriptionModel: トランスクリプション結果。

    Raises:
        HTTPException: 音声ファイルの処理に失敗した場合。
    """
    gcs_file = await process_webm_file(host_audio, meet_audio, BUCKET_NAME)
    logger.info(f"success to process audio files: {gcs_file.gcs_path}")
    try:
        transcription = process_transcript(gcs_file.gcs_path)
        logger.info(f"success to process transcript: {transcription}")
        delete_gcs_file(gcs_file)
        return transcription
    except Exception as e:
        delete_gcs_file(gcs_file)
        raise fastapi.HTTPException(500, detail=str(e))


def validate_agenda(json_data: str = Form(...)) -> AgendaModel:
    """
    JSON形式のデータをAgendaModelとして検証する。

    Args:
        json_data (str): JSON形式の文字列。

    Returns:
        AgendaModel: 検証済みのAgendaModelインスタンス。

    Raises:
        HTTPException: JSONの検証に失敗した場合。
    """
    try:
        agenda = AgendaModel.model_validate_json(json_data)
        return agenda
    except Exception as e:
        raise fastapi.HTTPException(500, detail=str(e))


@app.get("/", include_in_schema=False)
def redirect_to_docs():
    """
    ルートURLへのアクセスをドキュメントページへリダイレクトする。

    Returns:
        RedirectResponse: ドキュメントページへのリダイレクトレスポンス。
    """
    return RedirectResponse(url="/docs")


@app.post(
    "/transcript",
    response_model=TranscriptionModel,
    dependencies=[Depends(check_key)],
)
async def transcript(
    host_audio: UploadFile = File(..., media_type="audio/webm"),
    meet_audio: UploadFile = File(..., media_type="audio/webm"),
):
    """
    音声ファイルからトランスクリプトを生成する。

    Args:
        host_audio (UploadFile): ホストの音声ファイル。
        meet_audio (UploadFile): ミーティング参加者の音声ファイル。

    Returns:
        TranscriptionModel: 生成されたトランスクリプト。
    """
    return await process_audio_files(host_audio, meet_audio)


@app.post("/agenda", response_model=AgendaModel, dependencies=[Depends(check_key)])
async def agenda(
    host_audio: UploadFile = File(..., media_type="audio/webm"),
    meet_audio: UploadFile = File(..., media_type="audio/webm"),
    agenda: AgendaModel = Depends(validate_agenda),
):
    """
    音声ファイルとアジェンダに基づいて議事録を生成する。

    Args:
        host_audio (UploadFile): ホストの音声ファイル。
        meet_audio (UploadFile): ミーティング参加者の音声ファイル。
        agenda (AgendaModel): 会議のアジェンダ。

    Returns:
        AgendaModel: 更新されたアジェンダと議事録。

    Raises:
        HTTPException: 議事録の生成に失敗した場合。
    """
    transcription = await process_audio_files(host_audio, meet_audio)
    logger.info("success to get transcription")
    try:
        # 並列処理を実装
        tasks = [process_agenda_by_item(transcription, item) for item in agenda.items]
        items = await asyncio.gather(*tasks)
        new_agenda = AgendaModel(items=items).resolve_status()
        hand_over = process_hand_over(transcription, agenda, new_agenda)
        new_agenda.hand_over = hand_over.hand_over
        return new_agenda
    except Exception as e:
        logger.error(f"failed to process agenda: {e}")
        raise fastapi.HTTPException(500, detail=str(e))


@app.post(
    "/check_agenda", response_model=AgendaModel, dependencies=[Depends(check_key)]
)
async def check_agenda(
    agenda: AgendaModel = Depends(validate_agenda),
):
    """
    アジェンダの検証を行う。

    Args:
        agenda (AgendaModel): 検証するアジェンダ。

    Returns:
        AgendaModel: 検証されたアジェンダ。
    """
    return agenda


@app.get(
    "/actions",
    response_model=TemplateActionsModel,
    dependencies=[Depends(check_key)],
)
def actions():
    """
    利用可能なアクションテンプレートを取得する。

    Returns:
        TemplateActionsModel: アクションテンプレートのリスト。
    """
    return TemplateActionsModel.resolve()


@app.post(
    "/suggest_actions",
    response_model=SuggestActionModel,
    dependencies=[Depends(check_key)],
)
async def suggest_actions(
    template_action: TemplateAction,
    agenda: AgendaModel = Depends(validate_agenda),
):
    """
    アジェンダに基づいてアクションを提案する。

    Args:
        template_action (TemplateAction): 使用するアクションテンプレート。
        agenda (AgendaModel): 会議のアジェンダ。

    Returns:
        SuggestActionModel: 提案されたアクション。
    """
    return process_suggest_actions(template_action, agenda)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
