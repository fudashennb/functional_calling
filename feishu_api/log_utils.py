import inspect
import sys
import os

def log_print(*args, **kwargs):
    """
    打印日志，包含文件名和行号
    """
    # 获取调用者的帧信息
    frame = inspect.currentframe().f_back
    # 获取文件名和行号
    filename = os.path.basename(frame.f_code.co_filename)
    line_no = frame.f_lineno
    
    # 构建日志前缀
    log_prefix = f"[{filename}:{line_no}]"
    
    # 打印日志
    print(log_prefix, *args, **kwargs) 