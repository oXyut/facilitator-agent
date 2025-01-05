import textwrap
import time
from typing import List, Type

from logging_config import setup_logger
from models import AgendaModel, TranscriptionModel
from pydantic import BaseModel
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part

logger = setup_logger(__name__)


def exponential_backoff(retries: int):
    time.sleep(2**retries)


def __validate(
    model: GenerativeModel,
    parts: List[Part],
    schema_cls: Type[BaseModel],
    retries: int,
    max_retries: 5,
):
    try:
        response = model.generate_content(parts)
        parsed_response = schema_cls.model_validate_json(response.text)
        return parsed_response
    except Exception as e:
        logger.error(f"{retries}th attempt failed: {e}")
        if retries < max_retries:
            exponential_backoff(retries)
            return __validate(model, parts, schema_cls, retries + 1, max_retries)
        raise e


def process_transcript(audio_gcs_path: str) -> TranscriptionModel:
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
        model_name="gemini-2.0-flash-exp",
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=TranscriptionModel.to_response_schema(),
        ),
    )

    parts = [
        Part.from_uri(audio_gcs_path, mime_type="audio/mp3"),
        Part.from_text("これが文字起こしをしてほしい音声ファイルです。"),
    ]

    return __validate(model, parts, TranscriptionModel, 0, 5).clean_text()


def process_agenda(
    transcription: TranscriptionModel, agenda: AgendaModel
) -> AgendaModel:
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
        model_name="gemini-2.0-flash-exp",
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            temperature=0.0,
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

    return __validate(model, parts, AgendaModel, 0, 5)
