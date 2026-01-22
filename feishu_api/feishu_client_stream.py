import importlib
import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.api.cardkit.v1 import *

# SDK 使用说明: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/preparations-before-development
# 以下示例代码默认根据文档示例值填充，如果存在代码问题，请在 API 调试台填上相关必要参数后再复制代码使用
# 复制该 Demo 后, 需要将 "YOUR_APP_ID", "YOUR_APP_SECRET" 替换为自己应用的 APP_ID, APP_SECRET.


class ReplyMessageByStream:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = lark.Client.builder().app_id(self.app_id).app_secret(
            self.app_secret).log_level(log_level=lark.LogLevel.DEBUG).build()
        self.element_id = 1

    def reply_message_by_card(self, message_id):
        card_id = self.create_card()
        print("creat card id", card_id)
        self.send_card_to_usr(message_id, card_id)
        print("sucessfuly to send card")
        return card_id

    def send_card_to_usr(self, message_id, card_id):
        # 构造请求对象

        # json_content = {"type": "card", "data": {"card_id": card_id}}
        json_content = {"type": "card", "data": {"card_id": card_id}}
        # content_send = json.dumps({
        #     "elements": [
        #         {
        #             "tag": "div",
        #             "text": {
        #                 "content": json.dumps(json_content),
        #                 "tag": "lark_md"
        #             }
        #         }
        #     ]
        # })
        print("content_send:", json.dumps(json_content))
    # 构造请求对象
        request: ReplyMessageRequest = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder()
                          .content(json.dumps(json_content))
                          .msg_type("interactive")
                          .reply_in_thread(False)
                          .build()) \
            .build()

    # 发起请求
        response: ReplyMessageResponse = self.client.im.v1.message.reply(
            request)
        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.im.v1.message.reply failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return
        # 处理业务结果
    def close_stream_card(self, card_id):
        settings_json = {
            "config": {
                "streaming_mode": False,
                "summary": {
                    "content": "[生成中]"
                },
                "streaming_config": {
                    "print_frequency_ms": {
                        "default": 20,
                        "android": 20,
                        "ios": 20,
                        "pc": 20
                    },
                    "print_step": {
                        "default": 10,
                        "android": 10,
                        "ios": 10,
                        "pc": 10
                    },
                    "print_strategy": "fast",
                }
            }
        }
        request: SettingsCardRequest = SettingsCardRequest.builder() \
            .card_id(card_id) \
            .request_body(SettingsCardRequestBody.builder()
                          #   .settings("{\"card_link\":{\"android_url\":\"https://open.feishu.cn\",\"ios_url\":\"https://open.feishu.cn\",\"pc_url\":\"https://open.feishu.cn\",\"url\":\"https://open.feishu.cn\"},\"config\":{\"enable_forward\":true,\"enable_forward_interaction\":false,\"streaming_mode\":true,\"update_multi\":true,\"width_mode\":\"fill\"}}")
                          .settings(json.dumps(settings_json))
                          .sequence(self.sequence)
                          .build()) \
            .build()
        # 发起请求
        response: SettingsCardResponse = self.client.cardkit.v1.card.settings(
            request)
        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.cardkit.v1.card.settings failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            print("!!!!!!failed to create card")
            return
        print("sucessfuly to create card")

    def create_card(self):
        request: CreateCardRequest = CreateCardRequest.builder() \
            .request_body(CreateCardRequestBody.builder()
                          .type("card_json")
                          .data("{\"schema\":\"2.0\",\"header\":{\"title\":{\"content\":\"AI回复\",\"tag\":\"plain_text\"}},\"config\":{\"streaming_mode\":true,\"summary\":{\"content\":\"[生成中]\"}},\"body\":{\"elements\":[{\"tag\":\"markdown\",\"content\":\"[生成中...]\",\"element_id\":\"markdown_1\"}]}}")
                          .build()) \
            .build()
        import time
        self.sequence = (int)(time.time())
        # 发起请求
        response: CreateCardResponse = self.client.cardkit.v1.card.create(
            request)
        card_id = response.data.card_id
        print("card id", card_id)
        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.cardkit.v1.card.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            print("!!!!!!failed to create card")
            return
            # 处理业务结果
    # 构造请求对象
        settings_json = {
            "config": {
                "streaming_mode": True,
                "summary": {
                    "content": "[生成中...]"
                },
                "streaming_config": {
                    "print_frequency_ms": {
                        "default": 20,
                        "android": 20,
                        "ios": 20,
                        "pc": 20
                    },
                    "print_step": {
                        "default": 10,
                        "android": 10,
                        "ios": 10,
                        "pc": 10
                    },
                    "print_strategy": "fast",
                }
            }
        }
        request: SettingsCardRequest = SettingsCardRequest.builder() \
            .card_id(card_id) \
            .request_body(SettingsCardRequestBody.builder()
                          #   .settings("{\"card_link\":{\"android_url\":\"https://open.feishu.cn\",\"ios_url\":\"https://open.feishu.cn\",\"pc_url\":\"https://open.feishu.cn\",\"url\":\"https://open.feishu.cn\"},\"config\":{\"enable_forward\":true,\"enable_forward_interaction\":false,\"streaming_mode\":true,\"update_multi\":true,\"width_mode\":\"fill\"}}")
                          .settings(json.dumps(settings_json))
                          .sequence(self.sequence)
                          .build()) \
            .build()
        # 发起请求
        response: SettingsCardResponse = self.client.cardkit.v1.card.settings(
            request)
        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.cardkit.v1.card.settings failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            print("!!!!!!failed to create card")
            return
        print("sucessfuly to create card")
        # 处理业务结果
        # lark.logger.info(lark.JSON.marshal(response.data, indent=4))
        print("carddddd id", card_id)
        return card_id

    def reply_by_stream(self, card_id, content):
        # 创建client
        self.element_id += 1
        self.sequence += 1
        # 构造请求对象
        import time
        request: ContentCardElementRequest = ContentCardElementRequest.builder() \
            .card_id(card_id) \
            .element_id("markdown_1") \
            .request_body(ContentCardElementRequestBody.builder()
                          .content(content)
                          .sequence(self.sequence)
                          .build()) \
            .build()
        # 发起请求
        response: ContentCardElementResponse = self.client.cardkit.v1.card_element.content(
            request)
        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.cardkit.v1.card_element.content failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        # 处理业务结果
