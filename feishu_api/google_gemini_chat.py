import os
from google_generative_ai import GoogleGenerativeAI

api_key = os.environ.get('GOOGLE_API_KEY')  # Replace with your API key
print("api_key", api_key)
api_key = "AIzaSyB72lYFYap_YMphwlLIi9etJS2XQmGfYwU"
print("api_key", api_key)
google_ai = GoogleGenerativeAI(api_key)
response = google_ai.generate_content_by_memory("chat_1", "hello world")
print(response)
response = google_ai.generate_content_by_memory("chat_1", "今天天气怎样")
print(response)
response = google_ai.generate_content_by_memory("chat_1", "我刚刚说啥了")
print(response)
response = google_ai.generate_content_by_memory("chat_1", "你还记得我说啥了吗")
print(response)
response = google_ai.generate_content_by_memory("chat_1", "你怎么看你自己")
print(response)
