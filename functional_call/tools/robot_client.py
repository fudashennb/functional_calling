from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

from src.sr_modbus_sdk import SRModbusSdk
from src.sr_modbus_model import MovementState, MovementResult, ActionState, ActionResult


logger = logging.getLogger(__name__)


EventEmitter = Callable[[str, dict | None], None]


class RobotClient:
    """
    单机器人客户端（线程安全：每次Modbus交互用锁保护）。

    说明：
    - 只实现当前语音控制必需的指令/查询
    - 进度播报通过 emit(type, data) 由上层转成 VoiceEvent
    """

    def __init__(self, host: str, port: int) -> None:
        self._lock = threading.RLock()
        self._sdk = SRModbusSdk()
        self._host = host
        self._port = port
        self._sdk.connect_tcp(host, port)
        self._task_no = random.randint(1, 10000)

    def _next_task_no(self) -> int:
        self._task_no += 1
        return self._task_no

    def _retry_on_modbus_error(self, func: Callable, *args, max_retries: int = 3, **kwargs):
        """
        Modbus 通信容错重试装饰器逻辑。
        针对 'Invalid Message'、'No response' 或 'SlaveFailure' 等临时性错误进行重试。
        """
        last_exception = None
        for i in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                err_msg = str(e)
                # 识别常见的 Modbus 通信错误及从站忙碌错误 (SlaveFailure)
                is_transient = any(kw in err_msg for kw in ["Modbus Error", "ConnectionError", "SlaveFailure", "0 received"])
                
                if is_transient:
                    last_exception = e
                    if i < max_retries - 1:
                        logger.warning(f"⚠️ Modbus 通信抖动/从站忙碌，正在重试 ({i+1}/{max_retries}): {err_msg}")
                        time.sleep(0.5 * (i + 1))  # 递增等待时间
                        continue
                raise e # 非通信错误或重试耗尽，直接抛出
        if last_exception:
            raise last_exception

    # ------------------ 查询类 ------------------
    def get_battery_info(self) -> dict:
        def _inner():
            with self._lock:
                b = self._sdk.get_battery_info()
            return {
                "percentage_electricity": b.percentage_electricity,
                "temperature": b.temperature,
                "state": str(b.state),
                "voltage": b.voltage,
                "current": b.current,
                "nominal_capacity": b.nominal_capacity,
                "use_cycles": b.use_cycles,
            }
        return self._retry_on_modbus_error(_inner)

    def get_movement_task_info(self) -> dict:
        def _inner():
            with self._lock:
                t = self._sdk.get_movement_task_info()
            return {
                "state": str(t.state),
                "no": t.no,
                "target_station": t.target_station,
                "path_no": t.path_no,
                "result": str(t.result),
                "result_value": t.result_value,
            }
        return self._retry_on_modbus_error(_inner)

    def get_action_task_info(self) -> dict:
        def _inner():
            with self._lock:
                t = self._sdk.get_action_task_info()
            return {
                "state": str(t.state),
                "no": t.no,
                "id": t.id,
                "param0": t.param0,
                "param1": t.param1,
                "result": str(t.result),
                "result_value": t.result_value,
            }
        return self._retry_on_modbus_error(_inner)

    def is_charging(self) -> bool:
        def _inner():
            with self._lock:
                return bool(self._sdk.is_charge())
        return self._retry_on_modbus_error(_inner)

    # ------------------ 内部工具 ------------------
    def _poll_task_status(
        self,
        query_func: Callable,
        check_done_func: Callable[[Any], bool],
        *,
        timeout_s: int,
        emit: EventEmitter,
        task_name: str,
        task_no: int = 0,
        stop_event: threading.Event | None = None
    ) -> Any:
        """
        通用的状态轮询器：解耦业务逻辑与容错机制。
        """
        # 核心改进：在开始轮询前，先给 PLC 留出物理动作执行的“冷却期”
        time.sleep(0.8)
        
        start = time.time()
        last_progress_emit = 0.0
        
        while True:
            if stop_event and stop_event.is_set():
                logger.info(f"⏹️ {task_name}任务收到中断信号")
                self.cancel_current_task()
                raise InterruptedError(f"{task_name}任务已被取消")

            elapsed = int(time.time() - start)
            if elapsed >= timeout_s:
                raise TimeoutError(f"{task_name}超时（已等待{timeout_s}秒）")

            try:
                # 核心改进：在轮询内部增加即时重试，且支持 SlaveFailure 容错
                status = self._retry_on_modbus_error(query_func, max_retries=3)
                
                # 节流播报
                if time.time() - last_progress_emit >= 5:
                    last_progress_emit = time.time()
                    emit("progress", {
                        "text": f"{task_name}进行中，已等待 {elapsed} 秒，状态：{getattr(status, 'state', 'N/A')}。",
                        "elapsed_s": elapsed,
                        "status": str(status)
                    })

                # 检查是否完成
                if check_done_func(status):
                    return status

            except Exception as e:
                # 只有在多次重试都失败后才记录警告
                logger.warning(f"⚠️ {task_name}轮询中通信持续异常（已忽略）: {e}")

            time.sleep(1)

    # ------------------ 指令类（长耗时） ------------------
    def cancel_current_task(self) -> None:
        with self._lock:
            self._sdk.cancel_task()

    def move_to_station(
        self, 
        station_no: int, 
        *, 
        timeout_s: int = 120, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        
        # 1. 预检查与取消旧任务
        try:
            info = self.get_movement_task_info()
            if "MT_RUNNING" in info["state"]:
                self.cancel_current_task()
        except Exception: pass

        # 2. 下发指令
        task_no = self._next_task_no()
        emit("started", {"text": f"开始导航到站点 {station_no}（任务号 {task_no}）。"})
        self._retry_on_modbus_error(lambda: self._sdk.move_to_station_no(station_no, task_no))

        # 3. 使用通用轮询器
        def check_done(t):
            if t.state == MovementState.MT_FINISHED and (t.no == task_no or task_no == 0):
                if t.result == MovementResult.MT_TASK_ERROR:
                    raise RuntimeError(f"导航失败：错误码 {t.result_value}")
                return True
            return False

        self._poll_task_status(
            query_func=lambda: self._sdk.get_movement_task_info(),
            check_done_func=check_done,
            timeout_s=timeout_s,
            emit=emit,
            task_name="导航",
            task_no=task_no,
            stop_event=stop_event
        )
        emit("step_done", {"text": f"已到达站点 {station_no}。"})

    def execute_action(
        self,
        action_id: int,
        param1: int,
        param2: int,
        *,
        timeout_s: int = 60,
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        task_no = self._next_task_no()
        emit("started", {"text": f"开始执行动作 {action_id}（任务号 {task_no}）。"})
        self._retry_on_modbus_error(lambda: self._sdk.start_action_task_no(action_id, param1, param2, task_no))

        def check_done(t):
            if t.state == ActionState.AT_FINISHED and (t.no == task_no or task_no == 0):
                if t.result == ActionResult.AT_TASK_ERROR:
                    raise RuntimeError(f"动作失败：错误码 {t.result_value}")
                return True
            return False

        self._poll_task_status(
            query_func=lambda: self._sdk.get_action_task_info(),
            check_done_func=check_done,
            timeout_s=timeout_s,
            emit=emit,
            task_name="动作执行",
            task_no=task_no,
            stop_event=stop_event
        )
        emit("step_done", {"text": f"动作 {action_id} 执行完成。"})

    def start_charge(
        self, 
        *, 
        timeout_s: int = 40, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        
        if self.is_charging():
            emit("step_done", {"text": "机器人已在充电中。"})
            return
            
        emit("started", {"text": "开始启动充电。"})
        self._retry_on_modbus_error(lambda: self._sdk.charge())
        
        self._poll_task_status(
            query_func=lambda: self._sdk.is_charge(),
            check_done_func=lambda is_charging: bool(is_charging),
            timeout_s=timeout_s,
            emit=emit,
            task_name="充电启动",
            stop_event=stop_event
        )
        emit("step_done", {"text": "充电已启动。"})

    def stop_charge(
        self, 
        *, 
        timeout_s: int = 40, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        if not self.is_charging():
            emit("step_done", {"text": "机器人当前未在充电。"})
            return

        emit("started", {"text": "开始停止充电。"})
        self._retry_on_modbus_error(lambda: self._sdk.stop_charge())
        
        self._poll_task_status(
            query_func=lambda: self._sdk.is_charge(),
            check_done_func=lambda is_charging: not bool(is_charging),
            timeout_s=timeout_s,
            emit=emit,
            task_name="充电停止",
            stop_event=stop_event
        )
        emit("step_done", {"text": "充电已停止。"})

