# logging_config.py
import logging
import sys


def setup_logger(name):
    # ロガーの取得
    logger = logging.getLogger(name)
    if not logger.hasHandlers():  # 重複設定を防止
        logger.setLevel(logging.DEBUG)  # 基本のログレベルを設定
        # ストリームハンドラーの作成
        stream_handler = logging.StreamHandler(sys.stdout)
        # フォーマットの設定
        formatter = logging.Formatter(
            "[%(levelname)-8s][%(asctime)s][%(filename)s:%(lineno)d %(funcName)s]: %(message)s"
        )
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger
