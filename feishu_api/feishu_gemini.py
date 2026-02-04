import http.server
import socketserver
import json
from typing import Any
import hashlib
import base64
from dotenv import load_dotenv
import os
import sys
import os
# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import subprocess
from feishu_api.log_utils import log_print
from feishu_api.feishu_token_manager import FeishuTokenManager
from feishu_api.feishu_send_message import FeishuMessenger
from feishu_api.aes_cipher import AESCipher
import concurrent.futures
import time
import threading
import csv
from lark_oapi.api.im.v1 import *
import lark_oapi as lark

import dashscope

from dashscope.audio.asr import *
# http://183.62.227.229:60501/event

# load_dotenv()
# app_id = os.environ.get('APP_ID')
# app_secret = os.environ.get('APP_SECRET')
# token_manager = FeishuTokenManager(app_id, app_secret)
# api_key = os.environ.get('GOOGLE_API_KEY')  # Replace with your API key
# google_ai = GoogleGenerativeAI(api_key)
# rag_flow = RagflowGenerativeAI()
# messenger = FeishuMessenger(token_manager.get_token())
# processed_message_ids = {"1"}
# filter_history_message = {}


def copyQuestionToCsv(chat_id, question, response, file_name):
    with open(file=file_name, mode='a', encoding='utf-8') as f:
        writer = csv.writer(f)
        data = ["user", f"{chat_id}", "question",
                f"{question}", "response", f"{response}"]
        writer.writerow(data)


def getFeishuApiToken():
    app_id = os.environ.get('APP_ID')
    app_secret = os.environ.get('APP_SECRET')
    token_manager = FeishuTokenManager(app_id, app_secret)
    massage = FeishuMessenger(token_manager.get_token())
    return massage


def get_string_before_colon(text: str):
    """截取冒号之前的字符串"""
    parts = text.split(":")
    if len(parts) > 0 and parts[0] != text:
        return parts[0]
    else:
        parts = text.split("：")
        if len(parts) > 0:
            return parts[0]
        return text  # 如果没有冒号，则返回原始字符串


def get_string_after_colon(text: str):
    """截取冒号之前的字符串"""
    parts = text.split(":")
    if len(parts) > 0 and parts[0] != text:
        return parts[1]
    else:
        parts = text.split("：")
        if len(parts) > 0:
            return parts[1]
        return text  # 如果没有冒号，则返回原始字符串


class GlobalValues:
    def __init__(self):
        load_dotenv()
        self.messenger = getFeishuApiToken()
        self.processed_message_ids = {"1"}
        self.filter_history_message = {}
        
        # 配置信息
        app_id = os.environ.get('APP_ID')
        app_secret = os.environ.get('APP_SECRET')
        dashscope.api_key = os.environ.get('DASHSCOPE_API_KEY')
        
        # 机器人大脑配置
        self.brain_ip = os.environ.get("BRAIN_IP", "10.10.68.49")
        self.brain_port = os.environ.get("BRAIN_PORT", "8766")
        self.brain_url = f"http://{self.brain_ip}:{self.brain_port}"
        
        self.recognition = Recognition(
            model='paraformer-realtime-v2',
            format='opus',
            sample_rate=16000,
            callback=None,
        )
        self.client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.DEBUG) \
            .build()


global_values = GlobalValues()
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

def speech_to_text(file_path):
    global_values.recognition = Recognition(
            model='paraformer-realtime-v2',
            format='opus',
            sample_rate=16000,
            callback=None,
        )
    sentence_list = None
    while sentence_list is None:
        result = global_values.recognition.call(file_path)
        sentence_list = result.get_sentence()
        response_text = ""
        if sentence_list is None:
            log_print('No result')
            time.sleep(1)
            log_print(result)
    log_print('The brief result is:  ')
    response_text = ""
    for sentence in sentence_list:
        log_print(sentence['text'])
        response_text += sentence['text'] + "\n"
    log_print(
        '[Metric] requestId: {}, first package delay ms: {}, last package delay ms: {}'
        .format(
            global_values.recognition.get_last_request_id(),
            global_values.recognition.get_first_package_delay(),
            global_values.recognition.get_last_package_delay(),
        ))
    return response_text
class MyHttpRequestHandler(http.server.SimpleHTTPRequestHandler):
    def send_end_response(self, response):
        if response:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(bytes(json.dumps(response), "utf8"))

    def do_POST(self):
        log_print("info:", self.path)
        
        # 【新增】处理来自大脑的语音推送回调
        if self.path == '/voice/callback':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            log_print(f"🎤 收到大脑推送: {post_data}")
            self.handle_voice_callback(post_data)
            # 响应 200 OK
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"code":0, "msg":"ok"}')
            return

        if self.path == '/card':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            log_print(f"Server started at localhost:{post_data}")
            self.handle_card_action(post_data)

        if self.path == '/event':
            app_id = os.environ.get('APP_ID')
            app_secret = os.environ.get('APP_SECRET')
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            log_print(f"Server started at localhost:{post_data}")
            response = self.handle_event_action(post_data)
            if response:
                self.send_end_response(response)
    
    def handle_voice_callback(self, data):
        try:
            payload = json.loads(data)
            # 提取关键字段
            session_id = payload.get("session_id") # 对应 chat_id
            request_id = payload.get("request_id") # 对应 msg_id (如果透传成功)
            speak_text = payload.get("speak_text")
            event_type = payload.get("event_type")
            
            if speak_text:
                # 加上前缀以区分不同类型的消息（可选）
                prefix = ""
                if event_type == "fault": prefix = "⚠️ "
                elif event_type == "completed": prefix = "✅ "
                elif event_type == "failed": prefix = "❌ "
                
                final_text = f"{prefix}{speak_text}"
                log_print(f"📤 转发到飞书: {final_text}")
                
                # 尝试使用 request_id (如果是 message_id) 回复，或者直接发给 session_id (chat_id)
                # 由于 getFeishuApiToken().send_message 是 reply 接口，最好用 message_id
                target_id = request_id if request_id else session_id
                
                if target_id:
                    getFeishuApiToken().send_message(target_id, final_text) 
                else:
                    log_print("⚠️ 回调缺少 ID，无法发送飞书")
                
        except Exception as e:
            log_print(f"处理回调失败: {e}")
    
    def on_ai_tool_callback(self,message_id,response):
        res = getFeishuApiToken().send_message(message_id, response)

    def multiThreadHandleQuestion(self, chat_id, message_id, parsed_data):
        import functools
        msg_type = parsed_data['event']["message"]["message_type"]
        content_text = ""
        log_print("msg_type:", msg_type)
        if msg_type == 'audio' or msg_type == 'file':
            file_key = json.loads(
                parsed_data['event']['message']['content'])['file_key']
            log_print("file_key:", file_key, "messageid", message_id)
            request: GetMessageResourceRequest = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(file_key) \
                .type("file") \
                .build()
            response: GetMessageResourceResponse = global_values.client.im.v1.message_resource.get(
                request)
            log_print("get response", msg_type)
            if not response.success():
                log_print("get response failed!!!", msg_type)
                lark.logger.error(
                    f"client.im.v1.file.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
                response = getFeishuApiToken().send_message(message_id, "语音获取失败！请重新输入！")
                return
            if msg_type == 'audio':
                with open("audio_file.opus", "wb") as file:
                    log_print("write file")
                    file.write(response.file.read())
                log_print("write file success")
                content_text = speech_to_text("audio_file.opus")
                if content_text == "":
                    response = getFeishuApiToken().send_message(message_id, "语音获取失败！请重新输入！")
                    self.send_end_response(response)
            elif msg_type == 'file':
                log_print("file name:", response.file_name)
                file_path = "/home/lfc/web/log_analyzer/feishu_log/" + response.file_name
                with open(file_path, "wb") as file:
                    file.write(response.file.read())
                    file.close()
                    log_print("file write success")
                return
        elif msg_type == 'text':
            content_text = json.loads(
                parsed_data['event']['message']['content'])['text']
            log_print("Message ID:", message_id)
            log_print("content_text:", content_text)
        else:
            log_print("不支持的消息类型")
            response = getFeishuApiToken().send_message(message_id, "不支持的消息类型")
            self.send_end_response(response)
            return
        # 移除 Gemini 相关逻辑，重定向到语音服务
        try:
            # 【中转架构】飞书消息 -> 大脑 forward -> 语音模块 inject_stream
            brain_forward_url = f"{global_values.brain_url}/v1/voice/forward"
            log_print(f"🔄 [{message_id}] 指令发往大脑中转: {brain_forward_url}")
            
            payload = {
                "text": content_text,
                "session_id": chat_id,
                "msg_id": message_id
            }
            
            resp = requests.post(brain_forward_url, json=payload, timeout=5)
            
            if resp.status_code == 200:
                log_print(f"✅ [{message_id}] 大脑已接收并开始中转任务")
            else:
                log_print(f"⚠️ [{message_id}] 大脑中转异常: {resp.status_code}")
                getFeishuApiToken().send_message(message_id, "⚠️ 机器人大脑处理指令失败，请重试。")
            
        except Exception as e:
            log_print(f"❌ [{message_id}] 无法连接大脑: {e}")
            getFeishuApiToken().send_message(message_id, "⚠️ 无法连接到机器人大脑，请检查网络。")
            
        finally:
            log_print(f"🏁 [{message_id}] 线程处理流程结束")
    def getGeminiReponse(self, parsed_data):
        event_type = parsed_data['header']['event_type']
        if event_type == 'im.message.receive_v1':
            message_id = parsed_data['event']['message']['message_id']
            chat_id = parsed_data['event']['message']['chat_id']
            time_stamp = parsed_data['event']["message"]["create_time"]
            msg_type = parsed_data['event']["message"]["message_type"]
            time_stamp_int = int(time_stamp)
            if message_id in global_values.processed_message_ids:
                log_print(f"消息为重复消息已处理，message_id: {message_id}")
                return {"code": 0, "msg": "ok"}
            if (chat_id in global_values.filter_history_message):
                if (time_stamp_int < global_values.filter_history_message[chat_id]):
                    log_print(f"消息为历史消息，不处理，message_id: {message_id}")
                    return {"code": 0, "msg": "ok"}
            global_values.processed_message_ids.add(message_id)
            log_print("msg_type:", msg_type)
            if msg_type == 'audio' or msg_type == 'text' or msg_type == 'file':
                log_print("msg_type:", msg_type)
                global_values.filter_history_message[chat_id] = time_stamp_int
                executor.submit(self.multiThreadHandleQuestion, chat_id,
                                message_id, parsed_data)
            return {"code": 0, "msg": "ok"}

        else:
            log_print("收到未知类型的事件，忽略")
            return {"code": 0, "msg": "unknown event"}

    def handle_card_action(self, data):
        parsed_data = json.loads(data)
        encrypt = parsed_data.get('encrypt', '')
        cipher = AESCipher(os.environ.get('ENCRYPT_KEY'))
        parsed_data = json.loads(cipher.decrypt_string(encrypt))
        log_print("parse_data", parsed_data)
        if parsed_data.get('challenge', ''):
            response = {"challenge": parsed_data.get('challenge', '')}
            self.send_end_response(response)
        else:
            self.getGeminiReponse(parsed_data)

    def handle_event_action(self, data):
        parsed_data = json.loads(data)
        encrypt = parsed_data.get('encrypt', '')
        cipher = AESCipher(os.environ.get('ENCRYPT_KEY'))
        parsed_data = json.loads(cipher.decrypt_string(encrypt))
        log_print(f"parsed_data:{parsed_data}")
        
        # 立即返回 challenge (如果存在)
        if parsed_data.get('challenge', ''):
            return {"challenge": parsed_data.get('challenge', '')}
        
        # 针对消息事件，立即异步处理并返回 200 OK，防止飞书重试
        self.getGeminiReponse(parsed_data)
        return {"code": 0, "msg": "ok"}


PORT = 60502


def setup_ssh_tunnel():
    """自动化建立通往机器人的 SSH 隧道"""
    target = "10.10.70.218"
    mapping = "8866:localhost:8800"
    log_print(f"📡 正在建立隧道: {mapping} -> {target}")
    try:
        # 清理旧连接并启动后台隧道
        subprocess.run(["pkill", "-f", f"{mapping}.*{target}"], capture_output=True)
        ssh_cmd = [
            "ssh", "-o", "ServerAliveInterval=15", "-o", "ConnectTimeout=10",
            "-f", "-N", "-L", mapping, "-p", "2222", f"root@{target}"
        ]
        subprocess.run(ssh_cmd, check=True)
        log_print("✅ 隧道建立成功")
    except Exception as e:
        log_print(f"⚠️ 隧道自动建立失败: {e}")

def start_feishu_server():
    # setup_ssh_tunnel()  # 新架构下，飞书端不需要直接建立隧道，由大脑端统一管理
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), MyHttpRequestHandler) as httpd:
        log_print(f"Server started at localhost:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    start_feishu_server()
