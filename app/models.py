import json
import textwrap
from enum import Enum
from typing import List, Optional

import jsonref
from pydantic import BaseModel, Field


def parse_json_schema(schema: dict) -> dict:
    """
    JSON Schemaから不要なキーを削除し、参照を解決する。

    Args:
        schema (dict): JSON Schemaの辞書。

    Returns:
        dict: 不要なキーが削除され、参照が解決されたJSON Schemaの辞書。
    """

    def __remove_key_recursive(d, key_to_remove):
        if isinstance(d, dict):
            return {
                k: __remove_key_recursive(v, key_to_remove)
                for k, v in d.items()
                if k != key_to_remove
            }
        elif isinstance(d, list):
            return [__remove_key_recursive(item, key_to_remove) for item in d]
        else:
            return d

    def __remove_allOf(schema):
        if isinstance(schema, dict):
            # Check if 'allOf' exists and has only one item
            if "allOf" in schema and len(schema["allOf"]) == 1:
                # Replace the dict with the first item in 'allOf'
                schema.update(schema.pop("allOf")[0])

            # Recursively process all properties
            for key, value in schema.items():
                __remove_allOf(value)

        elif isinstance(schema, list):
            # Process each item in the list
            for item in schema:
                __remove_allOf(item)

        return schema

    def __remove_anyOf(schema):
        if isinstance(schema, dict):
            if "anyOf" in schema:
                anyOf_items = schema.pop("anyOf")
                schema.update(anyOf_items[0])
            for key, value in schema.items():
                __remove_anyOf(value)
        elif isinstance(schema, list):
            for item in schema:
                __remove_anyOf(item)

        return schema

    def __remove_pattern_properties(schema):
        if isinstance(schema, dict):
            if "pattern" in schema:
                schema.pop("pattern")
            for key, value in schema.items():
                __remove_pattern_properties(value)
        elif isinstance(schema, list):
            for item in schema:
                __remove_pattern_properties(item)
        return schema

    schema = jsonref.JsonRef.replace_refs(schema)
    # titleの削除は必須ではないがresponse_schemaのexampleにないため削除
    schema = __remove_key_recursive(schema, "title")
    schema = __remove_allOf(schema)
    schema = __remove_anyOf(schema)
    schema = __remove_pattern_properties(schema)
    schema = {k: v for k, v in schema.items() if k != "$defs"}
    return schema


class CustomBaseModel(BaseModel):
    class Config:
        populate_by_name = True

    @classmethod
    def to_response_schema(cls) -> dict:
        """
        Pydanticモデルからレスポンス用のJSON Schemaを生成する。

        Returns:
            dict: レスポンス用のJSON Schemaの辞書。
        """
        return parse_json_schema(cls.model_json_schema())

    @classmethod
    def to_response_schema_str(cls) -> str:
        """
        Pydanticモデルからレスポンス用のJSON Schemaを文字列として生成する。

        Returns:
            str: レスポンス用のJSON Schemaの文字列。
        """
        return json.dumps(cls.to_response_schema(), ensure_ascii=False, indent=2)


class CommentsModel(CustomBaseModel):
    start_sec: float = Field(..., description="開始時間（秒）")
    end_sec: float = Field(..., description="終了時間（秒）")
    speaker_id: str = Field(..., description="発話者ID")
    text: str = Field(..., description="テキスト")

    def clean_text(self) -> "CommentsModel":
        """
        テキスト内の不要な空白を削除する。

        Returns:
            CommentsModel: 空白が削除されたCommentsModelのインスタンス。
        """
        # remove all spaces
        return CommentsModel(
            start_sec=self.start_sec,
            end_sec=self.end_sec,
            speaker_id=self.speaker_id,
            text=self.text.replace(" ", ""),
        )


class TranscriptionModel(CustomBaseModel):
    comments: list[CommentsModel] = Field(..., description="音声データのコメント")

    def clean_text(self) -> "TranscriptionModel":
        """
        TranscriptionModel内の各コメントのテキストから不要な空白を削除する。

        Returns:
            TranscriptionModel: 空白が削除されたTranscriptionModelのインスタンス。
        """
        return TranscriptionModel(
            comments=[comment.clean_text() for comment in self.comments]
        )


class MeetingStatus(str, Enum):
    NOT_STARTED = "未開始"
    IN_PROGRESS = "進行中"
    COMPLETED = "完了"


class AgendaGoalModel(CustomBaseModel):
    done: bool = Field(
        False,
        description=textwrap.dedent(
            """
            [内容]
            該当アジェンダで話し合うべき目標の達成状況で、以下のいずれかです。
            - True: 達成済み
            - False: 未達成

            [タスク]
            - 与えられたトランスクリプションや該当アジェンダのminutesを参考に、該当アジェンダで話し合うべき目標が達成されているかどうかを判断してください。
            """.strip()
        ),
    )
    condition: str = Field(
        ...,
        description=textwrap.dedent(
            """
            [内容]
            - 該当アジェンダで話し合うべき目標の達成条件です。

            [タスク]
            - プロンプトとして与えられるのでコピーしてください。
            - このconditionに基づいてstatusやresultを更新してください。
            """.strip()
        ),
    )
    result: Optional[str] = Field(
        None,
        description=textwrap.dedent(
            """
            [内容]
            - 該当アジェンダで話し合うべき目標について、実際に話し合われた内容です。

            [タスク]
            - 与えられたアジェンダの該当resultが既にTrueだった場合：
                - 既にresultが記入済みです。内容をそのままコピーしてください。
            - 与えられたアジェンダの該当resultがFalseだった場合：
                - 今回のトランスクリプションでも話し合われていなければnullのままにしてください。
                - 今回のトランスクリプションで話し合われていた場合は、話し合われた内容を記入してください。
            """.strip()
        ),
    )


class AgendaItemModel(CustomBaseModel):
    agenda: str = Field(
        ...,
        description=textwrap.dedent(
            """
            [内容]
            - 該当アジェンダについて簡潔に説明したものです。
            - 該当アジェンダに沿って会議が進行します。

            [タスク]
            プロンプトとして与えられるのでコピーしてください。
            このagendaに基づいてminutesやstatusを更新してください。
            """.strip()
        ),
    )
    minutes: Optional[str] = Field(
        None,
        description=textwrap.dedent(
            """
            [内容]
            - トランスクリプションに基づき作成される議事録です。
            - このminutesに基づいてstatusを更新してください。

            [タスク]
            - トランスクリプションの内容が、該当agendaの内容かどうかを判断してください。
            - 該当agendaの内容であれば、内容を議事録として整理し記入してください。

            [注意]
            - プロンプトとして与えられた該当アジェンダのstatusが既にCOMPLETEDであれば、minutesは既に作成済みです。内容をそのままコピーしてください。
            - プロンプトとして与えられた該当アジェンダのstatusがIN_PROGRESSであれば、minutesは記入されているものの追記が必要な可能性があります。
            """.strip()
        ),
    )
    status: MeetingStatus = Field(
        MeetingStatus.NOT_STARTED,
        description=textwrap.dedent(
            """
            [内容]
            該当アジェンダのステータスで、以下のいずれかです。
            - NOT_STARTED: 未開始
            - IN_PROGRESS: 進行中
            - COMPLETED: 完了

            [タスク]
            - 与えられたアジェンダがNOT_STARTEDの場合：
                - 該当アジェンダのminutesに基づき、該当アジェンダがまだ話されていない場合はNOT_STARTEDのままに、話されている場合はIN_PROGRESSに、完了している場合はCOMPLETEDに更新してください。
            - 与えられたアジェンダがIN_PROGRESSの場合：
                - 該当アジェンダのminutesに基づき、該当アジェンダが進行中であればIN_PROGRESSのままに、完了していればCOMPLETEDに更新してください。
            - 与えられたアジェンダがCOMPLETEDの場合：
                - COMPLETEDのままにしてください。
            """.strip()
        ),
    )
    goals: list[AgendaGoalModel] = Field(
        [],
        description=textwrap.dedent(
            """
            [内容]
            - 該当アジェンダで達成すべき目標のリストです。
            """.strip()
        ),
    )


class AgendaModel(CustomBaseModel):
    items: List[AgendaItemModel] = Field(
        [],
        description=textwrap.dedent(
            """
            アジェンダのアイテムのリストです。
            """.strip()
        ),
    )

    hand_over: Optional[str] = Field(
        None,
        alias="handOver",
        description=textwrap.dedent(
            """
            [内容]
            - 次インターバルのアジェンダ更新に引き継ぎたい内容です。

            [タスク]
            - アジェンダがどこまで進行したか、何が話されて何が話されていないか、議事録を作成する上で重要な情報や気をつけるべき情報を記入してください。
            """.strip()
        ),
    )


class TemplateAction(str, Enum):
    HIGHLIGHT_UNRESOLVED_POINTS = "議論しきれていない部分を指摘する"
    SUGGEST_RELATED_IDEAS = "関連するアイデアを挙げる"
    RAISE_OFF_AGENDA_TOPICS = "アジェンダ外で話すべきことを挙げる"


class TemplateActionsModel(CustomBaseModel):
    actions: list[TemplateAction] = Field(
        [],
        description="アジェンダの更新におけるアクションのリスト",
    )

    @classmethod
    def resolve(cls) -> "TemplateActionsModel":
        """
        利用可能な全てのアクションテンプレートを返す。

        Returns:
            TemplateActionsModel: 全てのアクションテンプレートを含むTemplateActionsModelのインスタンス。
        """
        return cls(actions=[action for action in TemplateAction])


class SuggestActionModel(CustomBaseModel):
    template_action: TemplateAction = Field(
        ..., alias="templateAction", description="アクションのテンプレート"
    )
    suggested_action: str = Field(
        ..., alias="suggestedAction", description="提案されたアクション"
    )
