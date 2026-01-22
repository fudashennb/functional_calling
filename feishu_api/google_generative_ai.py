from email import message
import google.generativeai as genai


class GoogleGenerativeAI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.model = None
        self.memory = {}
        self.configure()

    def configure(self):
        genai.configure(api_key=self.api_key)
        generation_config = {
            "temperature": 0.9,
            "top_p": 1,
            "top_k": 1,
            "max_output_tokens": 2048,
        }

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
        ]

        self.model = genai.GenerativeModel(model_name="gemini-2.0-flash-001",
                                           generation_config=generation_config,
                                           safety_settings=safety_settings)

    def generate_content(self, prompt_parts):
        response = self.model.generate_content(prompt_parts)
        return response.text

    def generate_content_by_memory(self, chat_id, prompt_parts):
        # >>> messages = [{'role':'user', 'parts': ['hello']}]
        # >>> response = model.generate_content(messages) # "Hello, how can I help"
        # >>> messages.append(response.candidates[0].content)
        # >>> messages.append({'role':'user', 'parts': ['How does quantum physics work?']})
        # >>> response = model.generate_content(messages)
        messages = {'role': 'user', 'parts': [prompt_parts]}
        print(messages)
        if chat_id not in self.memory:
            print("create")
            self.memory[chat_id] = []
        chat_memory = self.memory[chat_id]
        chat_memory.append(messages)
        print("generate_content")
        response = self.model.generate_content(chat_memory)
        print(response.text)
        chat_memory.append(response.candidates[0].content)
        if len(chat_memory) > 50:
            chat_memory[:] = chat_memory[-50:]  # 只保留最后25条记录
        return response.text
