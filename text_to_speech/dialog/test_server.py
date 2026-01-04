from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

# 创建FastAPI应用
app = FastAPI(title="对话服务器", description="接收查询并返回响应")

# 定义请求模型


class QueryRequest(BaseModel):
    env: Optional[str] = None
    query: str

# 定义响应模型


class QueryResponse(BaseModel):
    resultCode: str
    resultMsg: str


@app.post("/panguLogSvc/aiApiCall/callDataCenterForPost", response_model=QueryResponse)
async def process_query(request: QueryRequest):
    """
    处理查询请求

    Args:
        request: 包含env和query的请求体

    Returns:
        QueryResponse: 包含resultCode和resultMsg的响应
    """
    try:
        # 验证必要参数
        if not request.query:
            raise HTTPException(status_code=400, detail="query参数是必需的")

        # 无论什么查询都返回“我还在学习”
        response = QueryResponse(
            resultCode="1",  # 1表示失败
            resultMsg="不好意思，这个问题，我还在学习"
        )
        return response

    except Exception as e:
        # 异常处理
        return QueryResponse(
            resultCode="1",
            resultMsg="不好意思，这个问题，我还在学习"
        )


@app.get("/")
async def root():
    """根路径，返回服务信息"""
    return {"message": "对话服务器正在运行", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy"}

if __name__ == "__main__":
    # 启动服务器
    uvicorn.run(
        "test_server:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info"
    )
