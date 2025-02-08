import textwrap
import time
from datetime import datetime
from typing import TypeVar

import pytz
from pydantic import BaseModel
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part

from app.src.logging_config import setup_logger
from app.src.models import (
    AgendaItemModel,
    AgendaModel,
    HandOverModel,
    SuggestActionModel,
    TemplateAction,
    TemplateActionsModel,
    TranscriptionModel,
)


class GeminiConfig:
    """Geminiモデルの設定を保持するクラス"""

    GEMINI_MODEL_NAME = "gemini-2.0-flash-001"
    TEMPERATURE = 0.5
    MAX_RETRIES = 5


logger = setup_logger(__name__)


def get_current_time(timezone: str = "Asia/Tokyo") -> str:
    """
    現在の時刻を取得する。
    デフォルトで日本時間(JST)を返す。

    Args:
        timezone (str, optional): タイムゾーン。デフォルトは"Asia/Tokyo"。

    Returns:
        str: YYYY-MM-DD HH:MM:SS形式の現在時刻。
    """
    tz = pytz.timezone(timezone)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def exponential_backoff(retries: int):
    """指数バックオフを行う"""
    time.sleep(2**retries)


T = TypeVar("T", bound=BaseModel)


def _validate(
    model: GenerativeModel,
    parts: list[Part],
    schema_cls: type[T],
    retries: int,
    max_retries: int = GeminiConfig.MAX_RETRIES,
) -> T:
    """
    Geminiモデルからのレスポンスを検証し、指定されたスキーマに変換する。

    Args:
        model (GenerativeModel): Geminiモデルのインスタンス。
        parts (list[Part]): モデルへの入力。
        schema_cls (Type[T]): レスポンスを検証するスキーマクラス。
        retries (int): 現在のリトライ回数。
        max_retries (int): 最大リトライ回数。

    Returns:
        T: 検証済みのレスポンス。

    Raises:
        Exception: 最大リトライ回数を超えてもレスポンスの検証に失敗した場合。
    """
    try:
        response = model.generate_content(parts)
        parsed_response = schema_cls.model_validate_json(response.text)
        return parsed_response
    except Exception as e:
        logger.error(f"{retries}th attempt failed: {e}")
        if retries < max_retries:
            exponential_backoff(retries)
            return _validate(model, parts, schema_cls, retries + 1, max_retries)
        raise e


def process_transcript(audio_gcs_path: str) -> TranscriptionModel:
    """
    音声ファイルからトランスクリプトを生成する。

    Args:
        audio_gcs_path (str): 音声ファイルのGCSパス。

    Returns:
        TranscriptionModel: 生成されたトランスクリプト。
    """
    system_prompt = textwrap.dedent(
        """
        # role
        あなたは優秀な文字起こし業者です。

        # task
        与えた音声から会話の内容を読み取り、話者と秒数を明確にしてトランスクリプトをJSON形式で作成してください。

        # output example
        ```
        [
        {"speaker_id": "男性1", "start_sec": 0.0, "end_sec": 5.0, "text": "今日はSDGsについて議論したいと思います。"},
        {"speaker_id": "男性2", "start_sec": 6.1, "end_sec": 8.7, "text": "よろしくお願いします"},
        ...
        ]
        ```
        # note
        - かならず音声の最初から最後までトランスクリプションを行ってください。
        - 無駄な空白を挿入せず自然な文章としてトランスクリプションを行ってください。
        """.strip()
    )

    model = GenerativeModel(
        model_name=GeminiConfig.GEMINI_MODEL_NAME,
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=GeminiConfig.TEMPERATURE,
            response_mime_type="application/json",
            response_schema=TranscriptionModel.to_response_schema(),
        ),
    )

    parts = [
        Part.from_uri(audio_gcs_path, mime_type="audio/mp3"),
        Part.from_text("これが文字起こしをしてほしい音声ファイルです。"),
    ]

    return _validate(model, parts, TranscriptionModel, 0, 5).clean_text()


def process_agenda(
    transcription: TranscriptionModel, agenda: AgendaModel
) -> AgendaModel:
    """
    トランスクリプトとアジェンダに基づいて議事録を生成する。

    Args:
        transcription (TranscriptionModel): 音声のトランスクリプト。
        agenda (AgendaModel): 会議のアジェンダ。

    Returns:
        AgendaModel: 更新されたアジェンダと議事録。
    """
    system_prompt = textwrap.dedent(
        f"""
        # role
        あなたは優秀なミーティングの議事録作成者です。

        # task
        会議中5~10分おきに作成されるトランスクリプトと、事前に作成されたアジェンダ+議事録を参考に、アジェンダに紐づいている議事録を作成してください。
        このタスクは繰り返し実行されるため、アジェンダ+議事録の入出力の形式は同一である必要があります。

        # input
        1. 今回のインターバル分のトランスクリプト
        ```
        {transcription.to_response_schema_str()}
        ```

        2. 事前に作成されたアジェンダ+これまでの議事録
        ```
        {agenda.to_response_schema_str()}
        ```

        # output
        今回のインターバル分のトランスクリプトを反映したアジェンダ+議事録を作成し、入力されたアジェンダ+議事録を更新してください。
        """
    )

    model = GenerativeModel(
        model_name=GeminiConfig.GEMINI_MODEL_NAME,
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=GeminiConfig.TEMPERATURE,
            response_mime_type="application/json",
            response_schema=AgendaModel.to_response_schema(),
        ),
    )

    parts = [
        Part.from_text("これがトランスクリプトです。"),
        Part.from_text(transcription.model_dump_json()),
        Part.from_text("これがアジェンダ+議事録です。"),
        Part.from_text(agenda.model_dump_json()),
        Part.from_text(
            "それではトランスクリプトを反映したアジェンダ+議事録を作成してください。"
        ),
    ]

    return _validate(model, parts, AgendaModel, 0, GeminiConfig.MAX_RETRIES)


async def process_agenda_by_item(
    transcription: TranscriptionModel, agenda: AgendaItemModel
) -> AgendaItemModel:
    """
    トランスクリプトとアジェンダに基づいて議事録を生成する。
    この関数は並列実行可能です。

    Args:
        transcription (TranscriptionModel): 音声のトランスクリプト。
        agenda (AgendaItemModel): アジェンダの1項目。

    Returns:
        AgendaItemModel: 更新されたアジェンダ項目と議事録。
    """
    system_prompt = textwrap.dedent(
        f"""
        # role
        あなたは優秀なミーティングの議事録作成者です。

        # task
        会議中5~10分おきに作成されるトランスクリプトと、事前に作成されたアジェンダ+議事録を参考に、アジェンダに紐づいている議事録を作成してください。
        このタスクは繰り返し実行されるため、アジェンダ+議事録の入出力の形式は同一である必要があります。
        また、このタスクは並列で実行されるため、あなたに渡されるのはアジェンダのうちの1項目とそれに紐づいた議事録です。

        # input
        1. 今回のインターバル分のトランスクリプト
        ```
        {transcription.to_response_schema_str()}
        ```

        2. 事前に作成されたアジェンダ+これまでの議事録
        ```
        {agenda.to_response_schema_str()}
        ```

        # output
        今回のインターバル分のトランスクリプトを反映したアジェンダ+議事録を作成し、入力されたアジェンダ+議事録を更新してください。

        # note
        - minutesは、トランスクリプトに基づき作成される議事録です。内容を整理し、マークダウン形式で詳細に記入してください。
        - 各goalsのresultは、条件が達成された場合のみ記入してください。されていない場合はnullのままにしてください。
        """
    )

    model = GenerativeModel(
        model_name=GeminiConfig.GEMINI_MODEL_NAME,
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=GeminiConfig.TEMPERATURE,
            response_mime_type="application/json",
            response_schema=AgendaItemModel.to_response_schema(),
        ),
    )

    parts = [
        Part.from_text("これがトランスクリプトです。"),
        Part.from_text(transcription.model_dump_json()),
        Part.from_text("これがアジェンダ+議事録の1項目です。"),
        Part.from_text(agenda.model_dump_json()),
        Part.from_text(
            "それではトランスクリプトを反映したアジェンダ+議事録を作成してください。"
        ),
    ]

    agenda_item = _validate(model, parts, AgendaItemModel, 0, GeminiConfig.MAX_RETRIES)
    logger.info(f"processed agenda item: {agenda_item.agenda}")
    return agenda_item


def process_hand_over(
    transcription: TranscriptionModel,
    previous_agenda: AgendaModel,
    post_agenda: AgendaModel,
) -> HandOverModel:
    """
    トランスクリプトと前回のアジェンダ+議事録と今回のアジェンダ+議事録に基づいて、次回インターバルに引き継ぐべき情報を提案する。
    """
    system_prompt = textwrap.dedent(
        """
        # role
        あなたは優秀なミーティングのファシリテーターです。

        # task
        トランスクリプトと前回のアジェンダ+議事録と今回のアジェンダ+議事録に基づいて、次回インターバルに引き継ぐべき情報を提案してください。
        次回インターバルに引き継ぐべき情報は以下の3つです。
        - 今回までのインターバルで話し合われた内容
        - 次回以降のインターバルで話し合われるべき内容
        """
    )

    model = GenerativeModel(
        model_name=GeminiConfig.GEMINI_MODEL_NAME,
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=GeminiConfig.TEMPERATURE,
            response_mime_type="application/json",
            response_schema=HandOverModel.to_response_schema(),
        ),
    )

    parts = [
        Part.from_text("これがトランスクリプトです。"),
        Part.from_text(transcription.model_dump_json()),
        Part.from_text("これが前回のアジェンダ+議事録です。"),
        Part.from_text(previous_agenda.model_dump_json()),
        Part.from_text("これが今回のアジェンダ+議事録です。"),
        Part.from_text(post_agenda.model_dump_json()),
        Part.from_text("次回インターバルに引き継ぐべき情報を提案してください。"),
    ]

    return _validate(model, parts, HandOverModel, 0, GeminiConfig.MAX_RETRIES)


def process_suggest_actions(
    template_action: TemplateAction, agenda: AgendaModel
) -> SuggestActionModel:
    """
    アジェンダに基づいてアクションを提案する。

    Args:
        template_action (TemplateAction): アクションテンプレート。
        agenda (AgendaModel): 会議のアジェンダ。

    Returns:
        SuggestActionModel: 提案されたアクション。
    """
    system_prompt = textwrap.dedent(
        f"""
        # role
        あなたは優秀なミーティングのファシリテーターです。

        # task
        与えられたアジェンダ+議事録に基づき、アクションテンプレートに沿ったアクションを提案してください。

        # input
        1. アジェンダ+議事録
        会議に関する情報を含むアジェンダ+議事録です。アクションテンプレートに沿ったアクションを提案するために必要な情報が含まれています。
        注意：description内の[タスク]は別タスクで使用される情報なので無視してください。

        ```
        {agenda.to_response_schema_str()}
        ```

        2. アクションテンプレート
        下記のいずれかが入力されるので、それに沿ったアクションを提案してください。
        ```
        {TemplateActionsModel.resolve().actions}
        ```

        # output
        アクションテンプレートに沿ったアクションを提案してください。
        出力形式は以下のresponse_schemaに沿ってください。
        
        ```
        {SuggestActionModel.to_response_schema_str()}
        ```
        """.strip()
    )

    model = GenerativeModel(
        model_name=GeminiConfig.GEMINI_MODEL_NAME,
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=GeminiConfig.TEMPERATURE,
            response_mime_type="application/json",
            response_schema=SuggestActionModel.to_response_schema(),
        ),
    )

    parts = [
        Part.from_text("これがアジェンダ+議事録です。"),
        Part.from_text(agenda.model_dump_json()),
        Part.from_text(f"これがアクションテンプレートです：{template_action}"),
        Part.from_text(
            "それではアジェンダの更新におけるアクションを提案してください。"
        ),
    ]

    return _validate(model, parts, SuggestActionModel, 0, GeminiConfig.MAX_RETRIES)
